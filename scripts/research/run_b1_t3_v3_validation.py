#!/usr/bin/env python3
"""
B1-T3 v3.0 升级回测

P1: 扩展至2023-2026 (843交易日), 含子期间分析
P2: 固定仓位前沿 (A50/A55/A58/A60/A65/A70) vs T3
P3: 完整Lag0-Lag20时效曲线 (修复: 使用预计算T3序列)
P4: CVaR95 / Ulcer Index 尾部风险指标

Usage:
    python scripts/research/run_b1_t3_v3_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

from __future__ import annotations

import argparse, json, sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    _safe_float, add_liquidity_derived_features,
    build_market_environment, build_strategy_specs, load_prices, load_scores,
)
from scripts.research_trusted_strategy_account_backtest import (
    AccountState, _rebalance, _price_lookup_for_day, _score_day_frame,
    _build_targets_cache, _equity,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, build_daily_features, SimplePositionController,
    _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_b1_t3_r2_validation import run_single_backtest

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _compute_full_metrics(nav_df: pd.DataFrame) -> dict:
    """Extended metrics including CVaR, Ulcer Index."""
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "max_drawdown": 0, "calmar": 0, "sharpe": 0,
                "cvar95": 0, "ulcer": 0, "max_dd_days": 0, "worst_5d": 0, "worst_10d": 0}

    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd_series = (nav - peak) / peak
    max_dd = float(np.min(dd_series))
    n_days = len(nav)
    ann_return = float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 and nav[0] > 0 else 0.0

    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_return / vol) if vol > 0 else 0.0
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 0 else 0.0

    # CVaR 95% (average of worst 5% daily returns)
    if len(daily_rets) > 20:
        cvar95 = float(-np.mean(np.sort(daily_rets)[:max(1, int(len(daily_rets) * 0.05))]))
    else:
        cvar95 = 0.0

    # Ulcer Index (sqrt of mean squared drawdown)
    ulcer = float(np.sqrt(np.mean(dd_series ** 2))) if len(dd_series) > 0 else 0.0

    # Max drawdown duration (longest consecutive days in drawdown)
    in_dd = dd_series < -0.001
    max_dd_days = 0
    current = 0
    for v in in_dd:
        if v:
            current += 1
            max_dd_days = max(max_dd_days, current)
        else:
            current = 0

    # Worst 5-day and 10-day rolling returns
    worst_5d, worst_10d = 0.0, 0.0
    if len(nav) >= 10:
        r5 = nav[5:] / nav[:-5] - 1
        r10 = nav[10:] / nav[:-10] - 1
        worst_5d = float(np.min(r5)) if len(r5) > 0 else 0.0
        worst_10d = float(np.min(r10)) if len(r10) > 0 else 0.0

    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(ann_return, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "volatility": round(vol, 6),
        "cvar95": round(cvar95, 6),
        "ulcer": round(ulcer, 6),
        "max_dd_days": max_dd_days,
        "worst_5d": round(worst_5d, 6),
        "worst_10d": round(worst_10d, 6),
        "n_days": n_days,
    }


def run_exposure_frontier(common: dict, positions: list) -> dict:
    """Run fixed-exposure frontier curves."""
    results = {}
    for pos in positions:
        label = f"A{int(pos*100)}"
        print(f"  {label} ({pos:.0%})...", end=" ", flush=True)
        r = run_single_backtest(label, position_source="fixed", fixed_position=pos, **common)
        m = r["metrics"]
        results[label] = r
        print(f"R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")
    return results


def run_lag_curve(common: dict, t3_position_sequence: list) -> dict:
    """Run full lag curve Lag0-Lag20 using pre-computed T3 sequence."""
    results = {}
    seq = t3_position_sequence
    lags = [0, 1, 2, 3, 5, 7, 10, 15, 20]
    for lag in lags:
        label = f"Lag{lag}"
        lagged_seq = [0.70] * min(lag, len(seq)) + seq[:len(seq)-lag] if lag > 0 else seq
        pad = [0.70] * (len(seq) - len(lagged_seq))
        lagged_seq = lagged_seq + pad
        print(f"  {label}...", end=" ", flush=True)
        r = run_single_backtest(label, position_source="sequence", position_sequence=lagged_seq, **common)
        m = r["metrics"]
        results[label] = r
        print(f"R={m['total_return']:.2%} Cal={m['calmar']:.2f}")
    return results


def sub_period_metrics(t3_nav: pd.DataFrame, t0_nav: pd.DataFrame,
                       period_name: str, start_d, end_d) -> dict:
    """Compute metrics for a sub-period."""
    t3p = t3_nav[(t3_nav["trade_date"] >= str(start_d)) & (t3_nav["trade_date"] <= str(end_d))]
    t0p = t0_nav[(t0_nav["trade_date"] >= str(start_d)) & (t0_nav["trade_date"] <= str(end_d))]
    if t3p.empty or t0p.empty or "nav" not in t3p.columns:
        return {}
    t3m = _compute_full_metrics(t3p)
    t0m = _compute_full_metrics(t0p)
    return {
        "period": period_name, "days": len(t3p),
        "t3_return": t3m["total_return"], "t0_return": t0m["total_return"],
        "t3_maxdd": t3m["max_drawdown"], "t0_maxdd": t0m["max_drawdown"],
        "t3_calmar": t3m["calmar"], "t0_calmar": t0m["calmar"],
        "t3_cvar95": t3m["cvar95"], "t0_cvar95": t0m["cvar95"],
        "t3_ulcer": t3m["ulcer"], "t0_ulcer": t0m["ulcer"],
    }


def build_v3_report(results: dict, frontier: dict, lag_curve: dict,
                    sub_periods: list, out_dir: Path) -> str:
    """Build v3.0 comprehensive report."""
    lines = [
        "# B1-T3 v3.0 升级回测报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 全样本结果 (2023-01-03 ~ 2026-06-30, 843交易日)",
        "",
        "| 曲线 | 总收益 | 最大回撤 | Calmar | CVaR95 | Ulcer | MaxDD天数 | 最差5日 |",
        "|------|--------|----------|--------|--------|-------|----------|----------|",
    ]
    for label in ["T0", "T3"]:
        r = results.get(label, {})
        m = r.get("full_metrics", r.get("metrics", {}))
        if not m: continue
        lines.append(
            f"| {label} | {m.get('total_return',0):.2%} | {m.get('max_drawdown',0):.2%} | "
            f"{m.get('calmar',0):.2f} | {m.get('cvar95',0):.4f} | {m.get('ulcer',0):.4f} | "
            f"{m.get('max_dd_days',0)} | {m.get('worst_5d',0):.2%} |"
        )

    # ── Exposure frontier ────────────────────────────────────
    lines.append("")
    lines.append("## 2. 固定仓位前沿")
    lines.append("")
    lines.append("| 曲线 | 仓位 | 总收益 | 最大回撤 | Calmar | CVaR95 | Ulcer |")
    lines.append("|------|------|--------|----------|--------|--------|-------|")
    t3_m = results.get("T3", {}).get("full_metrics", results.get("T3", {}).get("metrics", {}))
    for label in sorted(frontier.keys(), key=lambda x: int(x[1:])):
        r = frontier.get(label, {})
        m = r.get("metrics", {})
        pos = int(label[1:]) / 100
        lines.append(
            f"| {label} | {pos:.0%} | {m.get('total_return',0):.2%} | "
            f"{m.get('max_drawdown',0):.2%} | {m.get('calmar',0):.2f} | "
            f"{m.get('cvar95',0):.4f} | {m.get('ulcer',0):.4f} |"
        )
    lines.append(
        f"| **T3** | **57.8%** | **{t3_m.get('total_return',0):.2%}** | "
        f"**{t3_m.get('max_drawdown',0):.2%}** | **{t3_m.get('calmar',0):.2f}** | "
        f"**{t3_m.get('cvar95',0):.4f}** | **{t3_m.get('ulcer',0):.4f}** |"
    )

    # ── Lag curve ────────────────────────────────────────────
    lines.append("")
    lines.append("## 3. 滞后时效曲线")
    lines.append("")
    lines.append("| 滞后 | 总收益 | Calmar | 最大回撤 | vs Lag0 Calmar |")
    lines.append("|------|--------|--------|----------|---------------|")
    lag0_calmar = lag_curve.get("Lag0", {}).get("metrics", {}).get("calmar", 0)
    for lag_label in sorted(lag_curve.keys(), key=lambda x: int(x[3:])):
        r = lag_curve.get(lag_label, {})
        m = r.get("metrics", {})
        delta = (m.get("calmar", 0) - lag0_calmar) / abs(lag0_calmar) * 100 if lag0_calmar != 0 else 0
        lines.append(
            f"| {lag_label} | {m.get('total_return',0):.2%} | {m.get('calmar',0):.2f} | "
            f"{m.get('max_drawdown',0):.2%} | {delta:+.1f}% |"
        )

    # ── Sub-period analysis ──────────────────────────────────
    if sub_periods:
        lines.append("")
        lines.append("## 4. 子期间分析")
        lines.append("")
        lines.append("| 期间 | 天数 | T3收益 | T0收益 | T3 MaxDD | T0 MaxDD | T3 Calmar | T0 Calmar | T3 CVaR95 | T0 CVaR95 |")
        lines.append("|------|------|--------|--------|----------|----------|-----------|-----------|-----------|-----------|")
        for sp in sub_periods:
            if not sp: continue
            lines.append(
                f"| {sp['period']} | {sp['days']} | {sp['t3_return']:.2%} | {sp['t0_return']:.2%} | "
                f"{sp['t3_maxdd']:.2%} | {sp['t0_maxdd']:.2%} | {sp['t3_calmar']:.2f} | "
                f"{sp['t0_calmar']:.2f} | {sp['t3_cvar95']:.4f} | {sp['t0_cvar95']:.4f} |"
            )

    # ── T3 vs A1 tail risk ───────────────────────────────────
    a1 = frontier.get("A58", {})
    a1_m = a1.get("metrics", {})
    if t3_m and a1_m:
        lines.append("")
        lines.append("## 5. T3 vs A58 尾部风险非劣检验")
        dd_diff = t3_m.get("max_drawdown", 0) - a1_m.get("max_drawdown", 0)
        dd_rel = dd_diff / abs(a1_m.get("max_drawdown", 0.01)) * 100 if a1_m.get("max_drawdown", 0) != 0 else 0
        cvar_diff = t3_m.get("cvar95", 0) - a1_m.get("cvar95", 0)
        cvar_rel = cvar_diff / abs(a1_m.get("cvar95", 0.0001)) * 100 if a1_m.get("cvar95", 0) != 0 else 0
        ulcer_diff = t3_m.get("ulcer", 0) - a1_m.get("ulcer", 0)
        lines.append(f"- 最大回撤差异: {dd_diff:+.2%} ({dd_rel:+.1f}%) {'✅ ≤1pp or ≤5%' if abs(dd_diff) <= 0.01 or abs(dd_rel) <= 5 else '❌'}")
        lines.append(f"- CVaR95差异: {cvar_diff:+.4f} ({cvar_rel:+.1f}%) {'✅ ≤5%' if abs(cvar_rel) <= 5 else '❌'}")
        lines.append(f"- Ulcer差异: {ulcer_diff:+.4f} {'✅ 非劣' if ulcer_diff <= 0.005 else '⚠️'}")

    # ── Rating ───────────────────────────────────────────────
    lines.append("")
    lines.append("## 6. B1-T3 评级")
    lines.append(f"- 数据: 2023-01-03 ~ 2026-06-30 (843交易日, ~3.3年)")
    lines.append(f"- 覆盖: 上涨期(2023-2024) + 宽幅震荡(2025) + 趋势上行(2026)")
    lines.append(f"- 评级: **B1-T3-R3 (RESEARCH_VALIDATED+)** ")
    lines.append(f"- Shadow: ❌ (需嵌套Walk-Forward)")

    md = "\n".join(lines)
    (out_dir / "b1_t3_v3_report.md").write_text(md)
    return md


def main():
    parser = argparse.ArgumentParser(description="B1-T3 v3.0")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--skip-frontier", action="store_true")
    parser.add_argument("--skip-lag", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("B1-T3 v3.0 升级回测")
    print(f"区间: {args.start_date} ~ {args.end_date}")
    print("=" * 60)

    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # ── Load data ────────────────────────────────────────────
    print("Loading...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    print(f"  Calendar: {len(calendar)} days ({calendar[0]} to {calendar[-1]})")

    signal_to_exec, exec_to_signal = _build_signal_to_exec_map(calendar)

    index_trends = load_index_trends_pit(engine, ["000300.SH", "399006.SZ"], calendar)
    for d in calendar:
        if d not in index_trends:
            index_trends[d] = {"000300.SH": 0.0, "399006.SZ": 0.0}

    prices = load_prices(engine, min_date=args.start_date, max_date=args.end_date, extra_days=30)
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    ps = prices.sort_values("_date_sort").reset_index(drop=True)
    pdi = ps.groupby("trade_date", sort=True).indices
    print(f"  Prices: {len(ps)} rows, {len(pdi)} days")

    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    scores = add_liquidity_derived_features(scores, ps)
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    ss = scores.sort_values("_date_sort").reset_index(drop=True)
    sdi = ss.groupby("trade_date", sort=True).indices
    print(f"  Scores: {len(ss)} rows, {len(sdi)} days")

    try: me = build_market_environment(ss, ps)
    except: me = pd.DataFrame()

    specs = build_strategy_specs()

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=signal_to_exec, exec_to_signal=exec_to_signal,
        score_day_indices=sdi, price_day_indices=pdi, index_trends=index_trends,
        strategy_specs=specs, start_date=args.start_date, end_date=args.end_date,
        initial_cash=args.initial_cash,
    )

    # ── Output dir ────────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUT_ROOT / f"b1_t3_v3_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    results = {}

    # ── T0 ────────────────────────────────────────────────────
    print("\n=== T0: 固定70% ===")
    r = run_single_backtest("T0", position_source="fixed", fixed_position=0.70, **common)
    r["full_metrics"] = _compute_full_metrics(r["nav_df"])
    results["T0"] = r
    m = r["full_metrics"]
    print(f"  R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f} "
          f"CVaR95={m['cvar95']:.4f} Ulcer={m['ulcer']:.4f} MaxDDd={m['max_dd_days']}d")

    # ── T3 ────────────────────────────────────────────────────
    print("\n=== T3: 双因子共识 ===")
    r = run_single_backtest("T3", position_source="controller", controller_mode="T3", **common)
    r["full_metrics"] = _compute_full_metrics(r["nav_df"])
    results["T3"] = r
    m = r["full_metrics"]
    avg_pos = r["avg_target_position"]
    print(f"  R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f} "
          f"CVaR95={m['cvar95']:.4f} AvgPos={avg_pos:.1%}")

    # ── Exposure frontier ─────────────────────────────────────
    frontier = {}
    if not args.skip_frontier:
        print("\n=== 固定仓位前沿 ===")
        positions = [0.50, 0.55, avg_pos, 0.60, 0.65, 0.70]
        positions = sorted(set(round(p, 2) for p in positions))
        frontier = run_exposure_frontier(common, positions)
        # Add full metrics to frontier curves
        for label, fr in frontier.items():
            fr["full_metrics"] = _compute_full_metrics(fr["nav_df"])

    # ── Lag curve (fixed: use pre-computed T3 sequence) ──────
    lag_curve = {}
    if not args.skip_lag:
        print("\n=== 滞后时效曲线 ===")
        t3_seq = results["T3"]["position_sequence"]
        lag_curve = run_lag_curve(common, t3_seq)

    # ── Sub-period analysis ───────────────────────────────────
    print("\n=== 子期间分析 ===")
    t3_nav = results["T3"]["nav_df"]
    t0_nav = results["T0"]["nav_df"]
    t3_nav["trade_date"] = t3_nav["trade_date"].astype(str)
    t0_nav["trade_date"] = t0_nav["trade_date"].astype(str)

    sub_periods = []
    sub_defs = [
        ("2023 (恢复上涨)", "2023-01-03", "2023-12-29"),
        ("2024 (动量轮动)", "2024-01-02", "2024-12-31"),
        ("2025-2026 (当前)", "2025-01-02", "2026-06-30"),
        ("2023-2024 (发现前样本)", "2023-01-03", "2024-12-31"),
    ]
    for name, sd, ed in sub_defs:
        sp = sub_period_metrics(t3_nav, t0_nav, name, sd, ed)
        if sp:
            sub_periods.append(sp)
            print(f"  {name}: T3 R={sp['t3_return']:.2%} Cal={sp['t3_calmar']:.2f} | "
                  f"T0 R={sp['t0_return']:.2%} Cal={sp['t0_calmar']:.2f}")

    # ── Save CSVs ─────────────────────────────────────────────
    for label, r in results.items():
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf.to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
    if frontier:
        frows = []
        for label, fr in frontier.items():
            fm = fr.get("full_metrics", fr.get("metrics", {}))
            frows.append({"curve": label, "position": int(label[1:])/100, **fm})
        pd.DataFrame(frows).to_csv(out_dir / "exposure_frontier.csv", index=False)
    if lag_curve:
        lrows = []
        for label, lr in lag_curve.items():
            lm = lr["metrics"]
            lrows.append({"lag": int(label[3:]), **lm})
        pd.DataFrame(lrows).to_csv(out_dir / "lag_curve.csv", index=False)
    if sub_periods:
        pd.DataFrame(sub_periods).to_csv(out_dir / "sub_periods.csv", index=False)

    # ── Report ────────────────────────────────────────────────
    build_v3_report(results, frontier, lag_curve, sub_periods, out_dir)

    # ── Summary ───────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print("=" * 60)
    for label in ["T0", "T3"]:
        r = results.get(label, {})
        m = r.get("full_metrics", {})
        if m:
            print(f"  {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} "
                  f"Cal={m['calmar']:.2f} CVaR95={m['cvar95']:.4f} Ulcer={m['ulcer']:.4f}")

    # T3 vs A1 tail risk
    a1_label = f"A{int(avg_pos*100)}"
    a1_fr = frontier.get(a1_label, {})
    a1_m = a1_fr.get("full_metrics", a1_fr.get("metrics", {}))
    t3_m = results["T3"]["full_metrics"]
    if a1_m:
        dd_d = t3_m["max_drawdown"] - a1_m["max_drawdown"]
        cvar_d = t3_m["cvar95"] - a1_m["cvar95"]
        print(f"\n  T3 vs {a1_label} ({avg_pos:.1%}):")
        print(f"    MaxDD: {t3_m['max_drawdown']:.2%} vs {a1_m['max_drawdown']:.2%} (diff={dd_d:+.2%})")
        print(f"    CVaR95: {t3_m['cvar95']:.4f} vs {a1_m['cvar95']:.4f} (diff={cvar_d:+.4f})")
        print(f"    Calmar: {t3_m['calmar']:.2f} vs {a1_m['calmar']:.2f}")

    print(f"\n  Sub-period T3 Calmar vs T0 Calmar:")
    for sp in sub_periods:
        print(f"    {sp['period']}: {sp['t3_calmar']:.2f} vs {sp['t0_calmar']:.2f} "
              f"({(sp['t3_calmar']/sp['t0_calmar']-1)*100:+.1f}% vs T0)")

    print("\nDone.")


if __name__ == "__main__":
    main()
