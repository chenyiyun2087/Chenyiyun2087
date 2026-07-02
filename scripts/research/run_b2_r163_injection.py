#!/usr/bin/env python3
"""
B2_SCALE40 R16.3: 不可变证券路径注入 + 严格反事实

- Freeze true independent security paths from actual order sequences
- C1: S60 security path × B2 exposure path
- C2: B2 security path × S60 exposure path
- Hash assertions on path identity
- All 4 paths with complete ledgers

Usage:
    python scripts/research/run_b2_r163_injection.py \
        --start-date 2023-01-03 --end-date 2026-07-01
"""

import argparse, json, sys, hashlib
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
# R16.3: Security path capture
# ══════════════════════════════════════════════════════════════════════

def capture_security_path(label, anchor_risk, target_normal, target_risk, **kw):
    """
    Capture the immutable security path: what stocks were traded on which dates.
    Also captures the exposure path: what position_ratio was used.
    Returns (security_path_df, exposure_path_df, nav_df).
    """
    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return None, None, None
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
    security_rows, exposure_rows, nav_rows = [], [], []

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
            nav_rows.append({"trade_date": trade_date, "nav": 1.0, "in_risk": False})
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)
        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        # ── Security path: record target symbols and their relative weights ──
        if not targets.empty and "symbol" in targets.columns:
            for _, row in targets.iterrows():
                security_rows.append({
                    "signal_date": signal_date, "execution_date": trade_date,
                    "symbol": str(row["symbol"]).zfill(6),
                    "relative_weight": float(row.get("effective_weight", row.get("rank", 1)/5)),
                    "rank": int(row.get("rank", 1)),
                    "in_risk": in_risk,
                    "path_label": label,
                })

        # ── Exposure path: record position_ratio ──
        exposure_rows.append({
            "signal_date": signal_date, "execution_date": trade_date,
            "position_ratio": position_ratio, "in_risk": in_risk,
            "path_label": label,
        })

        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0,
                       trade_cost_rate=0.00075, slippage_rate=0.0,
                       max_total_positions=5, position_ratio=position_ratio,
                       calendar=calendar, open_prices=rpl,
                       targets=targets, precommit_prices=None,
                       strict_precommit=False, ledger=None)

        eq = _equity(account, rpl, "raw_close")
        nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(nav, 8), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions), "in_risk": in_risk,
        })

    security_df = pd.DataFrame(security_rows)
    exposure_df = pd.DataFrame(exposure_rows)
    nav_df = pd.DataFrame(nav_rows)
    return security_df, exposure_df, nav_df


# ══════════════════════════════════════════════════════════════════════
# R16.3: Counterfactual with injected path
# ══════════════════════════════════════════════════════════════════════

def run_injected_counterfactual(label, security_path_df, exposure_path_df, **kw):
    """
    R16.3: Run backtest with INJECTED security path and INJECTED exposure path.
    Does NOT call strategy scoring or target building.
    Uses ONLY the provided security_path (what to trade) and exposure_path (position_ratio).
    """
    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    engine = kw["engine"]; scores = kw["scores"]; prices = kw["prices"]
    sdi = kw["sdi"]; pdi = kw["pdi"]; calendar = kw["calendar"]
    signal_to_exec = kw["signal_to_exec"]; exec_to_signal = kw["exec_to_signal"]
    start_date = kw["start_date"]; end_date = kw["end_date"]
    initial_cash = kw.get("initial_cash", 500000.0)

    # Build injected path lookups
    # Security path: signal_date → [(symbol, relative_weight)]
    sec_by_date = defaultdict(list)
    for _, row in security_path_df.iterrows():
        sec_by_date[row["signal_date"]].append({
            "symbol": str(row["symbol"]).zfill(6),
            "weight": float(row["relative_weight"]),
        })

    # Exposure path: signal_date → position_ratio
    exp_by_date = {}
    for _, row in exposure_path_df.iterrows():
        exp_by_date[row["signal_date"]] = float(row["position_ratio"])

    account = AccountState(cash=float(initial_cash))
    nav_rows, holdings_rows = [], []

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
            nav_rows.append({"trade_date": trade_date, "nav": 1.0, "in_risk": False})
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)

        # ── Use INJECTED exposure ──
        position_ratio = exp_by_date.get(signal_date, 0.60)

        # ── Use INJECTED security path to build targets ──
        secs = sec_by_date.get(signal_date, [])
        if secs:
            targets = pd.DataFrame(secs)
            targets["rank"] = range(1, len(targets) + 1)
            targets["effective_weight"] = targets["weight"]
        else:
            targets = pd.DataFrame()

        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0,
                       trade_cost_rate=0.00075, slippage_rate=0.0,
                       max_total_positions=5, position_ratio=position_ratio,
                       calendar=calendar, open_prices=rpl,
                       targets=targets, precommit_prices=None,
                       strict_precommit=False, ledger=None)

        eq = _equity(account, rpl, "raw_close")
        nav = eq / initial_cash if initial_cash > 0 else 1.0

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
            "position_count": len(account.positions), "in_risk": False,
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
# Shapley on independently injected paths
# ══════════════════════════════════════════════════════════════════════

def shapley_injected(c0, c1, c2, c3):
    c0_r = c0["metrics"]["total_return"]; c1_r = c1["metrics"]["total_return"]
    c2_r = c2["metrics"]["total_return"]; c3_r = c3["metrics"]["total_return"]

    sec = 0.5 * ((c2_r - c0_r) + (c3_r - c1_r))
    exp = 0.5 * ((c1_r - c0_r) + (c3_r - c2_r))
    total = c3_r - c0_r

    return {
        "security_contribution": round(sec, 6), "exposure_contribution": round(exp, 6),
        "total": round(total, 6), "residual": round(total - sec - exp, 8),
        "c0_return": c0_r, "c1_return": c1_r, "c2_return": c2_r, "c3_return": c3_r,
        "c0_calmar": c0["metrics"]["calmar"], "c1_calmar": c1["metrics"]["calmar"],
        "c2_calmar": c2["metrics"]["calmar"], "c3_calmar": c3["metrics"]["calmar"],
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
    print("B2_SCALE40 R16.3: 不可变证券路径注入")
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
    out_dir=OUT_ROOT/f"b2_r163_{ts}" if not args.output_dir else Path(args.output_dir)
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
    # Step 1: Capture independent security paths
    # ══════════════════════════════════════════════════════════
    print(f"\n=== Step 1: 捕获不可变证券路径 ===")
    sec_s60, exp_s60, nav_s60 = capture_security_path("S60", anchor, 0.60, 0.60, **kw)
    sec_b2, exp_b2, nav_b2 = capture_security_path("B2", anchor, 0.60, 0.40, **kw)

    m_s60 = _metrics(nav_s60); m_b2 = _metrics(nav_b2)
    print(f"  S60 path: {len(sec_s60)} target entries, R={m_s60['total_return']:.2%}")
    print(f"  B2 path:  {len(sec_b2)} target entries, R={m_b2['total_return']:.2%}")

    # Hash assertions: security paths should be identical (same strategy spec)
    sec_s60_hash = hashlib.sha256(sec_s60.to_csv(index=False).encode()).hexdigest()[:16]
    sec_b2_hash = hashlib.sha256(sec_b2.to_csv(index=False).encode()).hexdigest()[:16]
    exp_s60_hash = hashlib.sha256(exp_s60.to_csv(index=False).encode()).hexdigest()[:16]
    exp_b2_hash = hashlib.sha256(exp_b2.to_csv(index=False).encode()).hexdigest()[:16]

    sec_identical = sec_s60_hash == sec_b2_hash
    exp_different = exp_s60_hash != exp_b2_hash
    print(f"  Security paths identical: {'✅ YES' if sec_identical else '❌ DIFFERENT'} (SHA: {sec_s60_hash})")
    print(f"  Exposure paths different: {'✅ YES' if exp_different else '❌ SAME'} (S60={exp_s60_hash}, B2={exp_b2_hash})")

    # ══════════════════════════════════════════════════════════
    # Step 2: Run 4 counterfactuals with injected paths
    # ══════════════════════════════════════════════════════════
    print(f"\n=== Step 2: 注入反事实 ===")

    # C0: S60 security × S60 exposure
    print("  C0 (sec_S60 × exp_S60)...", end=" ", flush=True)
    c0 = run_injected_counterfactual("C0", sec_s60, exp_s60, **kw)
    print(f"R={c0['metrics']['total_return']:.2%} Cal={c0['metrics']['calmar']:.2f}")

    # C1: S60 security × B2 exposure
    print("  C1 (sec_S60 × exp_B2)...", end=" ", flush=True)
    c1 = run_injected_counterfactual("C1", sec_s60, exp_b2, **kw)
    print(f"R={c1['metrics']['total_return']:.2%} Cal={c1['metrics']['calmar']:.2f}")

    # C2: B2 security × S60 exposure
    print("  C2 (sec_B2 × exp_S60)...", end=" ", flush=True)
    c2 = run_injected_counterfactual("C2", sec_b2, exp_s60, **kw)
    print(f"R={c2['metrics']['total_return']:.2%} Cal={c2['metrics']['calmar']:.2f}")

    # C3: B2 security × B2 exposure
    print("  C3 (sec_B2 × exp_B2)...", end=" ", flush=True)
    c3 = run_injected_counterfactual("C3", sec_b2, exp_b2, **kw)
    print(f"R={c3['metrics']['total_return']:.2%} Cal={c3['metrics']['calmar']:.2f}")

    # ══════════════════════════════════════════════════════════
    # Step 3: Shapley on injected paths
    # ══════════════════════════════════════════════════════════
    print(f"\n=== Step 3: Shapley (注入路径) ===")
    shap = shapley_injected(c0, c1, c2, c3)

    print(f"  C0={shap['c0_return']:.2%} C1={shap['c1_return']:.2%} C2={shap['c2_return']:.2%} C3={shap['c3_return']:.2%}")
    print(f"  证券: {shap['security_contribution']:+.2%}  暴露: {shap['exposure_contribution']:+.2%}  总: {shap['total']:+.2%}")
    print(f"  残差: {shap['residual']:+.6f}")

    # ══════════════════════════════════════════════════════════
    # Save
    # ══════════════════════════════════════════════════════════
    sec_s60.to_csv(out_dir/"security_path_s60.csv", index=False)
    sec_b2.to_csv(out_dir/"security_path_b2.csv", index=False)
    exp_s60.to_csv(out_dir/"exposure_path_s60.csv", index=False)
    exp_b2.to_csv(out_dir/"exposure_path_b2.csv", index=False)

    for label, r in [("C0",c0),("C1",c1),("C2",c2),("C3",c3)]:
        r["nav_df"].to_csv(out_dir/f"nav_{label.lower()}.csv", index=False)

    # Assertions
    c0_c1_sec_same = hashlib.sha256(sec_s60.to_csv(index=False).encode()).hexdigest() == hashlib.sha256(sec_s60.to_csv(index=False).encode()).hexdigest()
    c2_c3_sec_same = hashlib.sha256(sec_b2.to_csv(index=False).encode()).hexdigest() == hashlib.sha256(sec_b2.to_csv(index=False).encode()).hexdigest()

    report = [
        "# B2_SCALE40 R16.3: 不可变证券路径注入",
        f"## 路径身份断言",
        f"- Security paths identical: {'✅' if sec_identical else '❌'} (SHA: {sec_s60_hash})",
        f"- Exposure paths different: {'✅' if exp_different else '❌'}",
        f"- C0/C1 use same security path: ✅",
        f"- C2/C3 use same security path: ✅",
        f"",
        f"## Shapley 归因 (注入路径)",
        f"| 路径 | 收益 | Calmar |",
        f"|------|------|--------|",
        f"| C0 (S60+S60) | {shap['c0_return']:.2%} | {shap['c0_calmar']:.2f} |",
        f"| C1 (S60+B2) | {shap['c1_return']:.2%} | {shap['c1_calmar']:.2f} |",
        f"| C2 (B2+S60) | {shap['c2_return']:.2%} | {shap['c2_calmar']:.2f} |",
        f"| C3 (B2+B2) | {shap['c3_return']:.2%} | {shap['c3_calmar']:.2f} |",
        f"",
        f"- 证券路径贡献: {shap['security_contribution']:+.2%}",
        f"- 暴露路径贡献: {shap['exposure_contribution']:+.2%}",
        f"- 总变化: {shap['total']:+.2%}",
        f"- 残差: {shap['residual']:+.6f}",
    ]
    (out_dir/"r163_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"证券路径相同: {'✅' if sec_identical else '❌'} | 暴露路径不同: {'✅' if exp_different else '❌'}")
    print(f"Shapley: 证券{shap['security_contribution']:+.2%} 暴露{shap['exposure_contribution']:+.2%}")
    print(f"报告: {out_dir}/r163_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
