#!/usr/bin/env python3
"""
B2_SCALE40 R16.4: 执行路径身份审计 + R16.5 动态vs静态暴露

R16.4: Capture actual execution paths, compute SHA on economic fields,
       compare S60 vs B2, report differences.
R16.5: Dynamic exposure vs static pre-committed baseline comparison.

Usage:
    python scripts/research/run_b2_r164_audit.py \
        --start-date 2023-01-03 --end-date 2026-07-01
"""

import argparse, json, sys, hashlib
from datetime import datetime
from pathlib import Path
from collections import defaultdict
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
from scripts.research.run_b2_r161_shapley import run_counterfactual

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"

ECONOMIC_COLS = [
    "signal_date", "execution_date", "symbol", "side",
    "requested_shares", "filled_shares",
    "target_relative_weight", "actual_relative_weight",
    "entry_reason", "exit_reason",
]


# ══════════════════════════════════════════════════════════════════════
# R16.4: Capture actual execution path
# ══════════════════════════════════════════════════════════════════════

def capture_execution_path(label, anchor_risk, target_normal, target_risk, **kw):
    """R16.4: Capture actual execution path with entry/exit dates and trade details."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return None, None
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
    exec_rows, nav_rows = [], []
    # Track position entry dates
    position_entry = {}  # symbol → entry_date
    position_exit = {}   # symbol → exit_date (for recently closed)

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
            nav_rows.append({"trade_date": trade_date, "nav": 1.0})
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)
        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        prev_positions = set(account.positions.keys())

        if not targets.empty or account.positions:
            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0,
                trade_cost_rate=0.00075, slippage_rate=0.0,
                max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets, precommit_prices=None,
                strict_precommit=False, ledger=None)

            # Record execution path entries
            for t in trades:
                sym = str(t.get("symbol", "")).zfill(6)
                side = t.get("side", "?")
                shares = t.get("shares", 0) or 0
                price = t.get("price", 0) or 0

                # Get target weight from targets cache
                target_wt = 0.0
                if not targets.empty and "symbol" in targets.columns and "effective_weight" in targets.columns:
                    match = targets[targets["symbol"].astype(str).str.zfill(6) == sym]
                    if not match.empty:
                        target_wt = float(match.iloc[0]["effective_weight"])

                # Actual weight from portfolio
                pos = account.positions.get(sym)
                actual_shares = pos.shares if pos else 0
                total_mv = sum(p.shares * _safe_float(rpl.get(s,{}).get("raw_open"),0)
                              for s, p in account.positions.items())
                actual_wt = (actual_shares * price / total_mv) if total_mv > 0 and price > 0 else 0.0

                if side == "BUY":
                    entry_reason = "NEW_ENTRY" if sym not in prev_positions else "EXISTING_ADD"
                    exit_reason = ""
                    if sym not in position_entry:
                        position_entry[sym] = trade_date
                else:
                    entry_reason = ""
                    exit_reason = "HOLD_EXPIRY" if sym not in account.positions else "PARTIAL_SELL"
                    if sym not in account.positions:
                        position_exit[sym] = trade_date

                exec_rows.append({
                    "signal_date": signal_date, "execution_date": trade_date,
                    "symbol": sym, "side": side,
                    "requested_shares": abs(shares), "filled_shares": abs(shares),
                    "target_relative_weight": round(target_wt, 6),
                    "actual_relative_weight": round(actual_wt, 6),
                    "entry_reason": entry_reason, "exit_reason": exit_reason,
                    "fill_price": round(float(price), 2),
                    "position_ratio": round(position_ratio, 4),
                    "in_risk": in_risk,
                })

        eq = _equity(account, rpl, "raw_close")
        nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({"trade_date": trade_date, "nav": round(nav, 8)})

    exec_df = pd.DataFrame(exec_rows)
    nav_df = pd.DataFrame(nav_rows)
    return exec_df, nav_df


# ══════════════════════════════════════════════════════════════════════
# R16.4: Path identity comparison
# ══════════════════════════════════════════════════════════════════════

def compare_execution_paths(s60_exec, b2_exec):
    """Compare execution paths on economic fields only."""

    # Subset to economic columns
    s60_eco = s60_exec[[c for c in ECONOMIC_COLS if c in s60_exec.columns]].copy()
    b2_eco = b2_exec[[c for c in ECONOMIC_COLS if c in b2_exec.columns]].copy()

    s60_hash = hashlib.sha256(s60_eco.to_csv(index=False).encode()).hexdigest()
    b2_hash = hashlib.sha256(b2_eco.to_csv(index=False).encode()).hexdigest()
    identical = s60_hash == b2_hash

    # Row-level diff
    n_s60 = len(s60_eco); n_b2 = len(b2_eco)
    if n_s60 == n_b2:
        # Compare row by row
        diffs = 0
        for i in range(min(n_s60, n_b2)):
            s60_row = s60_eco.iloc[i].to_dict()
            b2_row = b2_eco.iloc[i].to_dict()
            if s60_row != b2_row:
                diffs += 1
    else:
        diffs = abs(n_s60 - n_b2)

    # Symbol-level comparison
    s60_syms = set(s60_exec["symbol"].unique())
    b2_syms = set(b2_exec["symbol"].unique())
    common = s60_syms & b2_syms
    s60_only = s60_syms - b2_syms
    b2_only = b2_syms - s60_syms

    # Side counts
    s60_buys = int((s60_exec["side"] == "BUY").sum())
    b2_buys = int((b2_exec["side"] == "BUY").sum())
    s60_sells = int((s60_exec["side"] == "SELL").sum())
    b2_sells = int((b2_exec["side"] == "SELL").sum())

    # Entry reason differences
    s60_new = int((s60_exec["entry_reason"] == "NEW_ENTRY").sum())
    b2_new = int((b2_exec["entry_reason"] == "NEW_ENTRY").sum())

    return {
        "s60_sha": s60_hash, "b2_sha": b2_hash,
        "identical": identical,
        "s60_rows": n_s60, "b2_rows": n_b2,
        "row_diffs": diffs,
        "common_symbols": len(common),
        "s60_only_symbols": len(s60_only),
        "b2_only_symbols": len(b2_only),
        "s60_buys": s60_buys, "b2_buys": b2_buys,
        "s60_sells": s60_sells, "b2_sells": b2_sells,
        "s60_new_entries": s60_new, "b2_new_entries": b2_new,
        "conclusion": "IDENTICAL — skip Shapley, go to dynamic vs static"
                      if identical else "DIFFERENT — proceed with Shapley decomposition",
    }


# ══════════════════════════════════════════════════════════════════════
# R16.5: Dynamic vs static exposure comparison
# ══════════════════════════════════════════════════════════════════════

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
    print("B2_SCALE40 R16.4-R16.5: 执行路径审计 + 动态vs静态")
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
    out_dir=OUT_ROOT/f"b2_r164_{ts}" if not args.output_dir else Path(args.output_dir)
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
    # R16.4: Capture execution paths
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R16.4: 执行路径捕获 ===")
    s60_exec, s60_nav = capture_execution_path("S60", anchor, 0.60, 0.60, **kw)
    b2_exec, b2_nav = capture_execution_path("B2", anchor, 0.60, 0.40, **kw)

    s60_m = _metrics(s60_nav); b2_m = _metrics(b2_nav)
    print(f"  S60: {len(s60_exec)} trades, R={s60_m['total_return']:.2%}")
    print(f"  B2:  {len(b2_exec)} trades, R={b2_m['total_return']:.2%}")

    # Compare
    cmp = compare_execution_paths(s60_exec, b2_exec)
    print(f"\n  经济字段 SHA:")
    print(f"    S60: {cmp['s60_sha']}")
    print(f"    B2:  {cmp['b2_sha']}")
    print(f"  路径相同: {'✅ YES' if cmp['identical'] else '❌ DIFFERENT'}")
    print(f"  S60: {cmp['s60_rows']} trades ({cmp['s60_buys']} buys, {cmp['s60_sells']} sells, {cmp['s60_new_entries']} new)")
    print(f"  B2:  {cmp['b2_rows']} trades ({cmp['b2_buys']} buys, {cmp['b2_sells']} sells, {cmp['b2_new_entries']} new)")
    print(f"  共同交易符号: {cmp['common_symbols']}, S60独有: {cmp['s60_only_symbols']}, B2独有: {cmp['b2_only_symbols']}")
    print(f"\n  结论: {cmp['conclusion']}")

    # ══════════════════════════════════════════════════════════
    # R16.5: Dynamic vs static exposure
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R16.5: 动态暴露 vs 静态暴露 ===")

    # Run static baselines
    statics = {}
    for pos in [0.40, 0.50, 0.60]:
        label = f"STATIC_{int(pos*100)}"
        r = run_counterfactual(label, anchor, pos, pos, **kw)
        statics[label] = r
        m = r["metrics"]
        print(f"  {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

    b2_avg_exp = float(b2_nav["nav"].mean())  # approximate
    print(f"\n  B2动态: R={b2_m['total_return']:.2%} DD={b2_m['max_drawdown']:.2%} Cal={b2_m['calmar']:.2f}")

    # Compare B2 against closest static
    static_40_m = statics["STATIC_40"]["metrics"]
    cal_delta_40 = b2_m["calmar"] - static_40_m["calmar"]
    print(f"  B2 vs STATIC_40 Calmar差: {cal_delta_40:+.2f} {'✅' if cal_delta_40 > 0.10 else '❌ <0.10'}")

    # Save
    s60_exec.to_csv(out_dir/"r164_exec_path_s60.csv", index=False)
    b2_exec.to_csv(out_dir/"r164_exec_path_b2.csv", index=False)
    s60_nav.to_csv(out_dir/"nav_s60.csv", index=False)
    b2_nav.to_csv(out_dir/"nav_b2.csv", index=False)

    with open(out_dir/"r164_path_identity.json","w") as f:
        json.dump(cmp, f, indent=2)

    report = [
        "# B2_SCALE40 R16.4-R16.5 审计",
        f"## R16.4: 执行路径身份",
        f"- S60 SHA: {cmp['s60_sha']}",
        f"- B2 SHA:  {cmp['b2_sha']}",
        f"- 路径相同: {'✅' if cmp['identical'] else '❌'}",
        f"- {cmp['conclusion']}",
        f"",
        f"## R16.5: 动态vs静态",
        f"- B2 vs STATIC_40 Calmar: {cal_delta_40:+.2f}",
    ]
    (out_dir/"r164_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"执行路径: {'IDENTICAL' if cmp['identical'] else 'DIFFERENT'}")
    print(f"B2 vs STATIC_40 Calmar: {cal_delta_40:+.2f}")
    print(f"报告: {out_dir}/r164_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
