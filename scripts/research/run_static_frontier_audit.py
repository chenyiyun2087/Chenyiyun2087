#!/usr/bin/env python3
"""
B1-T3 v4.0: P0 (固定仓位前沿一致性审计) + P1 (静态风险预算跨期间选择)

运行 A45-A70 每 1% 一档 (26条曲线)，全指标，跨6个子期间验证。
输出: 前沿审计报告、相邻仓位差异分析、跨期间稳定性矩阵。

Usage:
    python scripts/research/run_static_frontier_audit.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys
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
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_b1_t3_r2_validation import run_single_backtest

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def compute_all_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd_series = (nav - peak) / peak
    max_dd = float(np.min(dd_series))
    n = len(nav)
    ann_ret = float((1 + total_return) ** (252 / n) - 1) if n > 0 and nav[0] > 0 else 0.0
    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 else 0.0
    calmar = float(ann_ret / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    cvar95 = float(-np.mean(np.sort(daily_rets)[:max(1, int(n * 0.05))])) if n > 20 else 0.0
    ulcer = float(np.sqrt(np.mean(dd_series ** 2))) if n > 0 else 0.0

    # MaxDD duration & recovery
    trough_idx = int(np.argmin(dd_series))
    dd_start = trough_idx
    for i in range(trough_idx, -1, -1):
        if dd_series[i] > -0.001: dd_start = i + 1; break
    dd_end = trough_idx
    for i in range(trough_idx, n):
        if dd_series[i] > -0.001: dd_end = i; break
    max_dd_dur = dd_end - dd_start
    max_dd_rec = dd_end - trough_idx

    # Worst 5d, 10d, 20d
    w5 = float(np.min(nav[5:] / nav[:-5] - 1)) if n >= 10 else 0.0
    w10 = float(np.min(nav[10:] / nav[:-10] - 1)) if n >= 10 else 0.0
    w20 = float(np.min(nav[20:] / nav[:-20] - 1)) if n >= 20 else 0.0

    # Consecutive losing days (negative daily return streak)
    loss_streak = cur = 0
    for r in daily_rets:
        cur = cur + 1 if r < 0 else 0
        loss_streak = max(loss_streak, cur)

    # Avg position count
    avg_pos_count = float(nav_df["position_count"].mean()) if "position_count" in nav_df.columns else 0.0

    # Actual exposure
    avg_actual_exposure = 0.0
    if "equity" in nav_df.columns and "cash" in nav_df.columns:
        nav_df_copy = nav_df.copy()
        nav_df_copy["actual_exp"] = (nav_df_copy["equity"] - nav_df_copy["cash"]) / nav_df_copy["equity"].replace(0, np.nan)
        avg_actual_exposure = float(nav_df_copy["actual_exp"].mean())

    return {
        "total_return": round(total_return, 6), "annualized_return": round(ann_ret, 6),
        "max_drawdown": round(max_dd, 6), "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4), "volatility": round(vol, 6),
        "cvar95": round(cvar95, 6), "ulcer": round(ulcer, 6),
        "max_dd_duration": max_dd_dur, "max_dd_recovery": max_dd_rec,
        "worst_5d": round(w5, 6), "worst_10d": round(w10, 6), "worst_20d": round(w20, 6),
        "max_loss_streak": loss_streak,
        "avg_position_count": round(avg_pos_count, 1),
        "avg_actual_exposure": round(avg_actual_exposure, 4),
        "n_days": n,
    }


def sub_period_slice(nav_df: pd.DataFrame, sd: str, ed: str) -> pd.DataFrame:
    ndf = nav_df.copy()
    ndf["trade_date"] = ndf["trade_date"].astype(str)
    return ndf[(ndf["trade_date"] >= sd) & (ndf["trade_date"] <= ed)]


PERIODS = [
    ("2023", "2023-01-03", "2023-12-29"),
    ("2024", "2024-01-02", "2024-12-31"),
    ("2025", "2025-01-02", "2025-12-31"),
    ("2026H1", "2026-01-02", "2026-06-30"),
    ("2023-2024弱市", "2023-01-03", "2024-12-31"),
    ("2025-2026强势", "2025-01-02", "2026-06-30"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--step", type=int, default=1, help="Position step size in percent")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("P0+P1: 固定仓位前沿一致性审计 + 静态风险预算选择")
    print(f"区间: {args.start_date} ~ {args.end_date}, 步长: {args.step}%")
    print("=" * 60)

    # ── Data loading ────────────────────────────────────────
    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    cal = _build_calendar(engine, args.start_date, args.end_date)
    cal = sorted(set(cal))
    n_cal = len(cal)
    print(f"Calendar: {n_cal} days ({cal[0]} to {cal[-1]})")

    s2e, e2s = _build_signal_to_exec_map(cal)
    it_trends = load_index_trends_pit(engine, ["000300.SH", "399006.SZ"], cal)
    for d in cal:
        if d not in it_trends: it_trends[d] = {"000300.SH": 0.0, "399006.SZ": 0.0}

    prices = load_prices(engine, args.start_date, args.end_date, 30)
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    ps = prices.sort_values("_date_sort").reset_index(drop=True)
    pdi = ps.groupby("trade_date", sort=True).indices

    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    scores = add_liquidity_derived_features(scores, ps)
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    ss = scores.sort_values("_date_sort").reset_index(drop=True)
    sdi = ss.groupby("trade_date", sort=True).indices

    try: me = build_market_environment(ss, ps)
    except: me = pd.DataFrame()
    specs = build_strategy_specs()

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=cal,
        signal_to_exec=s2e, exec_to_signal=e2s, score_day_indices=sdi,
        price_day_indices=pdi, index_trends=it_trends, strategy_specs=specs,
        start_date=args.start_date, end_date=args.end_date,
        initial_cash=args.initial_cash,
    )

    # ── Output ───────────────────────────────────────────────
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"static_frontier_{ts_str}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    # ══════════════════════════════════════════════════════════
    # P0: Run A45-A70 at 1% increments
    # ══════════════════════════════════════════════════════════
    positions_pct = list(range(45, 71, args.step))
    print(f"\n=== P0: 运行 {len(positions_pct)} 条固定仓位曲线 (A45-A70) ===")

    frontier = {}
    for pct in positions_pct:
        pos = pct / 100.0
        label = f"A{pct}"
        print(f"  {label} ({pos:.0%})...", end=" ", flush=True)
        r = run_single_backtest(label, position_source="fixed", fixed_position=pos, **common)
        r["full_metrics"] = compute_all_metrics(r["nav_df"])
        frontier[label] = r
        m = r["full_metrics"]
        print(f"R={m.get('total_return',0):.2%} DD={m.get('max_drawdown',0):.2%} "
              f"Cal={m.get('calmar',0):.2f} Ulcer={m.get('ulcer',0):.4f} "
              f"ActExp={m.get('avg_actual_exposure',0):.1%}")

    # ══════════════════════════════════════════════════════════
    # P0: Adjacent-position discontinuity audit
    # ══════════════════════════════════════════════════════════
    print(f"\n=== P0: 相邻仓位一致性审计 ===")
    disc_rows = []
    for i in range(1, len(positions_pct)):
        p_prev = positions_pct[i-1]
        p_curr = positions_pct[i]
        m_prev = frontier[f"A{p_prev}"]["full_metrics"]
        m_curr = frontier[f"A{p_curr}"]["full_metrics"]
        ret_diff = m_curr["total_return"] - m_prev["total_return"]
        dd_diff = m_curr["max_drawdown"] - m_prev["max_drawdown"]
        cal_diff = m_curr["calmar"] - m_prev["calmar"]
        # Flag as anomaly if |ret diff| > 15% for 1% position change
        anomaly = abs(ret_diff) > 0.15
        disc_rows.append({
            "prev": f"A{p_prev}", "curr": f"A{p_curr}",
            "ret_diff": round(ret_diff, 4), "dd_diff": round(dd_diff, 4),
            "cal_diff": round(cal_diff, 4), "anomaly": anomaly,
        })
        flag = "⚠️ ANOMALY" if anomaly else ""
        print(f"  A{p_prev}→A{p_curr}: ΔRet={ret_diff:+.2%} ΔDD={dd_diff:+.2%} ΔCal={cal_diff:+.2f} {flag}")

    anomaly_count = sum(1 for d in disc_rows if d["anomaly"])
    print(f"\n  异常相邻档位数: {anomaly_count}/{len(disc_rows)}")
    pd.DataFrame(disc_rows).to_csv(out_dir / "adjacent_discontinuity_audit.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # P1: Cross-period stability matrix
    # ══════════════════════════════════════════════════════════
    print(f"\n=== P1: 跨期间稳定性矩阵 ===")
    candidates_pct = [50, 55, 60, 65, 70]

    # Full-period metrics for candidates
    print(f"\n{'仓位':<8} {'收益':>8} {'MaxDD':>8} {'Calmar':>6} {'CVaR95':>8} {'Ulcer':>8} {'DDdur':>6} {'最差5d':>8} {'最差10d':>8} {'连亏':>4}")
    print("-" * 85)
    for pct in candidates_pct:
        m = frontier[f"A{pct}"]["full_metrics"]
        print(f"{pct:>3}%    {m['total_return']:>7.2%} {m['max_drawdown']:>7.2%} {m['calmar']:>5.2f} "
              f"{m['cvar95']:>7.4f} {m['ulcer']:>7.4f} {m['max_dd_duration']:>5d} "
              f"{m['worst_5d']:>7.2%} {m['worst_10d']:>7.2%} {m['max_loss_streak']:>3d}")

    # Cross-period: compute metrics for each (period, candidate)
    cross = []
    for pname, sd, ed in PERIODS:
        for pct in candidates_pct:
            nav = frontier[f"A{pct}"]["nav_df"]
            sub = sub_period_slice(nav, sd, ed)
            if sub.empty: continue
            m = compute_all_metrics(sub)
            cross.append({
                "period": pname, "position_pct": pct,
                "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
                "calmar": m["calmar"], "cvar95": m["cvar95"], "ulcer": m["ulcer"],
                "max_dd_duration": m["max_dd_duration"], "worst_5d": m["worst_5d"],
                "n_days": m["n_days"],
            })

    cross_df = pd.DataFrame(cross)
    cross_df.to_csv(out_dir / "cross_period_stability.csv", index=False)

    # ── Stability ranking: how often is each position in top-3 per metric? ──
    print(f"\n  稳定性排名 (每个子期间每个指标的排名, 越低越好):")
    metrics_to_rank = ["total_return", "max_drawdown", "calmar", "cvar95", "ulcer"]
    rankings = {pct: {m: [] for m in metrics_to_rank} for pct in candidates_pct}
    for pname, _, _ in PERIODS:
        period_mask = cross_df["period"] == pname
        for metric in metrics_to_rank:
            # For max_drawdown, cvar95, ulcer: lower is better
            ascending = metric in ("max_drawdown", "cvar95", "ulcer", "max_dd_duration")
            period_data = cross_df[period_mask][["position_pct", metric]].dropna()
            if len(period_data) < 2: continue
            period_data = period_data.copy()
            period_data["rank"] = period_data[metric].rank(ascending=ascending)
            for _, row in period_data.iterrows():
                rankings[int(row["position_pct"])][metric].append(int(row["rank"]))

    # Average rank across all periods
    print(f"\n  {'仓位':<8} {'综合平均排名':>10} {'收益排名':>8} {'回撤排名':>8} {'Calmar排名':>8} {'CVaR排名':>8} {'Ulcer排名':>8}")
    print("  " + "-" * 70)
    stability_scores = {}
    for pct in candidates_pct:
        avg_ranks = {}
        for m in metrics_to_rank:
            vals = rankings[pct][m]
            avg_ranks[m] = np.mean(vals) if vals else 99
        overall = np.mean(list(avg_ranks.values()))
        stability_scores[pct] = overall
        print(f"  {pct:>3}%    {overall:>9.1f}      {avg_ranks['total_return']:>7.1f}  "
              f"{avg_ranks['max_drawdown']:>7.1f}  {avg_ranks['calmar']:>7.1f}  "
              f"{avg_ranks['cvar95']:>7.1f}  {avg_ranks['ulcer']:>7.1f}")

    # ── Selection recommendation ─────────────────────────────
    best_stable = min(stability_scores, key=stability_scores.get)
    print(f"\n  ✅ 跨期间最稳定仓位: {best_stable}% (综合排名 {stability_scores[best_stable]:.1f})")

    # ── Count periods where each candidate is in top-2 ──
    print(f"\n  各仓位在子期间中的胜出次数 (Calmar前2):")
    for pct in candidates_pct:
        wins = 0
        for pname, _, _ in PERIODS:
            period_data = cross_df[(cross_df["period"] == pname) & (cross_df["position_pct"].isin(candidates_pct))]
            if len(period_data) < 2: continue
            top2 = period_data.nlargest(2, "calmar")["position_pct"].values
            if pct in top2: wins += 1
        print(f"    {pct:>3}%: {wins}/{len(PERIODS)} 个子期间进入 Calmar 前2")

    # ── Save all NAVs ────────────────────────────────────────
    print(f"\n=== 保存数据 ===")
    summary_rows = []
    for pct in positions_pct:
        label = f"A{pct}"
        r = frontier[label]
        m = r["full_metrics"]
        summary_rows.append({"curve": label, "target_position": pct/100.0, **m})
        # Save NAV
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf.to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
    pd.DataFrame(summary_rows).to_csv(out_dir / "static_frontier_summary.csv", index=False)

    # ── Report ────────────────────────────────────────────────
    report = [
        "# P0+P1: 固定仓位前沿审计与静态风险预算选择",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"数据: {args.start_date} ~ {args.end_date} ({n_cal}交易日)",
        "",
        "## P0: 相邻仓位一致性审计",
        f"- 运行区间: A45-A70, 步长{args.step}%",
        f"- 异常相邻档位: {anomaly_count}/{len(disc_rows)}",
    ]

    if anomaly_count > 0:
        report.append("\n### ⚠️ 异常档位详情")
        for d in disc_rows:
            if d["anomaly"]:
                report.append(f"- {d['prev']}→{d['curr']}: ΔRet={d['ret_diff']:+.2%} ΔDD={d['dd_diff']:+.2%}")

    report += [
        "",
        "## P1: 跨期间稳定性排名",
        f"- 最稳定仓位: **{best_stable}%** (综合平均排名 {stability_scores[best_stable]:.1f})",
        "",
        "### 推荐",
        f"- 静态基础仓位候选: **{best_stable}%**",
        f"- 选择理由: 跨6个子期间综合排名最优",
        "- 注意: 此结果基于 2023-2026 历史数据, 具体选择需结合资金回撤容忍度",
        "",
        "## 全样本核心指标",
    ]
    for pct in candidates_pct:
        m = frontier[f"A{pct}"]["full_metrics"]
        report.append(f"- {pct}%: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} "
                      f"Cal={m['calmar']:.2f} CVaR95={m['cvar95']:.4f} Ulcer={m['ulcer']:.4f} "
                      f"DDdur={m['max_dd_duration']}d W5={m['worst_5d']:.2%}")

    (out_dir / "static_frontier_report.md").write_text("\n".join(report))

    # ── Final summary ────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL SELECTION")
    print("=" * 60)
    print(f"  推荐静态基础仓位: {best_stable}%")
    print(f"  综合跨期间排名: {stability_scores[best_stable]:.1f} (越低越好)")
    print(f"  报告: {out_dir}/static_frontier_report.md")
    print(f"  数据: {out_dir}/")
    print("\nDone.")


if __name__ == "__main__":
    main()
