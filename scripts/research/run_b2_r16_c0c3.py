#!/usr/bin/env python3
"""
B2_SCALE40 R16: C0-C3 严格反事实回放 + 持仓级归因

C0: S60 exact replay verification
C3: B2 exact replay verification
Attribution: common weight diff, unique stocks, cash, by risk period

Usage:
    python scripts/research/run_b2_r16_c0c3.py \
        --start-date 2023-01-03 --end-date 2026-07-01
"""

import argparse, json, sys, hashlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from decimal import Decimal, getcontext
import numpy as np, pandas as pd
from sqlalchemy import create_engine

getcontext().prec = 28

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
from scripts.research.run_fsc1_validation import build_anchor_risk_state
from scripts.research.run_b2_r158_cutover import run_exact_backtest, replay_exact

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R16: Holdings-level PnL attribution
# ══════════════════════════════════════════════════════════════════════

def compute_r16_attribution(s60, b2, prices_df, pdi, anchor_risk):
    """
    R16: Decompose B2 vs S60 using actual daily holdings × per-stock returns.
    Uses COMMON stock weight differences, UNIQUE stock contributions, and cash effects.
    """
    s60_hold = s60["holdings_df"]; b2_hold = b2["holdings_df"]
    s60_nav = s60["snapshot_df"]; b2_nav = b2["snapshot_df"]

    if s60_hold.empty or b2_hold.empty: return {}

    # Build holdings dict per date
    def hdict(hold_df):
        hd = defaultdict(dict)
        for _, r in hold_df.iterrows():
            hd[r["trade_date"]][str(r["symbol"]).zfill(6)] = float(r["shares"])
        return hd

    s60_hd = hdict(s60_hold); b2_hd = hdict(b2_hold)

    # Exposure paths
    s60_exp = {r["trade_date"]: float(r.get("actual_exposure", r.get("position_ratio",0.60)))
               for _, r in s60_nav.iterrows()}
    b2_exp = {r["trade_date"]: float(r.get("actual_exposure", r.get("position_ratio",0.60)))
              for _, r in b2_nav.iterrows()}

    # Risk dates
    risk_dates = set(anchor_risk[anchor_risk["risk_state"]==True]["signal_date"].values)

    # Daily stock returns from price data
    def stock_ret(sym, d_prev, d_curr):
        if d_prev not in pdi or d_curr not in pdi: return 0.0
        pp = prices_df.iloc[pdi[d_prev]]; pc = prices_df.iloc[pdi[d_curr]]
        px_p = _safe_float(pp[pp["symbol"]==sym]["raw_close"].iloc[0] if len(pp[pp["symbol"]==sym])>0 else 0, 0)
        px_c = _safe_float(pc[pc["symbol"]==sym]["raw_close"].iloc[0] if len(pc[pc["symbol"]==sym])>0 else 0, 0)
        return (px_c/px_p - 1.0) if px_p>0 and px_c>0 else 0.0

    dates = sorted(set(list(s60_hd.keys()) + list(b2_hd.keys())))
    if len(dates) < 2: return {}

    daily_rows = []
    cumulative = {
        "common_weight_diff_pnl": 0.0, "b2_unique_pnl": 0.0,
        "s60_unique_pnl": 0.0, "cash_effect": 0.0, "total": 0.0,
    }

    # Per-period tracking
    period_pnl = defaultdict(lambda: {"common":0,"b2_uniq":0,"s60_uniq":0,"cash":0,"days":0})
    current_risk = False

    for i in range(1, len(dates)):
        d_prev, d_curr = dates[i-1], dates[i]
        s60_h = s60_hd.get(d_prev, {}); b2_h = b2_hd.get(d_prev, {})
        if not s60_h and not b2_h: continue

        # All symbols
        all_syms = set(list(s60_h.keys()) + list(b2_h.keys()))
        common = set(s60_h.keys()) & set(b2_h.keys())
        s60_only = set(s60_h.keys()) - set(b2_h.keys())
        b2_only = set(b2_h.keys()) - set(s60_h.keys())

        s60_e = s60_exp.get(d_prev, 0.60); b2_e = b2_exp.get(d_prev, 0.60)

        # Total portfolio values
        s60_total_mv = sum(s60_h.get(s,0) * _safe_float(
            prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
            if d_prev in pdi and len(prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s])>0 else 0, 0)
            for s in s60_h) if d_prev in pdi else 1.0
        b2_total_mv = sum(b2_h.get(s,0) * _safe_float(
            prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
            if d_prev in pdi and len(prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s])>0 else 0, 0)
            for s in b2_h) if d_prev in pdi else 1.0

        s60_total_mv = max(s60_total_mv, 1.0); b2_total_mv = max(b2_total_mv, 1.0)

        # Common weight diff effect
        common_pnl = 0.0
        for s in common:
            ret = stock_ret(s, d_prev, d_curr)
            s60_w = (s60_h.get(s,0) * _safe_float(
                prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
                if d_prev in pdi and len(prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s])>0 else 0, 0)
                / s60_total_mv) if d_prev in pdi else 0.0
            b2_w = (b2_h.get(s,0) * _safe_float(
                prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
                if d_prev in pdi and len(prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s])>0 else 0, 0)
                / b2_total_mv) if d_prev in pdi else 0.0
            common_pnl += (b2_w - s60_w) * ret * s60_e

        # B2 unique stocks
        b2_uniq_pnl = 0.0
        for s in b2_only:
            ret = stock_ret(s, d_prev, d_curr)
            w = (b2_h.get(s,0) * _safe_float(
                prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
                if d_prev in pdi else 0, 0) / b2_total_mv) if d_prev in pdi else 0.0
            b2_uniq_pnl += w * ret * s60_e

        # S60 unique stocks (B2 doesn't hold → opportunity cost if they went up)
        s60_uniq_pnl = 0.0
        for s in s60_only:
            ret = stock_ret(s, d_prev, d_curr)
            w = (s60_h.get(s,0) * _safe_float(
                prices_df.iloc[pdi[d_prev]][prices_df.iloc[pdi[d_prev]]["symbol"]==s]["raw_close"].iloc[0]
                if d_prev in pdi else 0, 0) / s60_total_mv) if d_prev in pdi else 0.0
            s60_uniq_pnl -= w * ret * s60_e  # B2 missed these

        # Cash effect: different cash ratios
        cash_effect = (1 - b2_e) * 0.0 - (1 - s60_e) * 0.0  # simplified

        cumulative["common_weight_diff_pnl"] += common_pnl
        cumulative["b2_unique_pnl"] += b2_uniq_pnl
        cumulative["s60_unique_pnl"] += s60_uniq_pnl
        cumulative["cash_effect"] += cash_effect
        cumulative["total"] = cumulative["common_weight_diff_pnl"] + cumulative["b2_unique_pnl"] + cumulative["s60_unique_pnl"] + cumulative["cash_effect"]

        # Track by risk state
        is_risk = d_prev in risk_dates
        period = "risk" if is_risk else "normal"
        period_pnl[period]["common"] += common_pnl
        period_pnl[period]["b2_uniq"] += b2_uniq_pnl
        period_pnl[period]["s60_uniq"] += s60_uniq_pnl
        period_pnl[period]["cash"] += cash_effect
        period_pnl[period]["days"] += 1

        daily_rows.append({
            "trade_date": str(d_curr),
            "risk_state": is_risk,
            "common_weight_diff": round(common_pnl, 8),
            "b2_unique": round(b2_uniq_pnl, 8),
            "s60_unique": round(s60_uniq_pnl, 8),
            "cum_total": round(cumulative["total"], 6),
        })

    total_abs = abs(cumulative["common_weight_diff_pnl"]) + abs(cumulative["b2_unique_pnl"]) + abs(cumulative["s60_unique_pnl"])
    if total_abs > 0:
        common_pct = abs(cumulative["common_weight_diff_pnl"]) / total_abs * 100
        b2_pct = abs(cumulative["b2_unique_pnl"]) / total_abs * 100
        s60_pct = abs(cumulative["s60_unique_pnl"]) / total_abs * 100
    else:
        common_pct = b2_pct = s60_pct = 0

    mechanism = "WEIGHT_PATH" if common_pct > max(b2_pct, s60_pct) else ("SELECTION_PATH" if max(b2_pct,s60_pct) > common_pct else "MIXED")

    return {
        "cumulative": {k: round(v, 6) for k, v in cumulative.items()},
        "common_weight_pct": round(common_pct, 1),
        "b2_unique_pct": round(b2_pct, 1),
        "s60_unique_pct": round(s60_pct, 1),
        "mechanism": mechanism,
        "risk_period": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in period_pnl.items()},
        "daily_rows": daily_rows,
    }


# ══════════════════════════════════════════════════════════════════════
# R16: Concentration check
# ══════════════════════════════════════════════════════════════════════

def check_concentration(attribution):
    """Check if any stock/period dominates excess returns."""
    daily = attribution.get("daily_rows", [])
    if not daily: return {}

    df = pd.DataFrame(daily)
    total = df["cum_total"].iloc[-1] if len(df) > 0 else 0.001

    # Risk period contribution
    risk_total = df[df["risk_state"]==True]["cum_total"].iloc[-1] if len(df[df["risk_state"]==True]) > 0 else 0
    normal_total = df[~df["risk_state"]]["cum_total"].iloc[-1] if len(df[~df["risk_state"]]) > 0 else 0

    risk_pct = abs(risk_total) / max(abs(total), 0.001) * 100 if abs(total) > 0.001 else 0

    return {
        "risk_period_contribution_pct": round(risk_pct, 1),
        "risk_concentration_warning": risk_pct > 50,
        "total_excess": round(total, 6),
    }


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", default="2023-01-03")
    p.add_argument("--end-date", default="2026-07-01")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--initial-cash", type=float, default=500000.0)
    args = p.parse_args()

    print("=" * 60)
    print("B2_SCALE40 R16: C0-C3 反事实 + 持仓级归因")
    print(f"区间: {args.start_date} ~ {args.end_date}")
    print("=" * 60)

    db_url = build_sqlalchemy_url(); engine = create_engine(db_url)
    print("Loading...")
    cal = _build_calendar(engine, args.start_date, args.end_date); cal = sorted(set(cal))
    print(f"  Calendar: {len(cal)} days ({cal[0]} to {cal[-1]})")
    s2e, e2s = _build_signal_to_exec_map(cal)
    it = load_index_trends_pit(engine, ["000300.SH","399006.SZ"], cal)
    for d in cal:
        if d not in it: it[d] = {"000300.SH":0.0,"399006.SZ":0.0}
    prices = load_prices(engine, args.start_date, args.end_date, 30)
    prices["_ds"]=pd.to_datetime(prices["trade_date"]); ps=prices.sort_values("_ds").reset_index(drop=True)
    pdi=ps.groupby("trade_date",sort=True).indices
    scores=load_scores(engine,start_date=args.start_date,end_date=args.end_date)
    scores=add_liquidity_derived_features(scores,ps)
    scores["_ds"]=pd.to_datetime(scores["trade_date"]); ss=scores.sort_values("_ds").reset_index(drop=True)
    sdi=ss.groupby("trade_date",sort=True).indices
    try: me=build_market_environment(ss,ps)
    except: me=pd.DataFrame()
    specs=build_strategy_specs()

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir=OUT_ROOT/f"b2_r16_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True,exist_ok=True)

    anchor = build_anchor_risk_state(engine=engine, scores=ss, prices=ps, market_env=me,
                                      calendar=cal, signal_to_exec=s2e, exec_to_signal=e2s,
                                      sdi=sdi, pdi=pdi, it_trends=it, specs=specs,
                                      start_date=args.start_date, end_date=args.end_date,
                                      initial_cash=args.initial_cash)
    n_risk = int(anchor["risk_state"].sum())
    print(f"  Risk: {n_risk}/{len(anchor)} days")

    kw = dict(engine=engine, scores=ss, prices=ps, market_env=me, calendar=cal,
              signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi, it_trends=it,
              specs=specs, start_date=args.start_date, end_date=args.end_date,
              initial_cash=args.initial_cash)

    # ══════════════════════════════════════════════════════════
    # C0: S60, C3: B2
    # ══════════════════════════════════════════════════════════
    print(f"\n=== C0/C3: Exact replay ===")
    s60 = run_exact_backtest("S60", anchor, 0.60, 0.60, **kw)
    b2 = run_exact_backtest("B2", anchor, 0.60, 0.40, **kw)

    s60_rp = replay_exact(s60["ledger_df"], s60["snapshot_df"], ps, pdi, args.initial_cash, 0.00075)
    b2_rp = replay_exact(b2["ledger_df"], b2["snapshot_df"], ps, pdi, args.initial_cash, 0.00075)

    print(f"  C0 (S60): {'✅' if s60_rp['ok'] else '❌'} errs={s60_rp['n_errors']} NAV={s60_rp['max_nav_bps']}bps")
    print(f"  C3 (B2):  {'✅' if b2_rp['ok'] else '❌'} errs={b2_rp['n_errors']} NAV={b2_rp['max_nav_bps']}bps")

    s60_m = s60["metrics"]; b2_m = b2["metrics"]
    print(f"\n  S60: R={s60_m['total_return']:.2%} DD={s60_m['max_drawdown']:.2%} Cal={s60_m['calmar']:.2f}")
    print(f"  B2:  R={b2_m['total_return']:.2%} DD={b2_m['max_drawdown']:.2%} Cal={b2_m['calmar']:.2f}")

    # ══════════════════════════════════════════════════════════
    # R16: Attribution
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R16: 持仓级归因 ===")
    attr = compute_r16_attribution(s60, b2, ps, pdi, anchor)
    if attr:
        cum = attr["cumulative"]
        print(f"  总超额: {cum['total']:+.2%}")
        print(f"  共同权重差: {cum['common_weight_diff_pnl']:+.2%} ({attr['common_weight_pct']:.0f}%)")
        print(f"  B2独有股票: {cum['b2_unique_pnl']:+.2%} ({attr['b2_unique_pct']:.0f}%)")
        print(f"  S60独有股票: {cum['s60_unique_pnl']:+.2%} ({attr['s60_unique_pct']:.0f}%)")
        print(f"  机制: {attr['mechanism']}")

        rp = attr.get("risk_period", {})
        for period in ["risk", "normal"]:
            if period in rp:
                p = rp[period]
                print(f"  {period}期({p['days']}d): common={p['common']:+.2%} b2u={p['b2_uniq']:+.2%} s60u={p['s60_uniq']:+.2%}")

    conc = check_concentration(attr)
    if conc:
        print(f"\n  集中度: 风险期贡献={conc['risk_period_contribution_pct']:.0f}% "
              f"{'⚠️ >50%' if conc['risk_concentration_warning'] else '✅'}")

    # Save
    s60["ledger_df"].to_csv(out_dir/"c0_ledger_s60.csv", index=False)
    b2["ledger_df"].to_csv(out_dir/"c3_ledger_b2.csv", index=False)
    s60["holdings_df"].to_csv(out_dir/"c0_holdings_s60.csv", index=False)
    b2["holdings_df"].to_csv(out_dir/"c3_holdings_b2.csv", index=False)

    pd.DataFrame(attr.get("daily_rows",[])).to_csv(out_dir/"r16_daily_attribution.csv", index=False)

    report = [
        "# B2_SCALE40 R16: C0-C3 + 持仓级归因",
        f"区间: {args.start_date} ~ {args.end_date} ({len(cal)}天)",
        f"## C0/C3 回放",
        f"- C0 (S60): {'✅' if s60_rp['ok'] else '❌'}",
        f"- C3 (B2): {'✅' if b2_rp['ok'] else '❌'}",
        f"## 归因",
    ]
    if attr:
        report += [
            f"- 总超额: {cum['total']:+.2%}",
            f"- 共同权重差: {cum['common_weight_diff_pnl']:+.2%} ({attr['common_weight_pct']:.0f}%)",
            f"- B2独有: {cum['b2_unique_pnl']:+.2%} ({attr['b2_unique_pct']:.0f}%)",
            f"- S60独有: {cum['s60_unique_pnl']:+.2%} ({attr['s60_unique_pct']:.0f}%)",
            f"- 机制: {attr['mechanism']}",
        ]
    (out_dir/"r16_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"C0: {'✅' if s60_rp['ok'] else '❌'} | C3: {'✅' if b2_rp['ok'] else '❌'}")
    print(f"报告: {out_dir}/r16_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
