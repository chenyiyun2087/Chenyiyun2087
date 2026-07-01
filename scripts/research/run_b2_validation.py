#!/usr/bin/env python3
"""
B2_SCALE40 R1-R3: 等暴露基线 + 收益归因 + 事件验证

Usage:
    python scripts/research/run_b2_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys, hashlib
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
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_fsc1_validation import build_anchor_risk_state
from scripts.research.run_fsc1_r7_validation import run_b2_backtest

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _compute_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns: return {}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd = float(np.min((nav - peak) / peak))
    n = len(nav)
    ann_ret = float((1 + total_return) ** (252 / n) - 1) if n > 0 and nav[0] > 0 else 0.0
    daily_ret = np.diff(nav) / nav[:-1] if n > 1 else np.array([0])
    vol = float(np.std(daily_ret) * np.sqrt(252)) if len(daily_ret) > 1 else 0.0
    cvar95 = float(-np.mean(np.sort(daily_ret)[:max(1, int(n * 0.05))])) if n > 20 else 0.0
    ulcer = float(np.sqrt(np.mean(((nav - np.maximum.accumulate(nav)) / np.maximum.accumulate(nav)) ** 2)))
    calmar = float(ann_ret / abs(dd)) if abs(dd) > 0 else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 else 0.0

    # DD duration
    dd_series = (nav - np.maximum.accumulate(nav)) / np.maximum.accumulate(nav)
    trough = int(np.argmin(dd_series))
    dd_start = trough
    for i in range(trough, -1, -1):
        if dd_series[i] > -0.001: dd_start = i + 1; break
    dd_end = trough
    for i in range(trough, n):
        if dd_series[i] > -0.001: dd_end = i; break

    return {"total_return": round(total_return, 6), "max_drawdown": round(dd, 6),
            "calmar": round(calmar, 4), "sharpe": round(sharpe, 4),
            "cvar95": round(cvar95, 6), "ulcer": round(ulcer, 6),
            "max_dd_duration": dd_end - dd_start, "n_days": n}


# ══════════════════════════════════════════════════════════════════════
# R2: Return decomposition B2 vs S60
# ══════════════════════════════════════════════════════════════════════

def decompose_b2_vs_s60(s60_nav: pd.DataFrame, b2_nav: pd.DataFrame,
                         risk_seq: list) -> pd.DataFrame:
    """Split B2 vs S60 daily return into exposure, selection, cost, cash components."""
    if s60_nav.empty or b2_nav.empty: return pd.DataFrame()

    s60 = s60_nav.copy()
    b2 = b2_nav.copy()

    # Ensure date alignment
    s60["_dt"] = s60["trade_date"].astype(str)
    b2["_dt"] = b2["trade_date"].astype(str)

    rows = []
    for i in range(1, min(len(s60), len(b2))):
        s60_prev_nav = float(s60.iloc[i-1]["nav"])
        s60_curr_nav = float(s60.iloc[i]["nav"])
        b2_prev_nav = float(b2.iloc[i-1]["nav"])
        b2_curr_nav = float(b2.iloc[i]["nav"])

        s60_ret = s60_curr_nav / s60_prev_nav - 1.0 if s60_prev_nav > 0 else 0.0
        b2_ret = b2_curr_nav / b2_prev_nav - 1.0 if b2_prev_nav > 0 else 0.0
        total_delta = b2_ret - s60_ret

        s60_exp = float(s60.iloc[i-1].get("actual_exposure", s60.iloc[i-1].get("position_ratio", 0.60)))
        b2_exp = float(b2.iloc[i-1].get("actual_exposure", b2.iloc[i-1].get("position_ratio", 0.60)))
        exp_delta_pnl = s60_ret * (b2_exp - s60_exp) / max(s60_exp, 0.01)

        # Selection: residual after exposure effect
        selection_pnl = total_delta - exp_delta_pnl

        in_risk = False
        if i - 1 < len(risk_seq): in_risk = risk_seq[i-1]

        rows.append({
            "trade_date": s60.iloc[i]["trade_date"],
            "risk_state": in_risk,
            "s60_return": round(s60_ret, 6), "b2_return": round(b2_ret, 6),
            "total_delta": round(total_delta, 6),
            "s60_exposure": round(s60_exp, 4), "b2_exposure": round(b2_exp, 4),
            "exposure_pnl_delta": round(exp_delta_pnl, 6),
            "selection_cost_pnl_delta": round(selection_pnl, 6),
        })

    df = pd.DataFrame(rows)
    if df.empty: return df

    # Cumulative
    df["cum_exposure"] = df["exposure_pnl_delta"].cumsum()
    df["cum_selection"] = df["selection_cost_pnl_delta"].cumsum()
    df["cum_total"] = df["total_delta"].cumsum()

    # Split by risk state
    risk_mask = df["risk_state"] == True
    normal_mask = ~risk_mask

    return df


# ══════════════════════════════════════════════════════════════════════
# R3: Risk event analysis
# ══════════════════════════════════════════════════════════════════════

def analyze_risk_events(risk_df: pd.DataFrame, b2_nav: pd.DataFrame,
                         s60_nav: pd.DataFrame) -> list:
    """R3: Find NORMAL→RISK transitions, compute per-event metrics."""
    risk_seq = [bool(r) for r in risk_df["risk_state"].values]
    signal_dates = risk_df["signal_date"].values

    # Find transitions
    events = []
    for i in range(1, len(risk_seq)):
        if risk_seq[i] and not risk_seq[i-1]:
            # NORMAL → RISK transition
            start_sd = signal_dates[i]
            # Find RISK → NORMAL
            end_sd = None
            for j in range(i+1, len(risk_seq)):
                if not risk_seq[j]:
                    end_sd = signal_dates[j]
                    break
            events.append({
                "start_signal_date": start_sd,
                "end_signal_date": end_sd,
                "start_idx": i, "end_idx": j if end_sd else len(risk_seq)-1,
                "duration": (j - i) if end_sd else (len(risk_seq) - i),
            })

    if len(events) <= 1:
        return []

    # Compute per-event metrics
    event_results = []
    s60_nav_copy = s60_nav.copy(); s60_nav_copy["_d"] = s60_nav_copy["trade_date"].astype(str)
    b2_nav_copy = b2_nav.copy(); b2_nav_copy["_d"] = b2_nav_copy["trade_date"].astype(str)

    for ei, evt in enumerate(events):
        sd_str = str(evt["start_signal_date"])
        ed_str = str(evt["end_signal_date"]) if evt["end_signal_date"] else None

        # Find nav rows for this event window
        s60_sub = s60_nav_copy[s60_nav_copy["_d"] >= sd_str]
        b2_sub = b2_nav_copy[b2_nav_copy["_d"] >= sd_str]
        if ed_str:
            s60_sub = s60_sub[s60_sub["_d"] <= ed_str]
            b2_sub = b2_sub[b2_sub["_d"] <= ed_str]
        s60_sub = s60_sub.head(evt["duration"] + 30)
        b2_sub = b2_sub.head(evt["duration"] + 30)

        if s60_sub.empty or b2_sub.empty: continue

        s60_m = _compute_metrics(s60_sub)
        b2_m = _compute_metrics(b2_sub)

        b2_avg_exp = float(b2_sub["actual_exposure"].mean()) if "actual_exposure" in b2_sub.columns else 0.0
        s60_avg_exp = float(s60_sub["actual_exposure"].mean()) if "actual_exposure" in s60_sub.columns else 0.0

        event_results.append({
            "event_id": ei + 1,
            "start_date": sd_str,
            "end_date": ed_str or "ongoing",
            "duration_days": evt["duration"],
            "b2_return": b2_m["total_return"],
            "s60_return": s60_m["total_return"],
            "b2_maxdd": b2_m["max_drawdown"],
            "s60_maxdd": s60_m["max_drawdown"],
            "b2_calmar": b2_m["calmar"],
            "s60_calmar": s60_m["calmar"],
            "b2_avg_exposure": round(b2_avg_exp, 4),
            "s60_avg_exposure": round(s60_avg_exp, 4),
            "b2_better": b2_m["calmar"] > s60_m["calmar"],
        })

    return event_results


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("B2_SCALE40 R1-R3: 等暴露基线 + 收益归因 + 事件验证")
    print("=" * 60)

    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    print("Loading data...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    s2e, e2s = _build_signal_to_exec_map(calendar)
    it_trends = load_index_trends_pit(engine, ["000300.SH", "399006.SZ"], calendar)
    for d in calendar:
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

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"b2_validation_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
    )

    # ── Anchor risk state ─────────────────────────────────────
    print("\n=== Anchor risk state ===")
    anchor_risk = build_anchor_risk_state(**common)
    n_risk = int(anchor_risk["risk_state"].sum())
    print(f"  {n_risk}/{len(anchor_risk)} risk days")

    # ══════════════════════════════════════════════════════════
    # R1: Fine-grained static baselines + B2 + S60
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R1: 等暴露静态基线 ===")
    results = {}

    # Static range: 20% to 60%, with fine grain around ~30%
    static_positions = list(range(20, 40, 2)) + list(range(40, 65, 5))
    for pos_pct in static_positions:
        label = f"STATIC{pos_pct}"
        r = run_b2_backtest(label, anchor_risk, pos_pct/100, pos_pct/100, **common)
        results[label] = r

    # S60
    r = run_b2_backtest("S60", anchor_risk, 0.60, 0.60, **common)
    results["S60"] = r
    s60_m = r["metrics"]

    # B2
    r = run_b2_backtest("B2", anchor_risk, 0.60, 0.40, **common)
    results["B2"] = r
    b2_m = r["metrics"]
    b2_avg_exp = float(r["nav_df"]["actual_exposure"].mean())
    print(f"\n  S60: R={s60_m['total_return']:.2%} DD={s60_m['max_drawdown']:.2%} Cal={s60_m['calmar']:.2f}")
    print(f"  B2:  R={b2_m['total_return']:.2%} DD={b2_m['max_drawdown']:.2%} Cal={b2_m['calmar']:.2f} AvgExp={b2_avg_exp:.1%}")

    # Find closest static
    all_static = {p: results[f"STATIC{p}"]["metrics"] for p in static_positions}
    closest_pct = min(static_positions, key=lambda p: abs(p/100 - b2_avg_exp))
    closest_m = all_static[closest_pct]

    print(f"\n  最近等暴露静态: STATIC{closest_pct} (R={closest_m['total_return']:.2%} Cal={closest_m['calmar']:.2f})")

    calmar_delta = b2_m["calmar"] - closest_m["calmar"]
    dd_delta = b2_m["max_drawdown"] - closest_m["max_drawdown"]
    cvar_delta = b2_m["cvar95"] - closest_m["cvar95"]
    ulcer_delta = b2_m["ulcer"] - closest_m["ulcer"]

    checks = [
        ("Calmar ≥ +0.10", calmar_delta >= 0.10, f"{calmar_delta:+.2f}"),
        ("MaxDD不更差", dd_delta <= 0.01, f"B2={b2_m['max_drawdown']:.2%} Static={closest_m['max_drawdown']:.2%}"),
        ("CVaR95不更差", cvar_delta <= 0.005, f"{cvar_delta:+.4f}"),
        ("Ulcer不更差", ulcer_delta <= 0.01, f"{ulcer_delta:+.4f}"),
    ]
    for name, passed, val in checks:
        print(f"  {'✅' if passed else '❌'} {name}: {val}")

    # Print full static frontier
    print(f"\n  完整静态前沿:")
    print(f"  {'仓位':<10} {'收益':>8} {'MaxDD':>8} {'Calmar':>6} {'CVaR95':>8} {'Ulcer':>8} {'AvgExp':>8}")
    for pos_pct in static_positions:
        m = all_static[pos_pct]
        avg_e = float(results[f"STATIC{pos_pct}"]["nav_df"]["actual_exposure"].mean())
        print(f"  {pos_pct:>3}%     {m['total_return']:>7.2%} {m['max_drawdown']:>7.2%} "
              f"{m['calmar']:>5.2f} {m['cvar95']:>7.4f} {m['ulcer']:>7.4f} {avg_e:>7.1%}")

    # ══════════════════════════════════════════════════════════
    # R2: Return decomposition
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R2: 收益归因 B2 vs S60 ===")
    risk_seq = [bool(r) for r in anchor_risk["risk_state"].values]
    s60_nav_df = results["S60"]["nav_df"]
    b2_nav_df = results["B2"]["nav_df"]

    decomp = decompose_b2_vs_s60(s60_nav_df, b2_nav_df, risk_seq)
    if not decomp.empty:
        decomp.to_csv(out_dir / "b2_return_attribution.csv", index=False)

        total_delta = float(decomp["total_delta"].sum())
        exp_contrib = float(decomp["exposure_pnl_delta"].sum())
        sel_contrib = float(decomp["selection_cost_pnl_delta"].sum())

        # Split by risk state
        risk_decomp = decomp[decomp["risk_state"] == True]
        normal_decomp = decomp[~decomp["risk_state"]]

        risk_exp = float(risk_decomp["exposure_pnl_delta"].sum()) if not risk_decomp.empty else 0.0
        normal_exp = float(normal_decomp["exposure_pnl_delta"].sum()) if not normal_decomp.empty else 0.0
        risk_sel = float(risk_decomp["selection_cost_pnl_delta"].sum()) if not risk_decomp.empty else 0.0

        n_risk_days = len(risk_decomp)
        n_normal_days = len(normal_decomp)

        print(f"  累计超额收益: {total_delta:+.2%}")
        print(f"    暴露PnL贡献: {exp_contrib:+.2%}")
        print(f"    选股/成本/现金: {sel_contrib:+.2%}")
        print(f"  风险日(N={n_risk_days}): 暴露PnL={risk_exp:+.2%} 选股={risk_sel:+.2%}")
        print(f"  正常日(N={n_normal_days}): 暴露PnL={normal_exp:+.2%}")

    # ══════════════════════════════════════════════════════════
    # R3: Risk event analysis
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R3: 风险事件验证 ===")
    events = analyze_risk_events(anchor_risk, b2_nav_df, s60_nav_df)
    print(f"  风险进入事件: {len(events)}")
    n_effective = 0
    total_excess = 0
    for evt in events:
        b2_better = "✅" if evt["b2_better"] else "❌"
        excess = evt["b2_return"] - evt["s60_return"]
        total_excess += abs(excess)
        print(f"    事件{evt['event_id']} ({evt['start_date']}, {evt['duration_days']}d): "
              f"B2 R={evt['b2_return']:.2%} DD={evt['b2_maxdd']:.2%} vs "
              f"S60 R={evt['s60_return']:.2%} DD={evt['s60_maxdd']:.2%} "
              f"B2_Exp={evt['b2_avg_exposure']:.1%} {b2_better}")
        if evt["b2_better"]: n_effective += 1

    if events:
        n_eff = n_effective
        print(f"\n  有效事件: {n_eff}/{len(events)} (需≥2/3)")
        # Concentration check
        for evt in events:
            excess = abs(evt["b2_return"] - evt["s60_return"])
            pct = excess / max(total_excess, 0.001) * 100
            if pct > 50:
                print(f"  ⚠️ 事件{evt['event_id']}贡献{pct:.0f}%超额收益 (>50%)")

    pd.DataFrame(events).to_csv(out_dir / "b2_risk_events.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # Save & Report
    # ══════════════════════════════════════════════════════════
    for label, r in results.items():
        r["nav_df"].to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)

    summary = []
    for label, r in results.items():
        m = r["metrics"]
        avg_e = float(r["nav_df"]["actual_exposure"].mean())
        summary.append({"curve": label, "avg_actual_exposure": round(avg_e, 4), **m})
    pd.DataFrame(summary).to_csv(out_dir / "b2_summary.csv", index=False)

    report = [
        "# B2_SCALE40 R1-R3 验证报告",
        f"## R1: 等暴露静态基线",
        f"- B2 平均暴露: {b2_avg_exp:.1%}",
        f"- 最近静态: STATIC{closest_pct}",
        f"- Calmar差: {calmar_delta:+.2f} {'✅' if calmar_delta >= 0.10 else '❌'}",
        f"- MaxDD差: {dd_delta:+.2%}",
        "",
        "## R2: 收益归因",
    ]
    if not decomp.empty:
        report += [
            f"- 累计超额: {total_delta:+.2%}",
            f"- 暴露贡献: {exp_contrib:+.2%}",
            f"- 选股/成本/现金: {sel_contrib:+.2%}",
            f"- 风险日暴露PnL: {risk_exp:+.2%}",
            f"- 正常日暴露PnL: {normal_exp:+.2%}",
        ]

    report += [
        "",
        "## R3: 风险事件",
        f"- 事件数: {len(events)}",
        f"- 有效事件: {n_eff}/{len(events)}",
    ]
    (out_dir / "b2_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"B2 vs STATIC{closest_pct}: Calmar {calmar_delta:+.2f}")
    print(f"R1 checks: {sum(1 for _,p,_ in checks if p)}/{len(checks)}")
    print(f"报告: {out_dir}/b2_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
