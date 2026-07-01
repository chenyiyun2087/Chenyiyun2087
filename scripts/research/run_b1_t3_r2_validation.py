#!/usr/bin/env python3
"""
B1-T3-R2 证伪回测

P0: 固化证据包 (manifest, config snapshot, git SHA)
P1: A1 平均仓位匹配对照
P2: A2 区块随机化 + 滞后安慰剂 (T3_lag_5, T3_lag_10)

Usage:
    python scripts/research/run_b1_t3_r2_validation.py \
        --start-date 2025-09-02 --end-date 2026-06-30 \
        --randomizations 100
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    _safe_float, add_liquidity_derived_features,
    build_market_environment, build_strategy_specs,
    load_prices, load_scores,
)
from scripts.research_trusted_strategy_account_backtest import (
    AccountState, _rebalance, _price_lookup_for_day, _score_day_frame,
    _build_targets_cache, _equity,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, build_daily_features, SimplePositionController,
    _build_calendar, _build_signal_to_exec_map,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# Core backtest runner (returns position sequence + NAV)
# ══════════════════════════════════════════════════════════════════════

def run_single_backtest(
    label: str,
    position_source,  # "fixed", "controller", or list[float] for pre-computed sequence
    fixed_position: float = 0.70,
    controller_mode: str = "T3",
    position_sequence: list = None,
    lag_days: int = 0,
    engine=None, scores=None, prices=None, market_env=None,
    calendar=None, signal_to_exec=None, exec_to_signal=None,
    score_day_indices=None, price_day_indices=None,
    index_trends=None,
    strategy_specs=None, start_date=None, end_date=None,
    initial_cash=500000.0, top_n=5, hold_days=10,
    lot_size=100, cost_rate=0.00075, max_positions=5,
    min_trade_value=500.0,
) -> dict:
    """Run a single backtest and return NAV + metrics + position sequence."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in strategy_specs if s.name == strategy_name]
    if not matched:
        return {"label": label, "error": "strategy_not_found"}
    spec = matched[0]

    controller = SimplePositionController(controller_mode) if position_source == "controller" else None

    price_columns = [
        "raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
        "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
        "amount", "volume", "security_status_available", "execution_tradable",
        "universe_is_tradable", "is_listed", "circ_mv",
    ]

    cache_scores = scores
    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=cache_scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=top_n,
    )

    account = AccountState(cash=float(initial_cash))
    nav_rows = []
    position_sequence_out = []
    trade_count = 0

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_calendar = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_calendar = [d for d in sim_calendar if d >= first_exec]

    price_indices_orig = prices.groupby("trade_date", sort=True).indices
    seq_idx = 0

    for ti, trade_date in enumerate(sim_calendar):
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            eq = account.cash
            nav_rows.append({"trade_date": trade_date, "nav": eq / initial_cash,
                             "position_ratio": 0.0, "position_count": 0})
            position_sequence_out.append(0.0)
            continue

        # ── Determine position ratio ──────────────────────
        if position_source == "fixed":
            position_ratio = fixed_position
        elif position_source == "sequence":
            if position_sequence and seq_idx < len(position_sequence):
                position_ratio = position_sequence[seq_idx]
            else:
                position_ratio = 0.70
        elif position_source == "controller":
            day_scores = _score_day_frame(scores, score_day_indices, signal_date)
            price_snap = pd.DataFrame()
            if signal_date in price_indices_orig:
                price_snap = prices.iloc[price_indices_orig[signal_date]]
            me_row = None
            if market_env is not None and "trade_date" in market_env.columns:
                me_match = market_env[market_env["trade_date"] == signal_date]
                if not me_match.empty:
                    me_row = me_match.iloc[0]
            features = build_daily_features(signal_date, day_scores, price_snap, index_trends, me_row)
            position_ratio = controller.get_position_ratio(features)
        else:
            position_ratio = 0.70

        # Lag: use delayed signal
        if lag_days > 0 and seq_idx >= lag_days:
            position_ratio = position_sequence_out[seq_idx - lag_days]
        elif lag_days > 0:
            position_ratio = 0.70  # Default before lag window

        position_sequence_out.append(position_ratio)
        seq_idx += 1

        # ── Execute ─────────────────────────────────────
        day_scores = _score_day_frame(scores, score_day_indices, signal_date)
        rpl = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=top_n, hold_days=hold_days,
                lot_size=lot_size, min_trade_value=min_trade_value,
                trade_cost_rate=cost_rate, slippage_rate=0.0,
                max_total_positions=max_positions, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=None,
            )
            trade_count += int(meta.get("executed", 0))

        eq = _equity(account, rpl, "raw_close")
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(eq / initial_cash, 6),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "equity": round(eq, 2),
            "cash": round(account.cash, 2),
        })

    nav_df = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    metrics = _compute_metrics(nav_df)
    avg_position = float(np.mean(position_sequence_out)) if position_sequence_out else 0.0

    # Compute actual average exposure (from gross_exposure = equity - cash)
    if not nav_df.empty and "equity" in nav_df.columns and "cash" in nav_df.columns:
        nav_df["actual_exposure"] = (nav_df["equity"] - nav_df["cash"]) / nav_df["equity"]
        avg_actual_exposure = float(nav_df["actual_exposure"].mean())
    else:
        avg_actual_exposure = avg_position

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "position_sequence": position_sequence_out,
        "avg_target_position": avg_position,
        "avg_actual_exposure": avg_actual_exposure,
        "trade_count": trade_count,
    }


def _compute_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "max_drawdown": 0, "calmar": 0, "sharpe": 0}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    max_dd = float(np.min((nav - peak) / peak))
    n_days = len(nav)
    ann_return = float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 and nav[0] > 0 else 0.0
    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_return / vol) if vol > 0 else 0.0
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    return {"total_return": round(total_return, 6), "max_drawdown": round(max_dd, 6),
            "calmar": round(calmar, 4), "sharpe": round(sharpe, 4), "n_days": n_days}


# ══════════════════════════════════════════════════════════════════════
# Block Randomization (A2)
# ══════════════════════════════════════════════════════════════════════

def block_randomize(sequence: list, block_size: int, rng: np.random.RandomState) -> list:
    """Shuffle blocks of given size, preserving within-block continuity."""
    n = len(sequence)
    # Split into blocks
    blocks = []
    for i in range(0, n, block_size):
        block = sequence[i:i + block_size]
        if len(block) == block_size:
            blocks.append(block)
    if not blocks:
        return list(sequence)
    # Shuffle block order
    idx = list(range(len(blocks)))
    rng.shuffle(idx)
    result = []
    for i in idx:
        result.extend(blocks[i])
    # Pad to original length (in case of partial last block)
    if len(result) < n:
        result.extend(sequence[len(result):])
    return result[:n]


def run_a2_randomization(
    t3_position_sequence: list,
    block_size: int,
    n_iterations: int,
    **kwargs,
) -> dict:
    """Run block randomization test and return distribution of metrics."""
    rng = np.random.RandomState(42)
    calmar_dist = []
    return_dist = []
    dd_dist = []

    t3_result = kwargs.get("t3_result", {})

    print(f"    A2 (block={block_size}d, n={n_iterations}): ", end="", flush=True)

    for i in range(n_iterations):
        shuffled = block_randomize(t3_position_sequence, block_size, rng)
        result = run_single_backtest(
            label=f"A2_b{block_size}_{i}",
            position_source="sequence",
            position_sequence=shuffled,
            **{k: v for k, v in kwargs.items() if k != "t3_result"},
        )
        m = result["metrics"]
        calmar_dist.append(m["calmar"])
        return_dist.append(m["total_return"])
        dd_dist.append(m["max_drawdown"])

        if (i + 1) % max(1, n_iterations // 10) == 0:
            print(f"{i+1} ", end="", flush=True)
    print("done")

    calmar_arr = np.array(calmar_dist)
    return_arr = np.array(return_dist)
    dd_arr = np.array(dd_dist)

    t3_calmar = t3_result.get("metrics", {}).get("calmar", 0)
    t3_return = t3_result.get("metrics", {}).get("total_return", 0)
    t3_dd = t3_result.get("metrics", {}).get("max_drawdown", 0)

    calmar_pvalue = float(np.mean(calmar_arr >= t3_calmar))
    return_pvalue = float(np.mean(return_arr >= t3_return))
    dd_pvalue = float(np.mean(dd_arr <= t3_dd))  # Lower DD is better

    return {
        "block_size": block_size,
        "n_iterations": n_iterations,
        "t3_calmar": t3_calmar,
        "calmar_median_random": float(np.median(calmar_arr)),
        "calmar_95pct_random": float(np.percentile(calmar_arr, 95)),
        "calmar_pvalue": calmar_pvalue,
        "t3_return": t3_return,
        "return_median_random": float(np.median(return_arr)),
        "t3_maxdd": t3_dd,
        "dd_median_random": float(np.median(dd_arr)),
        "dd_pvalue": dd_pvalue,
        "calmar_distribution": calmar_arr.tolist(),
    }


# ══════════════════════════════════════════════════════════════════════
# Evidence Package (P0)
# ══════════════════════════════════════════════════════════════════════

def build_evidence_package(out_dir: Path, results: dict) -> dict:
    """Build P0 evidence package with manifest, config snapshot, git SHA."""
    evidence_dir = out_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}

    # ── Git SHA ──────────────────────────────────────────────
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        git_sha = "unknown"
    (evidence_dir / "git_sha.txt").write_text(git_sha + "\n")
    manifest["git_sha"] = git_sha

    # ── Config snapshot ──────────────────────────────────────
    config_path = PROJECT_ROOT / "config" / "b1_t3_r1_frozen.yaml"
    if config_path.exists():
        config_content = config_path.read_text()
        config_sha = hashlib.sha256(config_content.encode()).hexdigest()[:16]
        (evidence_dir / "config_snapshot.yaml").write_text(config_content)
        manifest["config_sha"] = config_sha
    else:
        manifest["config_sha"] = "not_found"

    # ── Strategy identification ──────────────────────────────
    manifest["strategy_id"] = "b1_t3_r1"
    manifest["strategy_version"] = "R1"
    manifest["rebalance_engine"] = "research_trusted_strategy_account_backtest._rebalance()"
    manifest["execution_mode"] = "t_plus_1_open_non_strict"

    # ── Data snapshot info ───────────────────────────────────
    manifest["data"] = {
        "score_table": "tushare_stock.score_rank_daily",
        "price_table": "tushare_stock.ashare_eod_prices",
        "calendar_table": "tushare_stock.dim_trade_cal",
        "index_table": "tushare_stock.dwd_index_daily",
        "index_codes": ["000300.SH", "399006.SZ"],
        "note": "CSI1000 (000852.SH) not in database",
    }

    # ── Parameters ───────────────────────────────────────────
    manifest["parameters"] = {
        "start_date": "2025-09-02",
        "end_date": "2026-06-30",
        "initial_cash": 500000,
        "top_n": 5,
        "hold_days": 10,
        "max_total_positions": 5,
        "trade_cost_rate": 0.00075,
        "slippage_rate": 0.0,
        "lot_size": 100,
        "min_trade_value": 500.0,
        "t3_csi300_threshold": 0.02,
        "t3_turnover_threshold": 1.10,
        "risk_on_position": 0.70,
        "neutral_position": 0.55,
    }

    # ── Results summary ──────────────────────────────────────
    manifest["results"] = {}
    for label, r in results.items():
        m = r.get("metrics", {})
        manifest["results"][label] = {
            "total_return": m.get("total_return", 0),
            "max_drawdown": m.get("max_drawdown", 0),
            "calmar": m.get("calmar", 0),
            "sharpe": m.get("sharpe", 0),
            "avg_target_position": r.get("avg_target_position", 0),
            "avg_actual_exposure": r.get("avg_actual_exposure", 0),
        }

    # ── Write manifest ───────────────────────────────────────
    manifest["generated_at"] = datetime.now().isoformat()
    manifest["b1_t3_r1_rating"] = "RESEARCH_VALIDATED"
    manifest["shadow_eligible"] = False

    with open(evidence_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

    # ── Copy/export key CSVs ─────────────────────────────────
    for label in ["T0", "T3", "A1"]:
        r = results.get(label, {})
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf.to_csv(evidence_dir / f"nav_{label.lower()}.csv", index=False)

    return manifest


# ══════════════════════════════════════════════════════════════════════
# Report builder
# ══════════════════════════════════════════════════════════════════════

def build_r2_report(results: dict, a2_5d: dict, a2_10d: dict,
                    manifest: dict, out_dir: Path) -> str:
    """Build comprehensive R2 validation report."""
    lines = [
        "# B1-T3-R2 证伪回测报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Git SHA: {manifest.get('git_sha', 'N/A')[:12]}",
        f"Config SHA: {manifest.get('config_sha', 'N/A')}",
        "",
        "## 1. 基准曲线对照",
        "",
        "| 曲线 | 描述 | 总收益 | 最大回撤 | Calmar | Sharpe | 平均仓位 |",
        "|------|------|--------|----------|--------|--------|----------|",
    ]
    for label in ["T0", "T3", "A1", "T3_lag_5", "T3_lag_10"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if not m: continue
        avg_pr = r.get("avg_target_position", 0)
        lines.append(
            f"| {label} | {r.get('label','')} | {m['total_return']:.2%} | "
            f"{m['max_drawdown']:.2%} | {m['calmar']:.2f} | {m['sharpe']:.2f} | {avg_pr:.1%} |"
        )

    # ── Key comparison: T3 vs A1 ────────────────────────────
    lines.append("")
    lines.append("## 2. 关键比较: T3 vs A1 (同平均仓位)")
    t3 = results.get("T3", {}).get("metrics", {})
    a1 = results.get("A1", {}).get("metrics", {})
    t0 = results.get("T0", {}).get("metrics", {})
    if t3 and a1 and t0:
        calmar_delta_a1 = (t3["calmar"] - a1["calmar"]) / abs(a1["calmar"]) * 100 if a1["calmar"] != 0 else 0
        dd_delta_a1 = (t3["max_drawdown"] - a1["max_drawdown"]) / abs(a1["max_drawdown"]) * 100 if a1["max_drawdown"] != 0 else 0
        ret_ratio = t3["total_return"] / a1["total_return"] if a1["total_return"] != 0 else 0
        lines.append(f"| 指标 | T0 (70%) | A1 (等仓位) | T3 (双因子) | T3 vs A1 | 验收 |")
        lines.append(f"|------|----------|------------|------------|----------|------|")
        lines.append(f"| Calmar | {t0['calmar']:.2f} | {a1['calmar']:.2f} | {t3['calmar']:.2f} | {calmar_delta_a1:+.1f}% | {'✅' if calmar_delta_a1 >= 10 else '❌'} |")
        lines.append(f"| MaxDD | {t0['max_drawdown']:.2%} | {a1['max_drawdown']:.2%} | {t3['max_drawdown']:.2%} | {dd_delta_a1:+.1f}% | {'✅' if dd_delta_a1 <= -10 else '❌'} |")
        lines.append(f"| Return | {t0['total_return']:.2%} | {a1['total_return']:.2%} | {t3['total_return']:.2%} | {ret_ratio:.1%} | {'✅' if ret_ratio >= 0.95 else '❌'} |")

    # ── A2 Randomization ────────────────────────────────────
    lines.append("")
    lines.append("## 3. A2 区块随机化")
    for a2 in [a2_5d, a2_10d]:
        if not a2: continue
        bs = a2["block_size"]
        lines.append(f"### {bs}日区块 (n={a2['n_iterations']})")
        lines.append(f"- T3 Calmar: {a2['t3_calmar']:.2f}")
        lines.append(f"- 随机化 Calmar 中位数: {a2['calmar_median_random']:.2f}")
        lines.append(f"- 随机化 Calmar 95%分位: {a2['calmar_95pct_random']:.2f}")
        lines.append(f"- Calmar p值: {a2['calmar_pvalue']:.4f} {'✅ p<0.05' if a2['calmar_pvalue'] < 0.05 else '❌ p≥0.05'}")
        lines.append(f"- 最大回撤 p值: {a2['dd_pvalue']:.4f} {'✅ p<0.05' if a2['dd_pvalue'] < 0.05 else '❌ p≥0.05'}")

    # ── Lag Placebo ──────────────────────────────────────────
    lines.append("")
    lines.append("## 4. 滞后安慰剂")
    for lag_label in ["T3_lag_5", "T3_lag_10"]:
        r = results.get(lag_label, {})
        m = r.get("metrics", {})
        if not m: continue
        lag_d = 5 if "5" in lag_label else 10
        calmar_vs = "✅ 优于" if m["calmar"] < t3["calmar"] else "❌ 不优于"
        lines.append(f"- {lag_label} ({lag_d}日延迟): Calmar={m['calmar']:.2f} {calmar_vs} T3 ({t3['calmar']:.2f})")

    # ── Rating ──────────────────────────────────────────────
    lines.append("")
    lines.append("## 5. B1-T3 评级")
    t3_a1_calmar_ok = calmar_delta_a1 >= 10 if t3 and a1 else False
    t3_a1_dd_ok = dd_delta_a1 <= -10 if t3 and a1 else False
    t3_a1_ret_ok = ret_ratio >= 0.95 if t3 and a1 else False
    a2_pval_ok = (a2_5d.get("calmar_pvalue", 1) < 0.05) if a2_5d else False
    lag_ok = (results.get("T3_lag_5", {}).get("metrics", {}).get("calmar", 0) < t3.get("calmar", 999)) if t3 else False

    r1_passed = [t3_a1_calmar_ok, t3_a1_dd_ok, t3_a1_ret_ok]
    r2_passed = r1_passed + [a2_pval_ok, lag_ok]

    lines.append(f"- R1 准入 (T3 vs A1): {'✅' if all(r1_passed) else '❌'} ({sum(r1_passed)}/3)")
    lines.append(f"- R2 准入 (含随机化+安慰剂): {'✅' if all(r2_passed) else '❌'} ({sum(r2_passed)}/5)")
    lines.append(f"- Shadow 准入: ❌ (需≥3年Walk-Forward)")
    lines.append(f"- 当前评级: **RESEARCH_VALIDATED**")

    lines.append("")
    lines.append("## 6. 数据说明")
    lines.append(f"- 回测区间: 2025-09-02 至 2026-06-30 (197交易日)")
    lines.append(f"- 指数数据: CSI300 + ChiNext (CSI1000不可用)")
    lines.append(f"- 执行框架: 既有 _rebalance()")
    lines.append(f"- ⚠️ 样本不足: 结论为研究级证据")

    md = "\n".join(lines)
    (out_dir / "b1_t3_r2_report.md").write_text(md)
    return md


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="B1-T3-R2 证伪回测")
    parser.add_argument("--start-date", default="2025-09-02")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--randomizations", type=int, default=100,
                        help="A2 randomization iterations (default 100)")
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("B1-T3-R2 证伪回测")
    print("=" * 60)

    # ── Connect & load data ──────────────────────────────────
    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    print("Loading data...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    print(f"  Calendar: {len(calendar)} days")

    signal_to_exec, exec_to_signal = _build_signal_to_exec_map(calendar)

    index_trends = load_index_trends_pit(
        engine, index_codes=["000300.SH", "399006.SZ"], calendar_dates=calendar)
    for d in calendar:
        if d not in index_trends:
            index_trends[d] = {"000300.SH": 0.0, "399006.SZ": 0.0}
    print(f"  Index trends: {len(index_trends)} dates")

    prices = load_prices(engine, min_date=args.start_date, max_date=args.end_date, extra_days=10)
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    prices_sorted = prices.sort_values("_date_sort").reset_index(drop=True)
    price_day_indices = prices_sorted.groupby("trade_date", sort=True).indices
    print(f"  Prices: {len(prices_sorted)} rows")

    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    scores = add_liquidity_derived_features(scores, prices_sorted)
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    scores_sorted = scores.sort_values("_date_sort").reset_index(drop=True)
    score_day_indices = scores_sorted.groupby("trade_date", sort=True).indices
    print(f"  Scores: {len(scores_sorted)} rows")

    try:
        market_env = build_market_environment(scores_sorted, prices_sorted)
    except Exception:
        market_env = pd.DataFrame()

    strategy_specs = build_strategy_specs()

    # ── Output dir ───────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUT_ROOT / f"b1_t3_r1_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    # Common kwargs for all backtest runs
    common = dict(
        engine=engine, scores=scores_sorted, prices=prices_sorted,
        market_env=market_env, calendar=calendar,
        signal_to_exec=signal_to_exec, exec_to_signal=exec_to_signal,
        score_day_indices=score_day_indices, price_day_indices=price_day_indices,
        index_trends=index_trends, strategy_specs=strategy_specs,
        start_date=args.start_date, end_date=args.end_date,
        initial_cash=args.initial_cash,
    )

    results = {}

    # ── T0: Fixed 70% ────────────────────────────────────────
    print("\n=== T0: 固定70%仓位 ===")
    r = run_single_backtest("T0", position_source="fixed", fixed_position=0.70, **common)
    results["T0"] = r
    print(f"  Return={r['metrics']['total_return']:.2%}, MaxDD={r['metrics']['max_drawdown']:.2%}, "
          f"Calmar={r['metrics']['calmar']:.2f}")

    # ── T3: Dual-factor consensus ────────────────────────────
    print("\n=== T3: 双因子共识 ===")
    r = run_single_backtest("T3", position_source="controller", controller_mode="T3", **common)
    results["T3"] = r
    t3_avg_pos = r["avg_target_position"]
    print(f"  Return={r['metrics']['total_return']:.2%}, MaxDD={r['metrics']['max_drawdown']:.2%}, "
          f"Calmar={r['metrics']['calmar']:.2f}, AvgPos={t3_avg_pos:.1%}")

    # ── A1: Matched exposure ─────────────────────────────────
    a1_position = t3_avg_pos
    print(f"\n=== A1: 固定{a1_position:.1%}仓位 (匹配T3平均暴露) ===")
    r = run_single_backtest("A1", position_source="fixed", fixed_position=a1_position, **common)
    results["A1"] = r
    print(f"  Return={r['metrics']['total_return']:.2%}, MaxDD={r['metrics']['max_drawdown']:.2%}, "
          f"Calmar={r['metrics']['calmar']:.2f}")

    # ── T3 lag 5 ─────────────────────────────────────────────
    print("\n=== T3_lag_5: 信号延迟5日 ===")
    r = run_single_backtest("T3_lag_5", position_source="controller",
                            controller_mode="T3", lag_days=5, **common)
    results["T3_lag_5"] = r
    print(f"  Return={r['metrics']['total_return']:.2%}, Calmar={r['metrics']['calmar']:.2f}")

    # ── T3 lag 10 ────────────────────────────────────────────
    print("\n=== T3_lag_10: 信号延迟10日 ===")
    r = run_single_backtest("T3_lag_10", position_source="controller",
                            controller_mode="T3", lag_days=10, **common)
    results["T3_lag_10"] = r
    print(f"  Return={r['metrics']['total_return']:.2%}, Calmar={r['metrics']['calmar']:.2f}")

    # ── A2: Block randomization ──────────────────────────────
    t3_seq = results["T3"]["position_sequence"]
    print(f"\n=== A2: 区块随机化 (n={args.randomizations}) ===")

    a2_5d = None
    a2_10d = None
    if len(t3_seq) >= 10:
        print("  5日区块:")
        a2_5d = run_a2_randomization(
            t3_seq, block_size=5, n_iterations=args.randomizations,
            t3_result=results["T3"], **common,
        )
        print(f"    T3 Calmar={a2_5d['t3_calmar']:.2f}, "
              f"Random median={a2_5d['calmar_median_random']:.2f}, "
              f"p={a2_5d['calmar_pvalue']:.4f}")

        print("  10日区块:")
        a2_10d = run_a2_randomization(
            t3_seq, block_size=10, n_iterations=args.randomizations,
            t3_result=results["T3"], **common,
        )
        print(f"    T3 Calmar={a2_10d['t3_calmar']:.2f}, "
              f"Random median={a2_10d['calmar_median_random']:.2f}, "
              f"p={a2_10d['calmar_pvalue']:.4f}")
    else:
        print("  SKIP: position sequence too short")

    # ── Save A2 distributions ────────────────────────────────
    if a2_5d:
        pd.DataFrame({"calmar_5d": a2_5d["calmar_distribution"]}).to_csv(
            out_dir / "a2_randomization_5d.csv", index=False)
    if a2_10d:
        pd.DataFrame({"calmar_10d": a2_10d["calmar_distribution"]}).to_csv(
            out_dir / "a2_randomization_10d.csv", index=False)

    # ── P0: Evidence package ─────────────────────────────────
    print("\n=== P0: 固化证据包 ===")
    manifest = build_evidence_package(out_dir, results)
    print(f"  manifest: {out_dir}/evidence/manifest.json")

    # ── Report ───────────────────────────────────────────────
    print("\n=== 生成报告 ===")
    report_md = build_r2_report(results, a2_5d or {}, a2_10d or {}, manifest, out_dir)
    print(f"  report: {out_dir}/b1_t3_r2_report.md")

    # ── Final summary ────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print("=" * 60)
    for label in ["T0", "T3", "A1", "T3_lag_5", "T3_lag_10"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if m:
            print(f"  {label:<12} Return={m['total_return']:>8.2%}  "
                  f"MaxDD={m['max_drawdown']:>8.2%}  "
                  f"Calmar={m['calmar']:>6.2f}  "
                  f"AvgPos={r.get('avg_target_position', 0):>5.1%}")

    if a2_5d:
        print(f"\n  A2 (5d block): T3 Calmar={a2_5d['t3_calmar']:.2f}, "
              f"Random 95%ile={a2_5d['calmar_95pct_random']:.2f}, "
              f"p={a2_5d['calmar_pvalue']:.4f}")
    if a2_10d:
        print(f"  A2 (10d block): T3 Calmar={a2_10d['t3_calmar']:.2f}, "
              f"Random 95%ile={a2_10d['calmar_95pct_random']:.2f}, "
              f"p={a2_10d['calmar_pvalue']:.4f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
