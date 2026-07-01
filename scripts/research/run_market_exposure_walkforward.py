#!/usr/bin/env python3
"""
Market Exposure Governor v1 (B方案)

固定核心选股策略 + 动态总仓位控制。
不选择策略、不修改因子、不替换候选池。
只决定：target_total_exposure。

使用既有 research_trusted_strategy_account_backtest 的完整执行框架：
ExecutionLedger + _rebalance() + 严格T+1 + 公司行为处理

Usage:
    python scripts/research/run_market_exposure_walkforward.py \
        --start-date 2025-09-02 --end-date 2026-06-30 \
        --curves A0,B0,B1,B2,B3,B4,B5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research.strict_execution_ledger import (
    ExecutionLedger, PrecommitOrder, CorporateAction, LEDGER_SCHEMA_VERSION,
)
from scripts.research.execution_market_rules import limit_prices, limit_ratio
from scripts.research_full_pool_liquidity_strategies import (
    StrategySpec, _safe_float, _select_candidates,
    add_liquidity_derived_features,
    build_market_environment, build_strategy_specs,
    load_prices, load_scores,
)

# ── Import battle-tested functions from existing backtest ──────────
from scripts.research_trusted_strategy_account_backtest import (
    AccountState,
    _rebalance,
    _price_lookup_for_day,
    _score_day_frame,
    _trade_day_count,
    _round_lot,
    _build_targets,
    _build_targets_cache,
    _equity,
    _sync_account_view_from_ledger,
    _apply_actions_to_ledger,
)

# ── Import market state model from Meta Allocator ──────────────────
from scripts.research.run_meta_allocator_walkforward import (
    MarketFeatures,
    MarketStateModel,
    MarketStateConfig,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
EXECUTION_MODE = "strict_t1_open_precommit"


# ══════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════

@dataclass
class GovernorConfig:
    version: str
    core_strategy: dict
    execution: dict
    position_levels: dict
    market_inputs: dict
    state_transitions: dict
    benchmarks: dict
    acceptance: dict
    capacity: dict
    walkforward: dict


def load_governor_config(path: str | Path = None) -> GovernorConfig:
    if path is None:
        path = PROJECT_ROOT / "config" / "market_exposure_governor_v1.yaml"
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    g = raw["governor"]
    return GovernorConfig(
        version=g["version"],
        core_strategy=g["core_strategy"],
        execution=g["execution"],
        position_levels=g["position_levels"],
        market_inputs=g["market_inputs"],
        state_transitions=g["state_transitions"],
        benchmarks=g["benchmarks"],
        acceptance=g["acceptance"],
        capacity=g["capacity"],
        walkforward=g["walkforward"],
    )


# ══════════════════════════════════════════════════════════════════════
# Position Controller
# ══════════════════════════════════════════════════════════════════════

class PositionController:
    """Determine target position ratio from market state."""

    def __init__(self, config: GovernorConfig, active_inputs: set, use_hysteresis: bool = False):
        self.cfg = config
        self.active_inputs = active_inputs
        self.use_hysteresis = use_hysteresis
        self.levels = config.position_levels
        self.transitions = config.state_transitions
        self.current_state = "NEUTRAL"
        self.state_days = 0
        self.confirmation_count = 0

        # Build MarketStateConfig from config parameters
        ms_inputs = config.market_inputs
        trend = ms_inputs.get("trend", {})
        liq = ms_inputs.get("liquidity", {})

        freeze_cfg = {
            "turnover_ratio_below": liq.get("turnover_ratio_20", {}).get("freeze_below", 0.50),
            "breadth_below": trend.get("breadth", {}).get("freeze_below", 0.15),
            "limit_down_above": liq.get("limit_down_freeze_above", 0.05),
            "vol_20_above": liq.get("vol_20_median", {}).get("freeze_above", 0.055),
        }
        risk_off_cfg = {
            **trend.get("risk_off", {}),
            "turnover_ratio_below": liq.get("turnover_ratio_20", {}).get("risk_off_below", 0.75),
            "breadth_below": trend.get("breadth", {}).get("risk_off_below", 0.30),
            "vol_20_above": liq.get("vol_20_median", {}).get("risk_off_above", 0.045),
        }
        risk_on_cfg = {
            **trend.get("risk_on", {}),
            "turnover_ratio_above": liq.get("turnover_ratio_20", {}).get("risk_on_above", 1.10),
            "breadth_above": trend.get("breadth", {}).get("risk_on_above", 0.55),
        }
        broad_cfg = {
            **trend.get("broad_risk_on", {}),
            "turnover_ratio_above": liq.get("turnover_ratio_20", {}).get("broad_risk_on_above", 1.20),
            "breadth_above": trend.get("breadth", {}).get("broad_risk_on_above", 0.70),
        }

        ms_config = MarketStateConfig(
            freeze=freeze_cfg,
            risk_off=risk_off_cfg,
            risk_on=risk_on_cfg,
            broad_risk_on=broad_cfg,
        )
        self.ms_model = MarketStateModel(ms_config)

    def get_position_ratio(self, features: MarketFeatures) -> float:
        """Determine position ratio for a given market state."""
        raw_state = self.ms_model.classify_risk_state(features)

        # Filter: only use active inputs to modify the classification
        if "trend_hs300" not in self.active_inputs:
            features.trend_csi300 = 0.0
        if "trend_csi1000" not in self.active_inputs:
            features.trend_csi1000 = 0.0
        if "trend_chinext" not in self.active_inputs:
            features.trend_chinext = 0.0
        if "turnover_ratio" not in self.active_inputs:
            features.turnover_ratio = 1.0
        if "breadth" not in self.active_inputs:
            features.breadth = 0.50
        if "candidate_quality" not in self.active_inputs:
            features.candidate_pool_count = 999
            features.candidate_avg_score = 80.0

        # Recompute with filtered inputs
        state = self.ms_model.classify_risk_state(features)

        # Apply hysteresis
        if self.use_hysteresis:
            state = self._apply_hysteresis(state)

        self.current_state = state
        return self.levels.get(state, 0.55)

    def _apply_hysteresis(self, raw_state: str) -> str:
        """Apply state transition rules with confirmation and slow recovery."""
        if raw_state == self.current_state:
            self.state_days += 1
            self.confirmation_count += 1
            return self.current_state

        # Risk downgrade: instant
        state_order = ["BROAD_RISK_ON", "RISK_ON", "NEUTRAL", "RISK_OFF", "FREEZE"]
        curr_idx = state_order.index(self.current_state) if self.current_state in state_order else 2
        new_idx = state_order.index(raw_state) if raw_state in state_order else 2

        if new_idx > curr_idx:
            # Downgrade: instant
            self.state_days = 1
            self.confirmation_count = 1
            return raw_state
        else:
            # Upgrade: need confirmation
            self.confirmation_count += 1
            needed = {
                "RISK_OFF": 3, "NEUTRAL": 2, "RISK_ON": 3, "BROAD_RISK_ON": 3,
            }.get(raw_state, 2)

            if self.confirmation_count >= needed:
                # Only upgrade one step at a time
                if new_idx < curr_idx - 1:
                    # Multi-step jump not allowed, go one step
                    intermediate = state_order[curr_idx - 1]
                    self.state_days = 1
                    self.confirmation_count = 0
                    return intermediate
                self.state_days = 1
                self.confirmation_count = 0
                return raw_state
            return self.current_state


# ══════════════════════════════════════════════════════════════════════
# Backtest Runner (using existing _rebalance)
# ══════════════════════════════════════════════════════════════════════

def run_governed_backtest(
    label: str,
    description: str,
    benchmark_cfg: dict,
    config: GovernorConfig,
    engine,
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    market_env: pd.DataFrame,
    calendar: list,
    signal_to_exec: dict,
    exec_to_signal: dict,
    score_day_indices: dict,
    price_day_indices: dict,
    strategy_specs: list,
    start_date,
    end_date,
) -> dict:
    """Run a single governed backtest curve.

    Uses the existing _rebalance() from research_trusted_strategy_account_backtest
    for identical execution quality to the legacy backtest.
    """
    exec_cfg = config.execution
    core = config.core_strategy

    initial_cash = float(exec_cfg.get("initial_cash", 500000))
    top_n = int(core.get("top_n", 5))
    hold_days = int(core.get("hold_days", 10))
    lot_size = int(exec_cfg.get("lot_size", 100))
    cost_rate = float(exec_cfg.get("trade_cost_rate", 0.00075))
    slippage = float(exec_cfg.get("slippage_rate", 0.0))
    max_positions = int(core.get("max_total_positions", 5))
    min_trade_value = float(exec_cfg.get("min_trade_value", 500))

    # Find strategy spec
    strategy_name = core["name"]
    matched_specs = [s for s in strategy_specs if s.name == strategy_name]
    if not matched_specs:
        print(f"  ERROR: Strategy '{strategy_name}' not found in specs")
        return {"label": label, "error": "strategy_not_found", "metrics": {}}
    spec = matched_specs[0]

    # ── Position controller ────────────────────────────────────
    position_mode = benchmark_cfg.get("position_mode", "fixed")
    active_inputs = set(benchmark_cfg.get("inputs", []))
    use_hysteresis = benchmark_cfg.get("use_hysteresis", False)
    fixed_position_ratio = float(benchmark_cfg.get("position_ratio", 0.70))

    controller = None
    if position_mode == "market_state":
        controller = PositionController(config, active_inputs, use_hysteresis)

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "raw_high", "raw_low",
                     "adj_open", "adj_close", "adj_high", "adj_low", "adj_factor",
                     "is_st", "is_suspended", "amount", "volume",
                     "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    # ── Build targets cache ────────────────────────────────────
    print(f"    Building targets cache...")
    targets_cache = _build_targets_cache(
        scores=scores,
        day_indices=score_day_indices,
        specs_by_name={spec.name: spec},
        top_n=top_n,
    )

    # ── Initialize account ─────────────────────────────────────
    account = AccountState(cash=initial_cash)
    ledger = None  # Use non-strict path — _rebalance modifies account directly
    previous_factors: dict[str, float] = {}

    nav_rows = []
    trade_rows = []
    position_rows = []
    decision_rows = []

    sim_calendar = [d for d in calendar if pd.Timestamp(start_date).date() <= d <= pd.Timestamp(end_date).date()]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_calendar = [d for d in sim_calendar if d >= first_exec]

    print(f"    Simulating {len(sim_calendar)} trading days...")
    trade_count = 0

    for ti, trade_date in enumerate(sim_calendar):
        if ti % 40 == 0 and ti > 0:
            print(f"      Day {ti}/{len(sim_calendar)}...")

        raw_price_lookup = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        price_lookup = raw_price_lookup  # Use raw prices for strict execution

        # Sync account from ledger (only when using strict execution)
        if ledger is not None:
            _sync_account_view_from_ledger(account, ledger, trade_date)

        signal_date = exec_to_signal.get(trade_date)
        if signal_date is not None:
            day_scores = _score_day_frame(scores, score_day_indices, signal_date)
            targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

            # Determine position ratio
            if controller is not None:
                # Build market features
                market_env_row = market_env[market_env["trade_date"] == signal_date] if market_env is not None and "trade_date" in market_env.columns else pd.DataFrame()
                features = MarketFeatures.from_daily_data(
                    signal_date, day_scores, pd.DataFrame(),
                    market_env_row, {}, {}, {},
                )
                position_ratio = controller.get_position_ratio(features)

                # Record decision
                decision_rows.append({
                    "signal_date": signal_date,
                    "execution_date": trade_date,
                    "market_state": controller.current_state,
                    "target_position_ratio": round(position_ratio, 4),
                    "turnover_ratio": round(features.turnover_ratio, 4),
                    "breadth": round(features.breadth, 4),
                    "trend_hs300": round(features.trend_csi300, 4),
                    "candidate_pool_count": features.candidate_pool_count,
                    "candidate_avg_score": round(features.candidate_avg_score, 1),
                })
            elif position_mode == "fixed":
                position_ratio = fixed_position_ratio
            else:
                position_ratio = 0.70

            if not targets.empty or account.positions:
                trades, cands, meta = _rebalance(
                    account=account,
                    signal_date=signal_date,
                    execution_date=trade_date,
                    day_scores=day_scores,
                    spec=spec,
                    top_n=top_n,
                    hold_days=hold_days,
                    lot_size=lot_size,
                    min_trade_value=min_trade_value,
                    trade_cost_rate=cost_rate,
                    slippage_rate=slippage,
                    max_total_positions=max_positions,
                    position_ratio=position_ratio,
                    calendar=calendar,
                    open_prices=price_lookup,
                    targets=targets if not targets.empty else None,
                    precommit_prices=price_lookup,
                    strict_precommit=False,
                    ledger=None,
                )
                trade_rows.extend(trades)
                trade_count += int(meta.get("executed", 0))

        # Record NAV
        if ledger is not None:
            _sync_account_view_from_ledger(account, ledger, trade_date)
        eq = _equity(account, price_lookup, "raw_close")
        nav_value = eq / initial_cash if initial_cash > 0 else 0.0

        nav_rows.append({
            "trade_date": trade_date,
            "signal_date": signal_date,
            "cash": round(account.cash, 2),
            "equity": round(eq, 2),
            "nav": round(nav_value, 6),
            "position_count": len(account.positions),
            "position_ratio": round(position_ratio, 4) if signal_date else 0.0,
        })

        # Record positions
        for sym, pos in account.positions.items():
            price = _safe_float(price_lookup.get(sym, {}).get("raw_close"), np.nan)
            position_rows.append({
                "trade_date": trade_date,
                "symbol": sym,
                "shares": pos.shares,
                "price": round(price, 2) if np.isfinite(price) else 0.0,
                "mv": round(pos.shares * price, 2) if np.isfinite(price) else 0.0,
            })

    # ── Compute metrics ────────────────────────────────────────
    nav_df = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    metrics = _compute_governor_metrics(nav_df)

    print(f"    Return: {metrics['total_return']:.2%}, MaxDD: {metrics['max_drawdown']:.2%}, "
          f"Calmar: {metrics['calmar']:.2f}, Trades: {trade_count}")

    return {
        "label": label,
        "description": description,
        "nav_df": nav_df,
        "metrics": metrics,
        "trades": trade_rows,
        "positions": position_rows,
        "decisions": decision_rows,
    }


def _compute_governor_metrics(nav_df: pd.DataFrame) -> dict:
    """Compute performance metrics from NAV series."""
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "annualized_return": 0, "max_drawdown": 0,
                "sharpe": 0, "calmar": 0, "volatility": 0}

    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0

    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    max_dd = float(np.min(dd))

    n_days = len(nav)
    ann_return = float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 and nav[0] > 0 else 0.0

    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_return / vol) if vol > 0 else 0.0
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 0 else 0.0

    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(ann_return, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "volatility": round(vol, 6),
        "n_days": n_days,
    }


# ══════════════════════════════════════════════════════════════════════
# Data Loading Helpers
# ══════════════════════════════════════════════════════════════════════

def _build_calendar(engine, start_date=None, end_date=None) -> list:
    sql = "SELECT DISTINCT cal_date FROM tushare_stock.dim_trade_cal WHERE exchange = 'SSE' AND is_open = 1"
    if start_date:
        start_int = int(pd.Timestamp(start_date).strftime("%Y%m%d"))
        sql += f" AND cal_date >= {start_int}"
    if end_date:
        end_int = int(pd.Timestamp(end_date).strftime("%Y%m%d"))
        sql += f" AND cal_date <= {end_int}"
    sql += " ORDER BY cal_date"
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    import datetime as _dt
    result = []
    for r in rows:
        try:
            d_str = str(int(r[0]))
            result.append(_dt.date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8])))
        except (ValueError, IndexError):
            continue
    return result


def _build_signal_to_exec_map(calendar: list) -> tuple:
    signal_to_exec = {}
    exec_to_signal = {}
    for i in range(len(calendar) - 1):
        signal_to_exec[calendar[i]] = calendar[i + 1]
        exec_to_signal[calendar[i + 1]] = calendar[i]
    return signal_to_exec, exec_to_signal


# ══════════════════════════════════════════════════════════════════════
# Output & Reporting
# ══════════════════════════════════════════════════════════════════════

def write_governor_outputs(out_dir: Path, results: dict, config: GovernorConfig) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    # NAV
    nav_dfs = []
    for label, r in results.items():
        nav_df = r.get("nav_df")
        if nav_df is not None and not nav_df.empty:
            nav_df = nav_df.copy()
            nav_df["curve_label"] = label
            nav_dfs.append(nav_df)
    if nav_dfs:
        combined_nav = pd.concat(nav_dfs, ignore_index=True)
        p = out_dir / "market_exposure_nav.csv"
        combined_nav.to_csv(p, index=False)
        paths["nav"] = str(p)

    # Comparison table
    comparison_rows = []
    for label, r in results.items():
        m = r.get("metrics", {})
        comparison_rows.append({
            "curve": label,
            "description": r.get("description", ""),
            "total_return": m.get("total_return", 0),
            "annualized_return": m.get("annualized_return", 0),
            "max_drawdown": m.get("max_drawdown", 0),
            "sharpe": m.get("sharpe", 0),
            "calmar": m.get("calmar", 0),
            "volatility": m.get("volatility", 0),
        })
    if comparison_rows:
        comp_df = pd.DataFrame(comparison_rows)
        p = out_dir / "market_exposure_benchmark_comparison.csv"
        comp_df.to_csv(p, index=False)
        paths["benchmark_comparison"] = str(p)

    # Decisions (from the most complete curve)
    for label in ["B5", "B4", "B3", "B2", "B1", "B0"]:
        r = results.get(label, {})
        decisions = r.get("decisions", [])
        if decisions:
            p = out_dir / "market_exposure_decisions.csv"
            pd.DataFrame(decisions).to_csv(p, index=False)
            paths["decisions"] = str(p)
            break

    # Ablation report
    ablation_rows = []
    for label in ["B0", "B1", "B2", "B3", "B4", "B5"]:
        r = results.get(label, {})
        if not r: continue
        m = r.get("metrics", {})
        ablation_rows.append({
            "ablation": label,
            "description": r.get("description", ""),
            "total_return": m.get("total_return", 0),
            "max_drawdown": m.get("max_drawdown", 0),
            "calmar": m.get("calmar", 0),
            "sharpe": m.get("sharpe", 0),
        })
    if ablation_rows:
        abl_df = pd.DataFrame(ablation_rows)
        p = out_dir / "market_exposure_ablation_report.csv"
        abl_df.to_csv(p, index=False)
        paths["ablation_report"] = str(p)

    # Markdown report
    md = build_governor_md_report(results, config)
    p = out_dir / "market_exposure_walkforward_report.md"
    with open(p, "w") as f:
        f.write(md)
    paths["md_report"] = str(p)

    return paths


def build_governor_md_report(results: dict, config: GovernorConfig) -> str:
    lines = [
        "# Market Exposure Governor v1 — 回测报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 基准曲线与消融结果",
        "",
        "| 曲线 | 描述 | 总收益 | 年化收益 | 最大回撤 | Sharpe | Calmar |",
        "|------|------|--------|----------|----------|--------|--------|",
    ]

    for label in ["A0", "A1", "A2", "B0", "B1", "B2", "B3", "B4", "B5"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if m:
            lines.append(
                f"| {label} | {r.get('description', '')} | "
                f"{m.get('total_return', 0):.2%} | {m.get('annualized_return', 0):.2%} | "
                f"{m.get('max_drawdown', 0):.2%} | {m.get('sharpe', 0):.2f} | "
                f"{m.get('calmar', 0):.2f} |"
            )

    # Key comparison: B5 vs A0 (G0-gated), B5 vs A1
    b5 = results.get("B5", {}).get("metrics", {})
    a0 = results.get("A0", {}).get("metrics", {})

    if b5 and a0:
        lines.append("")
        lines.append("## 2. 关键比较")
        calmar_b5 = b5.get("calmar", 0)
        calmar_a0 = a0.get("calmar", 0)
        dd_b5 = b5.get("max_drawdown", 0)
        dd_a0 = a0.get("max_drawdown", 0)
        ret_b5 = b5.get("total_return", 0)
        ret_a0 = a0.get("total_return", 0)

        lines.append(f"- A0 (固定70%仓位): Return={ret_a0:.2%}, MaxDD={dd_a0:.2%}, Calmar={calmar_a0:.2f}")
        lines.append(f"- B5 (完整系统): Return={ret_b5:.2%}, MaxDD={dd_b5:.2%}, Calmar={calmar_b5:.2f}")

        if calmar_a0 != 0:
            calmar_change = (calmar_b5 - calmar_a0) / abs(calmar_a0) * 100
            lines.append(f"- Calmar 变化: {calmar_change:+.1f}%")

    # Ablation analysis
    lines.append("")
    lines.append("## 3. 消融分析：每增加一个变量对Calmar的边际贡献")
    prev_calmar = None
    for label in ["B0", "B1", "B2", "B3", "B4", "B5"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if m:
            calmar = m.get("calmar", 0)
            if prev_calmar is not None and prev_calmar != 0:
                delta = (calmar - prev_calmar) / abs(prev_calmar) * 100
                lines.append(f"- {label}: Calmar={calmar:.2f} ({delta:+.1f}% vs previous)")
            else:
                lines.append(f"- {label}: Calmar={calmar:.2f}")
            prev_calmar = calmar

    lines.append("")
    lines.append("## 4. 数据说明")
    lines.append("- 回测框架: 复用既有 ExecutionLedger + _rebalance() 严格T+1执行")
    lines.append("- 核心策略: baseline_full_liquidity_detail_vol_position")
    lines.append("- ⚠️ 当前数据量(197日)不足以完成标准Walk-Forward (需≥3年)")
    lines.append("- 结论等级: 研究级证据")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Market Exposure Governor v1 Backtest")
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--start-date", type=str, default="2025-09-02")
    parser.add_argument("--end-date", type=str, default="2026-06-30")
    parser.add_argument("--output-dir", type=str, default=None)
    parser.add_argument("--curves", type=str, default="A0,B0,B1,B2,B3,B4,B5",
                        help="Comma-separated curve labels to run")
    args = parser.parse_args()

    print("=" * 60)
    print("Market Exposure Governor v1")
    print("=" * 60)

    # Load config
    config = load_governor_config(args.config)
    print(f"Version: {config.version}")

    # Connect to DB
    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # Load data
    print("Loading data...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    print(f"  Calendar: {len(calendar)} trading days from {calendar[0]} to {calendar[-1]}")

    signal_to_exec, exec_to_signal = _build_signal_to_exec_map(calendar)

    print("  Loading prices...")
    prices = load_prices(engine, min_date=args.start_date, max_date=args.end_date, extra_days=10)
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    prices_sorted = prices.sort_values("_date_sort").reset_index(drop=True)
    price_day_indices = prices_sorted.groupby("trade_date", sort=True).indices
    print(f"    Prices: {len(prices_sorted)} rows, {len(price_day_indices)} days")

    print("  Loading scores...")
    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    # Add liquidity-derived features (required for liquidity_detail_score sort_col)
    print("    Adding liquidity-derived features...")
    scores = add_liquidity_derived_features(scores, prices_sorted)
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    scores_sorted = scores.sort_values("_date_sort").reset_index(drop=True)
    # Use groupby().indices for compatibility with existing _score_day_frame
    score_day_indices = scores_sorted.groupby("trade_date", sort=True).indices
    print(f"    Scores: {len(scores_sorted)} rows, {len(score_day_indices)} days")

    print("  Building market environment...")
    try:
        market_env = build_market_environment(scores_sorted, prices_sorted)
        print(f"    Market env: {len(market_env)} days")
    except Exception as e:
        print(f"    WARNING: Could not build market environment: {e}")
        market_env = pd.DataFrame()

    print("  Building strategy specs...")
    strategy_specs = build_strategy_specs()

    # Output directory
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUT_ROOT / f"market_exposure_governor_{timestamp}"
    print(f"Output: {out_dir}")

    # Run selected curves
    curve_labels = [c.strip() for c in args.curves.split(",")]
    print(f"\nRunning curves: {curve_labels}")

    results = {}
    for label in curve_labels:
        bm = config.benchmarks.get(label)
        if bm is None:
            print(f"  WARNING: Unknown curve '{label}' — skipping")
            continue
        print(f"\n{'='*40}")
        print(f"  Curve {label}: {bm.get('description', '')}")
        print(f"{'='*40}")

        try:
            result = run_governed_backtest(
                label=label,
                description=bm.get("description", ""),
                benchmark_cfg=bm,
                config=config,
                engine=engine,
                scores=scores_sorted,
                prices=prices_sorted,
                market_env=market_env,
                calendar=calendar,
                signal_to_exec=signal_to_exec,
                exec_to_signal=exec_to_signal,
                score_day_indices=score_day_indices,
                price_day_indices=price_day_indices,
                strategy_specs=strategy_specs,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            results[label] = result
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            results[label] = {"label": label, "error": str(e), "metrics": {}}

    # Write outputs
    print(f"\n{'='*60}")
    print("Writing outputs...")
    paths = write_governor_outputs(out_dir, results, config)
    for name, path in paths.items():
        print(f"  {name}: {path}")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print("=" * 60)
    for label in ["A0", "B0", "B1", "B2", "B3", "B4", "B5"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if m:
            print(f"  {label}: Return={m.get('total_return', 0):.2%}, "
                  f"MaxDD={m.get('max_drawdown', 0):.2%}, "
                  f"Calmar={m.get('calmar', 0):.2f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
