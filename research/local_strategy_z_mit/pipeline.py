"""Master pipeline for Local Strategies (scoreRank & chenyiyunSelected) x Z-score x MIT Trader."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.star_pe_peg_z_mit.account_simulator import AccountSimulator
from research.star_pe_peg_z_mit.mit_risk_engine import MitRiskEngine
from research.star_pe_peg_z_mit.validator import MitThreeGatesValidator, paired_block_bootstrap_ci
from research.star_pe_peg_z_mit.z_signal_service import ZSignalService

from .data_loader import LocalStrategyDataLoader


SHANGHAI = ZoneInfo("Asia/Shanghai")


class LocalStrategyZPipeline:
    """Runs scoreRank and chenyiyunSelected with and without Z-score timing and MIT risk control."""

    def __init__(
        self,
        start_date: int = 20230103,
        end_date: int = 20261124,
        replay_start_date: int = 20230901,
        benchmark: str = "000300.SH",
        output_dir: Optional[Path] = None,
        seed: int = 20261127,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.replay_start_date = replay_start_date
        self.benchmark = benchmark
        self.output_dir = output_dir or Path("/Volumes/extension/projects/Chenyiyun2087/exports/backtest/local_strategy_z_mit")
        self.seed = seed

    def run(self) -> Dict[str, Any]:
        print(f"=== Local Strategy (scoreRank & chenyiyunSelected) x Z x MIT Pipeline [{self.start_date} -> {self.end_date}] ===", flush=True)

        loader = LocalStrategyDataLoader(
            start_date=self.start_date,
            end_date=self.end_date,
            replay_start_date=self.replay_start_date,
            benchmark=self.benchmark,
        )
        sessions = loader.load_calendar()
        benchmark_df = loader.load_benchmark(sessions)

        date_ints = [int(d.strftime("%Y%m%d")) for d in sessions]
        month_ends = []
        next_open_dates = []
        for i in range(len(date_ints) - 1):
            if str(date_ints[i])[:6] != str(date_ints[i + 1])[:6]:
                month_ends.append(date_ints[i])
                next_open_dates.append(date_ints[i + 1])
        month_ends.append(date_ints[-1])
        next_open_dates.append(date_ints[-1])

        print("Building monthly watchlists for scoreRank and chenyiyunSelected...", flush=True)
        sr_snaps, sr_syms = loader.build_scorerank_snapshots(month_ends, next_open_dates, sessions)
        cy_snaps, cy_syms = loader.build_chenyiyun_selected_snapshots(month_ends, next_open_dates)
        union_syms = sr_syms | cy_syms
        print(f"Candidate union: {len(union_syms)} stocks (scoreRank={len(sr_syms)}, chenyiyunSelected={len(cy_syms)})", flush=True)

        market_frames = loader.load_market_frames_bulk(union_syms, sessions)
        print(f"Market frames loaded: {len(market_frames)} stocks across {len(sessions)} sessions", flush=True)

        z_service = ZSignalService(window=50, entry_threshold=-2.0, exit_threshold=0.0)
        signal_frames = {sym: z_service.compute_signals(mf) for sym, mf in market_frames.items()}

        start_idx = date_ints.index(self.replay_start_date) if self.replay_start_date in date_ints else 0
        bm_slice = benchmark_df.iloc[start_idx:]
        bm_close_norm = bm_slice["close"] / bm_slice["close"].iloc[0]
        bm_daily_rets = bm_slice["close"].pct_change(fill_method=None).fillna(0.0).to_numpy()

        sim_base = AccountSimulator(
            initial_capital=2_000_000.0, num_sleeves=3, base_fee_bps=7.5,
            cost_stress=1.0, auto_board_lot=True, turnover_1pct_cap=True,
        )
        sim_stress = AccountSimulator(
            initial_capital=2_000_000.0, num_sleeves=3, base_fee_bps=7.5,
            cost_stress=2.0, auto_board_lot=True, turnover_1pct_cap=True,
        )
        risk_engine = MitRiskEngine(
            garch_window=252, kelly_fraction=0.25, max_account_fraction=0.10,
            single_loss_budget_pct=0.01, max_portfolio_var_pct=0.02, daily_loss_limit_pct=0.03,
        )
        sim_mit = AccountSimulator(
            initial_capital=2_000_000.0, num_sleeves=3, base_fee_bps=7.5,
            cost_stress=1.0, auto_board_lot=True, turnover_1pct_cap=True, risk_engine=risk_engine,
        )
        sim_mit_stress = AccountSimulator(
            initial_capital=2_000_000.0, num_sleeves=3, base_fee_bps=7.5,
            cost_stress=2.0, auto_board_lot=True, turnover_1pct_cap=True, risk_engine=risk_engine,
        )

        print("Simulating 6 strategy arms (plus 2x cost stress tests)...", flush=True)
        res_sr_only = sim_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_only")
        res_sr_only_2x = sim_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_only")
        res_sr_z = sim_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_plus_z")
        res_sr_z_2x = sim_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_plus_z")
        res_sr_mit = sim_mit.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_plus_z_mit")
        res_sr_mit_2x = sim_mit_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, sr_snaps, "valuation_plus_z_mit")

        res_cy_only = sim_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_only")
        res_cy_only_2x = sim_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_only")
        res_cy_z = sim_base.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_plus_z")
        res_cy_z_2x = sim_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_plus_z")
        res_cy_mit = sim_mit.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_plus_z_mit")
        res_cy_mit_2x = sim_mit_stress.simulate(sessions, self.replay_start_date, market_frames, signal_frames, cy_snaps, "valuation_plus_z_mit")

        inc = {
            "scorerank_timing_edge": paired_block_bootstrap_ci(
                np.array(res_sr_z["daily_returns"]), np.array(res_sr_only["daily_returns"]),
                block_size=20, n_boot=2000, seed=self.seed,
            ),
            "scorerank_mit_edge": paired_block_bootstrap_ci(
                np.array(res_sr_mit["daily_returns"]), np.array(res_sr_z["daily_returns"]),
                block_size=20, n_boot=2000, seed=self.seed,
            ),
            "chenyiyun_timing_edge": paired_block_bootstrap_ci(
                np.array(res_cy_z["daily_returns"]), np.array(res_cy_only["daily_returns"]),
                block_size=20, n_boot=2000, seed=self.seed,
            ),
            "chenyiyun_mit_edge": paired_block_bootstrap_ci(
                np.array(res_cy_mit["daily_returns"]), np.array(res_cy_z["daily_returns"]),
                block_size=20, n_boot=2000, seed=self.seed,
            ),
        }

        validator = MitThreeGatesValidator(
            folds_count=5, min_oos_sessions=497, gross_sharpe_threshold=0.50,
            positive_folds_ratio=0.60, permutation_trials=2000, fdr_q=0.10,
            dsr_threshold=0.95, net_sharpe_threshold=0.30,
        )
        gates = {
            "scorerank_only": validator.evaluate_strategy(res_sr_only["daily_snapshots"], bm_daily_rets, res_sr_only_2x["daily_snapshots"]),
            "scorerank_plus_z": validator.evaluate_strategy(res_sr_z["daily_snapshots"], bm_daily_rets, res_sr_z_2x["daily_snapshots"]),
            "scorerank_plus_z_mit": validator.evaluate_strategy(res_sr_mit["daily_snapshots"], bm_daily_rets, res_sr_mit_2x["daily_snapshots"]),
            "chenyiyun_only": validator.evaluate_strategy(res_cy_only["daily_snapshots"], bm_daily_rets, res_cy_only_2x["daily_snapshots"]),
            "chenyiyun_plus_z": validator.evaluate_strategy(res_cy_z["daily_snapshots"], bm_daily_rets, res_cy_z_2x["daily_snapshots"]),
            "chenyiyun_plus_z_mit": validator.evaluate_strategy(res_cy_mit["daily_snapshots"], bm_daily_rets, res_cy_mit_2x["daily_snapshots"]),
        }

        bm_summary = {
            "code": self.benchmark,
            "sessions": len(bm_slice),
            "from_date": date_ints[start_idx],
            "through_date": date_ints[-1],
            "total_return_close": float(bm_close_norm.iloc[-1] - 1.0),
            "cagr_annualized": float((bm_close_norm.iloc[-1]) ** (252.0 / len(bm_slice)) - 1.0),
            "annualized_volatility": float(np.std(bm_daily_rets, ddof=1) * math.sqrt(252)),
            "sharpe_ratio": float(np.mean(bm_daily_rets) / np.std(bm_daily_rets, ddof=1) * math.sqrt(252)),
            "max_drawdown": float((bm_close_norm / np.maximum.accumulate(bm_close_norm) - 1.0).min()),
        }

        def strip(res: dict) -> dict:
            return {k: v for k, v in res.items() if k not in ("fills", "daily_snapshots", "nav_series", "daily_returns")}

        manifest = {
            "mode": "RESEARCH_ONLY",
            "run_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "replay_start_date": self.replay_start_date,
            "sessions_evaluated": len(bm_slice),
            "candidate_union_count": len(market_frames),
            "scorerank_candidates_count": len(sr_syms),
            "chenyiyun_candidates_count": len(cy_syms),
            "data_notes": {
                "chenyiyun_mlev": "tushare_stock.dwd_fina_indicator 为空表(0行)，mlev 因子按原策略缺省中位数填充退化为等权透传；股息率(dv_ttm/dv_ratio)、换手波动(turnover_vol_20)、流通市值(circ_mv)全量真实生效。",
                "scorerank_score": "chenyiyun.score_rank_daily(s_liquidity) + ods_daily 实时复算 liquidity_detail_score 全量真实生效。",
            },
            "benchmark": bm_summary,
            "control_groups": {
                "hs300": bm_summary,
                "scorerank_only": strip(res_sr_only),
                "scorerank_plus_z": strip(res_sr_z),
                "scorerank_plus_z_mit": strip(res_sr_mit),
                "chenyiyun_only": strip(res_cy_only),
                "chenyiyun_plus_z": strip(res_cy_z),
                "chenyiyun_plus_z_mit": strip(res_cy_mit),
            },
            "incremental_value_comparisons": inc,
            "three_gates": gates,
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.output_dir / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        nav_df = pd.DataFrame(
            {
                "trade_date": [s.trade_date for s in res_sr_z["daily_snapshots"]],
                "hs300_close_norm": bm_close_norm.to_numpy(),
                "scorerank_only_nav": res_sr_only["nav_series"],
                "scorerank_plus_z_nav": res_sr_z["nav_series"],
                "scorerank_plus_z_mit_nav": res_sr_mit["nav_series"],
                "chenyiyun_only_nav": res_cy_only["nav_series"],
                "chenyiyun_plus_z_nav": res_cy_z["nav_series"],
                "chenyiyun_plus_z_mit_nav": res_cy_mit["nav_series"],
            }
        )
        nav_df.to_csv(self.output_dir / "daily_nav_comparison.csv", index=False)
        print(f"Local Strategy x Z x MIT Pipeline complete! Saved to {self.output_dir}", flush=True)
        return manifest
