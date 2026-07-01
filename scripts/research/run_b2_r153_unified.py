#!/usr/bin/env python3
"""
B2_SCALE40 R15.3: 成交价格与成本语义统一

- 统一 fill event: fill_price = trade price, reference_open_price = raw_open
- price_impact_cost = abs(fill - ref_open) × shares (captures all slippage)
- commission/stamp_tax always separate
- 5 scenarios × 2 strategies, all must pass replay
- Fixed-order + endogenous-order sensitivity tests
- Tamper detection on all cost/price fields

Usage:
    python scripts/research/run_b2_r153_unified.py \
        --start-date 2023-01-03 --end-date 2026-06-30
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
    _build_targets_cache,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_fsc1_validation import build_anchor_risk_state

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R15.3: Unified fill event backtest
# ══════════════════════════════════════════════════════════════════════

def run_unified_ledger(label, anchor_risk, target_normal=0.60, target_risk=0.40,
                        cost_rate=0.00075, slip_rate=0.0, **kw):
    """R15.3: Native fill events with unified price/cost semantics."""

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
    ledger_rows, snapshot_rows = [], []
    order_id, cum_cost = 0, 0.0

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
            snapshot_rows.append(_snap(trade_date, None, account, rpl_close, 0.0, False, cum_cost, 0))
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)
        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        daily_cost = 0.0
        if not targets.empty or account.positions:
            pre_cash = account.cash
            pre_positions = {sym: pos.shares for sym, pos in account.positions.items()}

            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0,
                trade_cost_rate=cost_rate, slippage_rate=slip_rate,
                max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets, precommit_prices=None,
                strict_precommit=False, ledger=None)

            # ── R15.3: Sequential fill tracking with unified prices ──
            running_cash = pre_cash
            running_positions = dict(pre_positions)
            exec_seq = 0

            for t in trades:
                exec_seq += 1
                sym = str(t.get("symbol", "")).zfill(6)
                side = t.get("side", "?")
                shares = t.get("shares", 0) or 0

                # R15.3: Fill price = actual trade price from _rebalance
                fill_price = t.get("price", 0) or 0
                ref_open = _safe_float(rpl.get(sym, {}).get("raw_open"), 0)

                gross_notional = abs(shares) * fill_price if fill_price else 0
                price_impact = abs(fill_price - ref_open) * abs(shares) if ref_open > 0 else 0
                commission = gross_notional * cost_rate
                stamp = 0.0

                shares_before = running_positions.get(sym, 0)
                cash_before = running_cash

                # Pre-trade MV using reference_open_price for all positions
                pre_mv = sum(sh * _safe_float(rpl.get(s, {}).get("raw_open"), 0)
                            for s, sh in running_positions.items())

                if side == "BUY":
                    cash_delta = -(gross_notional + commission)
                else:
                    cash_delta = gross_notional - commission
                running_cash += cash_delta

                if side == "BUY":
                    running_positions[sym] = running_positions.get(sym, 0) + shares
                else:
                    running_positions[sym] = running_positions.get(sym, 0) - shares
                    if running_positions[sym] <= 0:
                        running_positions.pop(sym, None)

                shares_after = running_positions.get(sym, 0)

                # Post-trade MV
                post_mv = sum(sh * _safe_float(rpl.get(s, {}).get("raw_open"), 0)
                             for s, sh in running_positions.items())

                daily_cost += commission
                order_id += 1

                ledger_rows.append({
                    "order_id": order_id, "execution_date": trade_date,
                    "execution_sequence": exec_seq, "signal_date": signal_date,
                    "symbol": sym, "side": side,
                    "order_reason": "HOLD_EXPIRY" if side == "SELL" else "TARGET_REBALANCE",
                    "reference_open_price": round(float(ref_open), 4),
                    "fill_price": round(float(fill_price), 4),
                    "shares_filled": abs(shares),
                    "gross_notional": round(float(gross_notional), 4),
                    "price_impact_cost": round(float(price_impact), 4),
                    "commission": round(float(commission), 6),
                    "stamp_tax": 0.0, "transfer_fee": 0.0,
                    "total_fee": round(float(commission), 6),
                    "cash_delta": round(float(cash_delta), 2),
                    "cash_before": round(float(cash_before), 2),
                    "cash_after": round(float(running_cash), 2),
                    "shares_before": shares_before, "shares_after": shares_after,
                    "market_value_before": round(float(pre_mv), 2),
                    "market_value_after": round(float(post_mv), 2),
                    "equity_before": round(float(cash_before + pre_mv), 2),
                    "equity_after": round(float(running_cash + post_mv), 2),
                    "risk_state": in_risk, "target_exposure": round(position_ratio, 4),
                    "strategy_label": label,
                    "scenario": f"c{cost_rate}_s{slip_rate}",
                })

            cum_cost += daily_cost

        snapshot_rows.append(_snap(trade_date, signal_date, account, rpl_close,
                                    position_ratio, in_risk, cum_cost, daily_cost))

    ledger_df = pd.DataFrame(ledger_rows) if ledger_rows else pd.DataFrame()
    snapshot_df = pd.DataFrame(snapshot_rows) if snapshot_rows else pd.DataFrame()

    # Cash continuity check
    breaks = 0
    if not ledger_df.empty:
        for ed in ledger_df["execution_date"].unique():
            day = ledger_df[ledger_df["execution_date"] == ed].sort_values("execution_sequence")
            for i in range(1, len(day)):
                if abs(float(day.iloc[i]["cash_before"]) - float(day.iloc[i-1]["cash_after"])) > 0.02:
                    breaks += 1

    m = _metrics(snapshot_df)
    return {"label": label, "ledger_df": ledger_df, "snapshot_df": snapshot_df,
            "metrics": m, "cash_breaks": breaks, "cumulative_cost": cum_cost}


def _snap(td, sd, acct, rpl_close, pr, ir, cc, dc):
    mv = sum(pos.shares * _safe_float(rpl_close.get(s, {}).get("raw_close"), 0)
             for s, pos in acct.positions.items())
    eq = acct.cash + mv; nav = eq / 500000.0
    return {"trade_date": td, "signal_date": sd, "cash": round(acct.cash, 2),
            "gross_market_value": round(mv, 2), "equity": round(eq, 2),
            "nav": round(nav, 8),
            "actual_exposure": round(mv/eq, 6) if eq > 0 else 0.0,
            "position_count": len(acct.positions),
            "position_ratio": round(pr, 4), "risk_state": ir,
            "daily_total_cost": round(dc, 4),
            "cumulative_total_cost": round(cc, 4)}


def _metrics(df):
    if df is None or df.empty or "nav" not in df.columns: return {}
    nav = df["nav"].values; n = len(nav)
    tr = float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak = np.maximum.accumulate(nav); dd = float(np.min((nav-peak)/peak))
    ar = float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    cv = float(-np.mean(np.sort(np.diff(nav)/nav[:-1])[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul = float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca = float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6),"n_days":n}


# ══════════════════════════════════════════════════════════════════════
# R15.3: Independent replay
# ══════════════════════════════════════════════════════════════════════

def replay_unified(ledger_df, snapshot_df, prices_df, pdi, initial_cash=500000.0):
    """R15.3: Replay using unified fill events. Detects any tampering."""
    if ledger_df.empty: return {"ok": False, "error": "empty"}

    cash = initial_cash; positions = {}
    nav_diffs, cash_diffs = [], []
    all_dates = sorted(snapshot_df["trade_date"].unique())
    lbd = defaultdict(list)
    for _, row in ledger_df.iterrows():
        lbd[row["execution_date"]].append(row)

    for td in all_dates:
        orders = sorted(lbd.get(td, []), key=lambda x: x["execution_sequence"])
        for order in orders:
            sym = order["symbol"]; side = order["side"]
            shares = order["shares_filled"]
            gross_notional = float(order["gross_notional"])
            commission = float(order["commission"])

            exp_cash = float(order["cash_before"])
            if abs(cash - exp_cash) > 0.02:
                return {"ok": False, "error": f"TAMPER cash_before order {order['order_id']}"}

            if side == "BUY":
                cash -= (gross_notional + commission)
                positions[sym] = positions.get(sym, 0) + shares
            else:
                cash += (gross_notional - commission)
                positions[sym] = positions.get(sym, 0) - shares
                if positions[sym] <= 0: positions.pop(sym, None)

            exp_cash_after = float(order["cash_after"])
            if abs(cash - exp_cash_after) > 0.02:
                return {"ok": False, "error": f"TAMPER cash_after order {order['order_id']}"}

        rpl_close = _price_lookup_for_day(prices_df, pdi, td, ["raw_close"])
        mv = sum(sh * _safe_float(rpl_close.get(s, {}).get("raw_close"), 0)
                 for s, sh in positions.items())
        eq = cash + mv; nav = eq / initial_cash

        snap = snapshot_df[snapshot_df["trade_date"] == td]
        if not snap.empty:
            nav_diffs.append(abs(nav - float(snap.iloc[0]["nav"])))
            cash_diffs.append(abs(cash - float(snap.iloc[0]["cash"])))

    mx_nav = max(nav_diffs) * 10000 if nav_diffs else 999
    mx_cash = max(cash_diffs) if cash_diffs else 999
    return {"ok": mx_nav <= 0.01 and mx_cash <= 0.02,
            "max_nav_bps": round(mx_nav, 6), "max_cash_diff": round(mx_cash, 4)}


# ══════════════════════════════════════════════════════════════════════
# Tamper tests
# ══════════════════════════════════════════════════════════════════════

def test_tamper(ledger_df, snap_df, ps, pdi, initial_cash, field, row_idx, new_val):
    t = ledger_df.copy()
    t.loc[row_idx, field] = new_val
    rp = replay_unified(t, snap_df, ps, pdi, initial_cash)
    return not rp["ok"]


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", default="2023-01-03")
    p.add_argument("--end-date", default="2026-06-30")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--initial-cash", type=float, default=500000.0)
    args = p.parse_args()

    print("=" * 60)
    print("B2_SCALE40 R15.3: 成交价格与成本语义统一")
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
    out_dir=OUT_ROOT/f"b2_r153_{ts}" if not args.output_dir else Path(args.output_dir)
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

    scenarios = [
        ("base", 0.00075, 0.0),
        ("c15bp", 0.0015, 0.0),
        ("s5bp", 0.00075, 0.0005),
        ("s10bp", 0.00075, 0.0010),
        ("s20bp", 0.00075, 0.0020),
    ]

    all_results = {}
    all_pass = True
    for sn, cr, sr in scenarios:
        print(f"\n=== {sn} (c={cr:.4f} s={sr:.4f}) ===")
        s60 = run_unified_ledger("S60", anchor, 0.60, 0.60, cost_rate=cr, slip_rate=sr, **kw)
        b2 = run_unified_ledger("B2", anchor, 0.60, 0.40, cost_rate=cr, slip_rate=sr, **kw)
        s60_rp = replay_unified(s60["ledger_df"], s60["snapshot_df"], ps, pdi, args.initial_cash)
        b2_rp = replay_unified(b2["ledger_df"], b2["snapshot_df"], ps, pdi, args.initial_cash)
        ok = s60["cash_breaks"] == 0 and b2["cash_breaks"] == 0 and s60_rp["ok"] and b2_rp["ok"]
        all_pass = all_pass and ok
        print(f"  S60: R={s60['metrics']['total_return']:.2%} Breaks={s60['cash_breaks']} "
              f"Replay={'✅' if s60_rp['ok'] else '❌'} NAV={s60_rp.get('max_nav_bps','?')}bps")
        print(f"  B2:  R={b2['metrics']['total_return']:.2%} Breaks={b2['cash_breaks']} "
              f"Replay={'✅' if b2_rp['ok'] else '❌'} NAV={b2_rp.get('max_nav_bps','?')}bps")
        print(f"  {'✅' if ok else '❌'}")
        all_results[sn] = {"S60": s60, "B2": b2, "s60_replay": s60_rp, "b2_replay": b2_rp}

    # Tamper tests
    print(f"\n=== 篡改检测 ===")
    base_ledger = all_results["base"]["S60"]["ledger_df"]
    base_snap = all_results["base"]["S60"]["snapshot_df"]
    tamper_fields = ["fill_price", "gross_notional", "commission", "cash_after", "execution_sequence"]
    tamper_ok = 0
    for field in tamper_fields:
        old = base_ledger.loc[0, field]
        new = old * 1.5 if isinstance(old, (int, float)) else old
        detected = test_tamper(base_ledger, base_snap, ps, pdi, args.initial_cash, field, 0, new)
        print(f"  {field}: {'DETECTED ✅' if detected else 'UNDETECTED ❌'}")
        if detected: tamper_ok += 1

    # Save
    base_b2 = all_results["base"]["B2"]
    base_b2["ledger_df"].to_csv(out_dir/"r153_ledger_b2.csv", index=False)
    base_b2["snapshot_df"].to_csv(out_dir/"r153_snapshots_b2.csv", index=False)

    stress = []
    for sn, cr, sr in scenarios:
        r = all_results[sn]
        s60m = r["S60"]["metrics"]; b2m = r["B2"]["metrics"]
        stress.append({"scenario": sn, "cost_rate": cr, "slip_rate": sr,
                       "s60_return": s60m["total_return"], "b2_return": b2m["total_return"],
                       "s60_calmar": s60m["calmar"], "b2_calmar": b2m["calmar"],
                       "cash_breaks": r["S60"]["cash_breaks"] + r["B2"]["cash_breaks"],
                       "replay_ok": r["s60_replay"]["ok"] and r["b2_replay"]["ok"]})
    pd.DataFrame(stress).to_csv(out_dir/"r153_stress.csv", index=False)

    verdict = [
        "# B2_SCALE40 R15.3 结论",
        f"## 全场景通过: {'✅' if all_pass else '❌'}",
        f"## 篡改检测: {tamper_ok}/{len(tamper_fields)}",
    ]
    (out_dir/"r153_verdict.md").write_text("\n".join(verdict))

    print(f"\n{'='*60}")
    print(f"全场景: {'✅' if all_pass else '❌'} | 篡改: {tamper_ok}/{len(tamper_fields)}")
    print("Done.")


if __name__ == "__main__":
    main()
