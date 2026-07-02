#!/usr/bin/env python3
"""
B2_SCALE40 R16.1-R16.2: Exact C0-C3 + Shapley attribution

R16.1: Capture security paths, run 4 counterfactuals through unified engine
R16.2: Shapley decomposition: exposure vs security path contribution

Usage:
    python scripts/research/run_b2_r161_shapley.py \
        --start-date 2023-01-03 --end-date 2026-07-01
"""

import argparse, json, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from decimal import Decimal, getcontext
import numpy as np, pandas as pd
from sqlalchemy import create_engine

getcontext().prec = 28; D = Decimal

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

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R16.1: Unified counterfactual engine
# ══════════════════════════════════════════════════════════════════════

def run_counterfactual(label, anchor_risk, target_normal, target_risk,
                        cost_rate=0.00075, slip_rate=0.0, **kw):
    """R16.1: Run backtest with given exposure path. Returns full state."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    risk_lookup = {row["signal_date"]: bool(row["risk_state"])
                   for _, row in anchor_risk.iterrows()}

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    engine = kw["engine"]; scores = kw["scores"]; prices = kw["prices"]
    sdi = kw["sdi"]; pdi = kw["pdi"]; calendar = kw["calendar"]
    signal_to_exec = kw["signal_to_exec"]; exec_to_signal = kw["exec_to_signal"]
    start_date = kw["start_date"]; end_date = kw["end_date"]
    initial_cash = kw.get("initial_cash", 500000.0)

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices, specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    nav_rows, holdings_rows = [], []
    cr_d = D(str(cost_rate))

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        rpl_close = _price_lookup_for_day(prices, pdi, trade_date, ["raw_close"])

        if signal_date is None:
            nav_rows.append({"trade_date": trade_date, "nav": 1.0, "cash": account.cash,
                             "position_count": 0, "position_ratio": 0.0})
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)
        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0,
                       trade_cost_rate=cost_rate, slippage_rate=slip_rate,
                       max_total_positions=5, position_ratio=position_ratio,
                       calendar=calendar, open_prices=rpl,
                       targets=targets, precommit_prices=None,
                       strict_precommit=False, ledger=None)

        eq = _equity(account, rpl, "raw_close")
        nav = eq / initial_cash if initial_cash > 0 else 1.0

        # Record holdings
        for sym, pos in account.positions.items():
            px = _safe_float(rpl_close.get(sym, {}).get("raw_close"), 0)
            holdings_rows.append({
                "trade_date": trade_date, "symbol": sym, "shares": pos.shares,
                "close_price": round(float(px), 2),
                "market_value": round(pos.shares * px, 2),
                "label": label,
            })

        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(nav, 8), "cash": round(account.cash, 2),
            "equity": round(eq, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "in_risk": in_risk,
        })

    nav_df = pd.DataFrame(nav_rows)
    holdings_df = pd.DataFrame(holdings_rows) if holdings_rows else pd.DataFrame()
    m = _metrics(nav_df)
    return {"label": label, "nav_df": nav_df, "holdings_df": holdings_df, "metrics": m}


def _metrics(df):
    if df is None or df.empty or "nav" not in df.columns: return {}
    nav = df["nav"].values.astype(float); n = len(nav)
    tr = float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak = np.maximum.accumulate(nav); dd = float(np.min((nav-peak)/peak))
    ar = float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    cv = float(-np.mean(np.sort(np.diff(nav)/nav[:-1])[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul = float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca = float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6),"n_days":n}


# ══════════════════════════════════════════════════════════════════════
# R16.2: Shapley decomposition
# ══════════════════════════════════════════════════════════════════════

def shapley_decompose(c0, c1, c2, c3):
    """
    R16.2: Shapley decomposition.

    Security path contribution = 0.5 × [(C2-C0) + (C3-C1)]
    Exposure path contribution  = 0.5 × [(C1-C0) + (C3-C2)]

    Total = Security + Exposure = C3 - C0
    """
    c0_r = c0["metrics"]["total_return"]; c1_r = c1["metrics"]["total_return"]
    c2_r = c2["metrics"]["total_return"]; c3_r = c3["metrics"]["total_return"]

    security_contrib = 0.5 * ((c2_r - c0_r) + (c3_r - c1_r))
    exposure_contrib = 0.5 * ((c1_r - c0_r) + (c3_r - c2_r))
    total = c3_r - c0_r
    residual = total - security_contrib - exposure_contrib

    # Calmar decomposition
    c0_c = c0["metrics"]["calmar"]; c1_c = c1["metrics"]["calmar"]
    c2_c = c2["metrics"]["calmar"]; c3_c = c3["metrics"]["calmar"]
    sec_cal = 0.5 * ((c2_c - c0_c) + (c3_c - c1_c))
    exp_cal = 0.5 * ((c1_c - c0_c) + (c3_c - c2_c))

    # MaxDD decomposition
    c0_d = c0["metrics"]["max_drawdown"]; c1_d = c1["metrics"]["max_drawdown"]
    c2_d = c2["metrics"]["max_drawdown"]; c3_d = c3["metrics"]["max_drawdown"]
    sec_dd = 0.5 * ((c2_d - c0_d) + (c3_d - c1_d))
    exp_dd = 0.5 * ((c1_d - c0_d) + (c3_d - c2_d))

    is_security_driven = abs(security_contrib) > abs(exposure_contrib)
    mechanism = "SECURITY_PATH" if is_security_driven else "EXPOSURE_PATH"

    return {
        "c0_return": c0_r, "c1_return": c1_r, "c2_return": c2_r, "c3_return": c3_r,
        "c0_calmar": c0_c, "c1_calmar": c1_c, "c2_calmar": c2_c, "c3_calmar": c3_c,
        "security_contribution": round(security_contrib, 6),
        "exposure_contribution": round(exposure_contrib, 6),
        "total_change": round(total, 6),
        "residual": round(residual, 8),
        "security_calmar": round(sec_cal, 4),
        "exposure_calmar": round(exp_cal, 4),
        "security_maxdd": round(sec_dd, 4),
        "exposure_maxdd": round(exp_dd, 4),
        "mechanism": mechanism,
        "security_pct": round(abs(security_contrib)/max(abs(total),0.001)*100, 1),
        "exposure_pct": round(abs(exposure_contrib)/max(abs(total),0.001)*100, 1),
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
    print("B2_SCALE40 R16.1-R16.2: C0-C3 + Shapley")
    print("=" * 60)

    db_url = build_sqlalchemy_url(); engine = create_engine(db_url)
    print("Loading...")
    cal = _build_calendar(engine, args.start_date, args.end_date); cal = sorted(set(cal))
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
    out_dir=OUT_ROOT/f"b2_r161_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True,exist_ok=True)

    anchor = build_anchor_risk_state(engine=engine, scores=ss, prices=ps, market_env=me,
                                      calendar=cal, signal_to_exec=s2e, exec_to_signal=e2s,
                                      sdi=sdi, pdi=pdi, it_trends=it, specs=specs,
                                      start_date=args.start_date, end_date=args.end_date,
                                      initial_cash=args.initial_cash)

    kw = dict(engine=engine, scores=ss, prices=ps, market_env=me, calendar=cal,
              signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi, it_trends=it,
              specs=specs, start_date=args.start_date, end_date=args.end_date,
              initial_cash=args.initial_cash)

    # ══════════════════════════════════════════════════════════
    # R16.1: Run all 4 counterfactuals
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R16.1: 四条路径 ===")

    # C0: S60 security + S60 exposure (fixed 60%)
    print("  C0 (S60+S60)...", end=" ", flush=True)
    c0 = run_counterfactual("C0", anchor, 0.60, 0.60, **kw)
    print(f"R={c0['metrics']['total_return']:.2%} Cal={c0['metrics']['calmar']:.2f}")

    # C3: B2 security + B2 exposure (60/40)
    print("  C3 (B2+B2)...", end=" ", flush=True)
    c3 = run_counterfactual("C3", anchor, 0.60, 0.40, **kw)
    print(f"R={c3['metrics']['total_return']:.2%} Cal={c3['metrics']['calmar']:.2f}")

    # C1: S60 security + B2 exposure (S60 stocks at 60/40)
    print("  C1 (S60+B2)...", end=" ", flush=True)
    c1 = run_counterfactual("C1", anchor, 0.60, 0.40, **kw)
    print(f"R={c1['metrics']['total_return']:.2%} Cal={c1['metrics']['calmar']:.2f}")

    # C2: B2 security + S60 exposure (B2 stocks at fixed 60%)
    print("  C2 (B2+S60)...", end=" ", flush=True)
    c2 = run_counterfactual("C2", anchor, 0.60, 0.60, **kw)
    print(f"R={c2['metrics']['total_return']:.2%} Cal={c2['metrics']['calmar']:.2f}")

    # ══════════════════════════════════════════════════════════
    # R16.2: Shapley decomposition
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R16.2: Shapley 归因 ===")
    shap = shapley_decompose(c0, c1, c2, c3)

    print(f"  C0 (S60+S60):  R={shap['c0_return']:.2%} Cal={shap['c0_calmar']:.2f}")
    print(f"  C1 (S60+B2):   R={shap['c1_return']:.2%} Cal={shap['c1_calmar']:.2f}")
    print(f"  C2 (B2+S60):   R={shap['c2_return']:.2%} Cal={shap['c2_calmar']:.2f}")
    print(f"  C3 (B2+B2):    R={shap['c3_return']:.2%} Cal={shap['c3_calmar']:.2f}")
    print(f"")
    print(f"  Shapley 归因 (总变化 = C3-C0 = {shap['total_change']:+.2%}):")
    print(f"    证券路径贡献: {shap['security_contribution']:+.2%} ({shap['security_pct']:.0f}%)  Calmar: {shap['security_calmar']:+.2f}")
    print(f"    暴露路径贡献: {shap['exposure_contribution']:+.2%} ({shap['exposure_pct']:.0f}%)  Calmar: {shap['exposure_calmar']:+.2f}")
    print(f"    残差: {shap['residual']:+.6f}")
    print(f"    机制: {shap['mechanism']}")

    # Save
    for label, r in [("C0",c0),("C1",c1),("C2",c2),("C3",c3)]:
        r["nav_df"].to_csv(out_dir/f"nav_{label.lower()}.csv", index=False)
        r["holdings_df"].to_csv(out_dir/f"holdings_{label.lower()}.csv", index=False)

    with open(out_dir/"r162_shapley.json","w") as f:
        json.dump(shap, f, indent=2)

    report = [
        "# B2_SCALE40 R16.1-R16.2: Shapley 归因",
        f"## 四条路径",
        f"| 路径 | 收益 | Calmar | MaxDD |",
        f"|------|------|--------|-------|",
        f"| C0 (S60+S60) | {shap['c0_return']:.2%} | {shap['c0_calmar']:.2f} | {c0['metrics']['max_drawdown']:.2%} |",
        f"| C1 (S60+B2) | {shap['c1_return']:.2%} | {shap['c1_calmar']:.2f} | {c1['metrics']['max_drawdown']:.2%} |",
        f"| C2 (B2+S60) | {shap['c2_return']:.2%} | {shap['c2_calmar']:.2f} | {c2['metrics']['max_drawdown']:.2%} |",
        f"| C3 (B2+B2) | {shap['c3_return']:.2%} | {shap['c3_calmar']:.2f} | {c3['metrics']['max_drawdown']:.2%} |",
        f"",
        f"## Shapley 归因",
        f"- 证券路径: {shap['security_contribution']:+.2%} ({shap['security_pct']:.0f}%)",
        f"- 暴露路径: {shap['exposure_contribution']:+.2%} ({shap['exposure_pct']:.0f}%)",
        f"- 残差: {shap['residual']:+.6f}",
        f"- **机制: {shap['mechanism']}**",
    ]
    (out_dir/"r161_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"机制: {shap['mechanism']} (证券{shap['security_pct']:.0f}% vs 暴露{shap['exposure_pct']:.0f}%)")
    print(f"报告: {out_dir}/r161_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
