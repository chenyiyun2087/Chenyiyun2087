"""Master pipeline for STAR Market PE/PEG x Z-score x MIT Trader research."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .account_simulator import AccountSimulator
from .data_feed import StarDataFeed
from .mit_risk_engine import MitRiskEngine
from .valuation_service import ValuationService
from .validator import MitThreeGatesValidator, calc_sharpe, paired_block_bootstrap_ci
from .z_signal_service import ZSignalService


SHANGHAI = ZoneInfo("Asia/Shanghai")


def assess_prototype_window(z_series: np.ndarray, open_returns: np.ndarray, can_buy: np.ndarray, can_sell: np.ndarray, costs: tuple) -> dict:
    """Assess 504-day window for prototype ranking: >=3 closed trades, first half > 0, second half > 0."""
    buy_fee, sell_fee = costs
    entries = z_series < -2.0
    exits = z_series >= 0.0
    boundary = entries | exits
    offsets = np.arange(len(z_series))
    last_boundary = np.maximum.accumulate(np.where(boundary, offsets, -1))
    target = np.r_[False, entries][last_boundary + 1]

    holdings = False
    net = np.zeros(len(z_series))
    round_trips = 0

    for i in range(len(z_series)):
        mult = 1.0 + open_returns[i] if holdings else 1.0
        if target[i] != holdings:
            if target[i] and can_buy[i]:
                holdings = True
                mult *= 1.0 - buy_fee
            elif not target[i] and can_sell[i]:
                holdings = False
                round_trips += 1
                mult *= 1.0 - sell_fee[i]
        net[i] = mult - 1.0

    halves = np.split(net, 2)
    first_h = float(np.prod(1.0 + halves[0]) - 1.0)
    second_h = float(np.prod(1.0 + halves[1]) - 1.0)
    sr = calc_sharpe(net, 252)

    return {
        "net_sharpe": sr,
        "round_trips": round_trips,
        "first_half_net": first_h,
        "second_half_net": second_h,
        "passes": round_trips >= 3 and first_h > 0 and second_h > 0,
    }


def compute_prototype_ranks(sessions: pd.DatetimeIndex, market_frames: dict, signal_frames: dict, month_ends: list[int]) -> dict:
    """Computes monthly prototype rankings based on 504-day Sharpe, >=3 trades, both halves > 0."""
    ranks = {}
    date_ints = [int(d.strftime("%Y%m%d")) for d in sessions]
    buy_fee = 0.00075
    duty = np.where(np.array(date_ints) < 20230828, 0.0010, 0.0005)
    sell_fees = buy_fee + duty
    costs = (buy_fee, sell_fees)

    for me in month_ends:
        if me not in date_ints:
            continue
        idx = date_ints.index(me)
        if idx < 504:
            continue
        start_idx = idx + 1 - 504

        rows = []
        for sym, m_df in market_frames.items():
            s_df = signal_frames.get(sym)
            if s_df is None or len(m_df) < idx + 1:
                continue
            z_sub = s_df["z"].iloc[start_idx : idx + 1].to_numpy()
            if len(z_sub) < 504 or not np.isfinite(z_sub[-1]):
                continue
            # Open returns
            adj_open = m_df["adj_open"].ffill()
            open_ret = adj_open.pct_change().fillna(0.0).iloc[start_idx : idx + 1].to_numpy()
            can_b = m_df["can_buy"].iloc[start_idx : idx + 1].to_numpy()
            can_s = m_df["can_sell"].iloc[start_idx : idx + 1].to_numpy()

            res = assess_prototype_window(z_sub, open_ret, can_b, can_s, (costs[0], costs[1][start_idx : idx + 1]))
            rows.append({"code": sym, **res})

        rows.sort(key=lambda r: (-r["net_sharpe"], r["code"]))
        passing = [r for r in rows if r["passes"]]
        ranks[me] = {
            "rank_close": me,
            "watchlist": [r["code"] for r in passing[:3]],
            "passing_count": len(passing),
            "scored_count": len(rows),
        }
    return ranks


class StarResearchPipeline:
    """Master pipeline orchestrating all 6 control groups, MIT risk, and incremental value tests."""

    def __init__(
        self,
        start_date: int = 20190722,
        end_date: int = 20261126,
        replay_start_date: int = 20230901,
        history_sessions: int = 1001,
        benchmark: str = "000688.SH",
        output_dir: Optional[Path] = None,
        seed: int = 20261127,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.replay_start_date = replay_start_date
        self.history_sessions = history_sessions
        self.benchmark = benchmark
        self.output_dir = output_dir or Path("/Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit")
        self.seed = seed

    def run(self) -> Dict[str, Any]:
        print(f"=== STAR Market PE/PEG x Z-score x MIT Trader Pipeline [{self.start_date} -> {self.end_date}] ===", flush=True)

        # 1. Data Feed
        feed = StarDataFeed(
            start_date=self.start_date,
            end_date=self.end_date,
            replay_start_date=self.replay_start_date,
            history_sessions=self.history_sessions,
            benchmark=self.benchmark,
        )
        roster, eligible_symbols = feed.load_universe_roster()
        sessions = feed.load_calendar()
        benchmark_df = feed.load_benchmark(sessions)
        market_frames, audits, skipped = feed.load_market_data(eligible_symbols, sessions)
        pe_df = feed.load_pe_ttm_map()
        annual_cagr_df = feed.load_disclosed_annual_reports()
        forecast_audit = feed.audit_forecast_consensus()

        print(f"Universe: {len(roster)} STAR stocks, Eligible (>=1001 sessions): {len(eligible_symbols)}", flush=True)
        print(f"Sessions loaded: {len(sessions)}, Market frames loaded: {len(market_frames)}", flush=True)

        # 2. Z-Signal Service
        z_service = ZSignalService(window=50, entry_threshold=-2.0, exit_threshold=0.0)
        signal_frames = {sym: z_service.compute_signals(m_df) for sym, m_df in market_frames.items()}

        # 3. Calendar & Month-Ends
        date_ints = [int(d.strftime("%Y%m%d")) for d in sessions]
        month_ends = []
        next_open_dates = []
        for i in range(len(date_ints) - 1):
            if str(date_ints[i])[:6] != str(date_ints[i + 1])[:6]:
                month_ends.append(date_ints[i])
                next_open_dates.append(date_ints[i + 1])
        month_ends.append(date_ints[-1])
        next_open_dates.append(date_ints[-1])

        eligible_by_date = {me: eligible_symbols for me in month_ends}

        # 4. Valuation Service
        val_service = ValuationService(pe_lower=0.0, pe_upper=60.0, peg_lower=0.0, peg_upper=1.1)
        monthly_snapshots = val_service.compute_monthly_snapshots(
            month_ends=month_ends,
            next_open_dates=next_open_dates,
            eligible_symbols_by_date=eligible_by_date,
            pe_df=pe_df,
            disclosed_cagr_df=annual_cagr_df,
            forecast_audit=forecast_audit,
        )

        # 5. Prototype Ranks
        proto_ranks = compute_prototype_ranks(sessions, market_frames, signal_frames, month_ends)

        # 6. Benchmark Snapshots (000688.SH)
        start_idx = date_ints.index(self.replay_start_date) if self.replay_start_date in date_ints else 0
        bm_slice = benchmark_df.iloc[start_idx:]
        bm_open_norm = bm_slice["open"] / bm_slice["open"].iloc[0]
        bm_close_norm = bm_slice["close"] / bm_slice["close"].iloc[0]
        bm_daily_rets = bm_slice["close"].pct_change().fillna(0.0).to_numpy()

        # 7. Simulator & Control Groups
        simulator_base = AccountSimulator(
            initial_capital=2_000_000.0,
            num_sleeves=3,
            base_fee_bps=7.5,
            cost_stress=1.0,
            turnover_1pct_cap=True,
        )
        simulator_stress = AccountSimulator(
            initial_capital=2_000_000.0,
            num_sleeves=3,
            base_fee_bps=7.5,
            cost_stress=2.0,
            turnover_1pct_cap=True,
        )

        risk_engine = MitRiskEngine(
            garch_window=252,
            kelly_fraction=0.25,
            max_account_fraction=0.10,
            single_loss_budget_pct=0.01,
            max_portfolio_var_pct=0.02,
            daily_loss_limit_pct=0.03,
        )
        simulator_mit = AccountSimulator(
            initial_capital=2_000_000.0,
            num_sleeves=3,
            base_fee_bps=7.5,
            cost_stress=1.0,
            turnover_1pct_cap=True,
            risk_engine=risk_engine,
        )
        simulator_mit_stress = AccountSimulator(
            initial_capital=2_000_000.0,
            num_sleeves=3,
            base_fee_bps=7.5,
            cost_stress=2.0,
            turnover_1pct_cap=True,
            risk_engine=risk_engine,
        )

        print("Simulating Control Groups...", flush=True)

        # G1: Benchmark 000688.SH
        # G2: Pure Z
        res_pure_z = simulator_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "pure_z")
        res_pure_z_stress = simulator_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "pure_z")

        # G3: Prototype Rank + Z
        res_proto_z = simulator_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "prototype_rank_z", proto_ranks)
        res_proto_z_stress = simulator_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "prototype_rank_z", proto_ranks)

        # G4: Valuation Only (Top 3 PEG/PE rebalanced monthly)
        res_val_only = simulator_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_only")
        res_val_only_stress = simulator_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_only")

        # G5: Valuation + Z (Historical Disclosed Growth Proxy)
        res_val_z = simulator_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_plus_z")
        res_val_z_stress = simulator_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_plus_z")

        # G6: Valuation + Z + MIT Risk Control
        res_val_z_mit = simulator_mit.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_plus_z_mit")
        res_val_z_mit_stress = simulator_mit_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, monthly_snapshots, "valuation_plus_z_mit")

        # G5_forecast: Audit and UNESTIMABLE
        res_forecast = {
            "strategy_mode": "valuation_plus_z_forecast",
            "status": "UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING",
            "coverage_audit": forecast_audit,
            "total_return": "UNESTIMABLE",
            "sharpe_ratio": "UNESTIMABLE",
            "max_drawdown": "UNESTIMABLE",
            "backfill_conditions": forecast_audit["backfill_conditions"],
        }

        # Exposure-scaled benchmark index (000688.SH scaled to previous-day actual exposure)
        def calc_exposure_scaled_bm(snapshots):
            scaled_rets = []
            for i in range(len(snapshots)):
                exp = snapshots[i - 1].exposure if i > 0 else 0.0
                scaled_rets.append(exp * bm_daily_rets[i])
            scaled_nav = np.cumprod(1.0 + np.array(scaled_rets))
            return {
                "total_return": float(scaled_nav[-1] - 1.0),
                "sharpe": calc_sharpe(scaled_rets, 252),
                "daily_returns": scaled_rets,
            }

        bm_scaled_val_z = calc_exposure_scaled_bm(res_val_z["daily_snapshots"])
        bm_scaled_mit = calc_exposure_scaled_bm(res_val_z_mit["daily_snapshots"])

        # 8. Three Incremental Comparisons (三项增量比较)
        # Incremental 1: Valuation Edge ("叠加减纯 Z" = res_val_z - res_pure_z)
        inc1_valuation = paired_block_bootstrap_ci(
            np.array(res_val_z["daily_returns"]),
            np.array(res_pure_z["daily_returns"]),
            block_size=20, n_boot=2000, seed=self.seed,
        )

        # Incremental 2: Timing Edge ("叠加减仅估值" = res_val_z - res_val_only)
        inc2_timing = paired_block_bootstrap_ci(
            np.array(res_val_z["daily_returns"]),
            np.array(res_val_only["daily_returns"]),
            block_size=20, n_boot=2000, seed=self.seed,
        )

        # Incremental 3: MIT Risk Edge ("风控组减叠加组" = res_val_z_mit - res_val_z)
        inc3_risk = paired_block_bootstrap_ci(
            np.array(res_val_z_mit["daily_returns"]),
            np.array(res_val_z["daily_returns"]),
            block_size=20, n_boot=2000, seed=self.seed,
        )

        # 9. MIT Three Gates Validation
        validator = MitThreeGatesValidator(
            folds_count=5,
            min_oos_sessions=497,
            gross_sharpe_threshold=0.50,
            positive_folds_ratio=0.60,
            permutation_trials=2000,
            fdr_q=0.10,
            dsr_threshold=0.95,
            net_sharpe_threshold=0.30,
        )

        gates_results = {
            "pure_z": validator.evaluate_strategy(res_pure_z["daily_snapshots"], res_pure_z["daily_snapshots"], res_pure_z_stress["daily_snapshots"]),
            "prototype_rank_z": validator.evaluate_strategy(res_proto_z["daily_snapshots"], res_proto_z["daily_snapshots"], res_proto_z_stress["daily_snapshots"]),
            "valuation_only": validator.evaluate_strategy(res_val_only["daily_snapshots"], res_val_only["daily_snapshots"], res_val_only_stress["daily_snapshots"]),
            "valuation_plus_z": validator.evaluate_strategy(res_val_z["daily_snapshots"], res_val_z["daily_snapshots"], res_val_z_stress["daily_snapshots"]),
            "valuation_plus_z_mit": validator.evaluate_strategy(res_val_z_mit["daily_snapshots"], res_val_z_mit["daily_snapshots"], res_val_z_mit_stress["daily_snapshots"]),
        }

        # 10. Summary Metrics & Delivery Manifest
        benchmark_summary = {
            "code": self.benchmark,
            "sessions": len(bm_slice),
            "from_date": date_ints[start_idx],
            "through_date": date_ints[-1],
            "total_return_close": float(bm_close_norm.iloc[-1] - 1.0),
            "cagr_365": float((bm_close_norm.iloc[-1]) ** (252.0 / len(bm_slice)) - 1.0),
            "annualized_volatility": float(np.std(bm_daily_rets, ddof=1) * math.sqrt(252)),
            "sharpe_ratio": float(np.mean(bm_daily_rets) / np.std(bm_daily_rets, ddof=1) * math.sqrt(252)),
            "max_drawdown": float((bm_close_norm / np.maximum.accumulate(bm_close_norm) - 1.0).min()),
            "scaled_to_val_z_exposure": bm_scaled_val_z,
            "scaled_to_mit_exposure": bm_scaled_mit,
        }

        manifest = {
            "mode": "RESEARCH_ONLY",
            "run_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "replay_start_date": self.replay_start_date,
            "sessions_evaluated": len(bm_slice),
            "universe_count": len(roster),
            "eligible_1001d_count": len(eligible_symbols),
            "seed": self.seed,
            "benchmark": benchmark_summary,
            "control_groups": {
                "star50": benchmark_summary,
                "pure_z": {k: v for k, v in res_pure_z.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")},
                "prototype_rank_z": {k: v for k, v in res_proto_z.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")},
                "valuation_only": {k: v for k, v in res_val_only.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")},
                "valuation_plus_z_proxy": {k: v for k, v in res_val_z.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")},
                "valuation_plus_z_mit": {k: v for k, v in res_val_z_mit.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")},
                "valuation_plus_z_forecast": res_forecast,
            },
            "incremental_value_comparisons": {
                "valuation_edge_vs_pure_z": inc1_valuation,
                "timing_edge_vs_valuation_only": inc2_timing,
                "mit_risk_edge_vs_valuation_z": inc3_risk,
            },
            "three_gates": gates_results,
            "forecast_audit": forecast_audit,
        }

        # Export Files
        self.output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.output_dir / "metrics.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Save Daily NAV CSV
        nav_df = pd.DataFrame(
            {
                "trade_date": [s.trade_date for s in res_val_z["daily_snapshots"]],
                "star50_close_norm": bm_close_norm.to_numpy(),
                "pure_z_nav": res_pure_z["nav_series"],
                "prototype_rank_z_nav": res_proto_z["nav_series"],
                "valuation_only_nav": res_val_only["nav_series"],
                "valuation_plus_z_nav": res_val_z["nav_series"],
                "valuation_plus_z_mit_nav": res_val_z_mit["nav_series"],
            }
        )
        nav_df.to_csv(self.output_dir / "daily_nav_comparison.csv", index=False)

        # Save Fills Parquet/CSV
        fills_df = pd.DataFrame(res_val_z_mit["fills"])
        fills_df.to_csv(self.output_dir / "mit_risk_fills.csv", index=False)

        print(f"Pipeline complete! Artifacts saved to {self.output_dir}", flush=True)
        return {
            "manifest": manifest,
            "res_pure_z": res_pure_z,
            "res_proto_z": res_proto_z,
            "res_val_only": res_val_only,
            "res_val_z": res_val_z,
            "res_val_z_mit": res_val_z_mit,
            "monthly_snapshots": monthly_snapshots,
            "benchmark_summary": benchmark_summary,
            "incremental_comparisons": {
                "inc1": inc1_valuation,
                "inc2": inc2_timing,
                "inc3": inc3_risk,
            },
            "gates_results": gates_results,
        }
