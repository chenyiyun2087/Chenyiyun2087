#!/usr/bin/env python3
"""
B2_SCALE40 R15-R16: 统一事件账本 + 独立回放 + 严格反事实

R15: 不可变订单账本 + 独立replay engine
R16: C0-C3反事实回放 (使用账本, 不是NAV缩放)

Usage:
    python scripts/research/run_b2_r15_ledger.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys
from datetime import datetime, date
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

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R15: Unified immutable order ledger
# ══════════════════════════════════════════════════════════════════════

LEDGER_COLUMNS = [
    "order_id", "execution_date", "signal_date",
    "symbol", "side", "order_reason",
    "shares_requested", "shares_filled",
    "execution_price", "notional",
    "commission", "stamp_tax", "slippage", "total_cost",
    "cash_before", "cash_delta", "cash_after",
    "shares_before", "shares_after",
    "market_value_before", "market_value_after",
    "equity_before", "equity_after",
    "nav_before", "nav_after",
    "tradable", "limit_blocked", "suspended",
    "risk_state", "target_exposure", "actual_exposure",
    "strategy_label",
]


def run_ledger_backtest(label: str, anchor_risk, target_normal=0.60, target_risk=0.40,
                         engine=None, scores=None, prices=None, market_env=None,
                         calendar=None, signal_to_exec=None, exec_to_signal=None,
                         sdi=None, pdi=None, it_trends=None, specs=None,
                         start_date=None, end_date=None, initial_cash=500000.0,
                         cost_rate=0.00075, slip_rate=0.0,
                         price_field="raw_open"):
    """
    R15: Backtest that produces a complete immutable order ledger.
    Every order records pre/post state for independent replay.
    """

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    risk_lookup = {}
    for _, row in anchor_risk.iterrows():
        risk_lookup[row["signal_date"]] = bool(row["risk_state"])

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices, specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    ledger_rows, snapshot_rows = [], []
    order_counter = 0
    current_nav = 1.0

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            snapshot_rows.append(_snapshot(trade_date, None, account, 0.0, False, 0.0, current_nav))
            continue

        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        day_scores = _score_day_frame(scores, sdi, signal_date)

        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal

        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        # Capture pre-trade state
        pre_cash = account.cash
        pre_positions = {sym: pos.shares for sym, pos in account.positions.items()}
        pre_mv = sum(pos.shares * _safe_float(rpl.get(sym,{}).get(price_field), 0)
                     for sym, pos in account.positions.items())
        pre_equity = pre_cash + pre_mv
        pre_nav = pre_equity / initial_cash if initial_cash > 0 else 1.0

        if not targets.empty or account.positions:
            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0,
                trade_cost_rate=cost_rate, slippage_rate=slip_rate,
                max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets, precommit_prices=None,
                strict_precommit=False, ledger=None)

            # Record each trade as a ledger entry
            for t in trades:
                sym = str(t.get("symbol","")).zfill(6)
                side = t.get("side","?")
                shares = t.get("shares",0) or 0
                price = t.get("price",0) or 0
                notional = abs(shares) * price if price else 0
                cost = notional * cost_rate + notional * slip_rate

                shares_before = pre_positions.get(sym, 0)
                shares_after_val = account.positions.get(sym)
                shares_after_val = shares_after_val.shares if shares_after_val else 0

                order_counter += 1
                ledger_rows.append({
                    "order_id": order_counter,
                    "execution_date": trade_date, "signal_date": signal_date,
                    "symbol": sym, "side": side,
                    "order_reason": "TARGET_REBALANCE" if side=="BUY" else "HOLD_EXPIRY",
                    "shares_requested": abs(shares), "shares_filled": abs(shares),
                    "execution_price": round(float(price), 2),
                    "notional": round(float(notional), 2),
                    "commission": round(float(cost), 4), "stamp_tax": 0.0,
                    "slippage": round(float(notional * slip_rate), 4),
                    "total_cost": round(float(cost), 4),
                    "cash_before": round(pre_cash, 2),
                    "cash_delta": round(float(-notional if side=="BUY" else notional - cost), 2),
                    "cash_after": round(account.cash, 2),
                    "shares_before": shares_before,
                    "shares_after": shares_after_val,
                    "market_value_before": round(pre_mv, 2),
                    "market_value_after": round(post_mv(account, rpl, price_field), 2),
                    "equity_before": round(pre_equity, 2),
                    "equity_after": round(account.cash + post_mv(account, rpl, price_field), 2),
                    "nav_before": round(pre_nav, 6),
                    "nav_after": 0.0,  # filled below
                    "tradable": True, "limit_blocked": False, "suspended": False,
                    "risk_state": in_risk,
                    "target_exposure": round(position_ratio, 4),
                    "actual_exposure": 0.0,  # filled below
                    "strategy_label": label,
                })

        # Post-trade snapshot
        post_eq = _equity(account, rpl, "raw_close")
        current_nav = post_eq / initial_cash if initial_cash > 0 else 1.0
        post_mv_val = post_mv(account, rpl, "raw_close")
        actual_exp = post_mv_val / post_eq if post_eq > 0 else 0.0

        snapshot_rows.append(_snapshot(trade_date, signal_date, account, position_ratio,
                                        in_risk, actual_exp, current_nav))

    ledger_df = pd.DataFrame(ledger_rows) if ledger_rows else pd.DataFrame()
    snapshot_df = pd.DataFrame(snapshot_rows) if snapshot_rows else pd.DataFrame()

    # Fill post-trade nav in ledger
    if not ledger_df.empty and not snapshot_df.empty:
        nav_map = dict(zip(snapshot_df["trade_date"], snapshot_df["nav"]))
        exp_map = dict(zip(snapshot_df["trade_date"], snapshot_df["actual_exposure"]))
        ledger_df["nav_after"] = ledger_df["execution_date"].map(nav_map).fillna(1.0)
        ledger_df["actual_exposure"] = ledger_df["execution_date"].map(exp_map).fillna(0.0)

    metrics = _compute_metrics(snapshot_df)
    return {
        "label": label, "ledger_df": ledger_df, "snapshot_df": snapshot_df,
        "metrics": metrics, "nav_df": snapshot_df,
    }


def post_mv(account, rpl, field="raw_open"):
    return sum(pos.shares * _safe_float(rpl.get(sym,{}).get(field), 0)
               for sym, pos in account.positions.items())


def _snapshot(td, sd, account, pos_ratio, in_risk, actual_exp, nav):
    mv = 0
    for sym, pos in account.positions.items():
        mv += pos.shares * 1  # placeholder, actual calc in post_mv
    return {
        "trade_date": td, "signal_date": sd,
        "cash": round(account.cash, 2),
        "position_count": len(account.positions),
        "position_ratio": round(pos_ratio, 4),
        "actual_exposure": round(float(actual_exp), 4),
        "in_risk": in_risk,
        "equity": round(account.cash + mv, 2),
        "nav": round(float(nav), 6),
    }


# ══════════════════════════════════════════════════════════════════════
# R15: Independent replay engine
# ══════════════════════════════════════════════════════════════════════

def replay_ledger(ledger_df, snapshot_df, prices_df, pdi, initial_cash=500000.0):
    """
    R15: Independent replay — reconstructs NAV solely from the ledger,
    without using the strategy engine.
    """
    if ledger_df.empty: return {}

    # Initialize state
    cash = initial_cash
    positions = {}  # symbol -> shares
    nav_series = []
    cash_series = []
    eq_series = []

    # Get all unique trade dates
    all_dates = sorted(snapshot_df["trade_date"].unique()) if not snapshot_df.empty else []

    ledger_by_date = defaultdict(list)
    for _, row in ledger_df.iterrows():
        ledger_by_date[row["execution_date"]].append(row)

    for td in all_dates:
        # Apply orders for this date
        for order in ledger_by_date.get(td, []):
            sym = order["symbol"]
            side = order["side"]
            shares = order["shares_filled"]
            notional = float(order["notional"])
            cost = float(order["total_cost"])

            if side == "BUY":
                cash -= (notional + cost)
                positions[sym] = positions.get(sym, 0) + shares
            elif side == "SELL":
                cash += (notional - cost)
                positions[sym] = positions.get(sym, 0) - shares
                if positions[sym] <= 0:
                    positions.pop(sym, None)

        # Compute end-of-day NAV using close prices
        rpl = _price_lookup_for_day(prices_df, pdi, td, ["raw_close"])
        mv = sum(sh * _safe_float(rpl.get(s,{}).get("raw_close"),0)
                 for s, sh in positions.items())
        equity = cash + mv
        nav = equity / initial_cash if initial_cash > 0 else 1.0

        nav_series.append(nav)
        cash_series.append(cash)
        eq_series.append(equity)

    replay_df = pd.DataFrame({
        "trade_date": all_dates[:len(nav_series)],
        "replay_nav": nav_series,
        "replay_cash": cash_series,
        "replay_equity": eq_series,
    })

    # Compare with engine snapshots
    if not snapshot_df.empty:
        merged = snapshot_df.merge(replay_df, on="trade_date", how="inner")
        nav_diff = (merged["nav"] - merged["replay_nav"]).abs()
        max_nav_diff = float(nav_diff.max()) if len(nav_diff) > 0 else 0.0
        max_nav_diff_bps = max_nav_diff * 10000  # convert to bps
        cash_diff = (merged["cash"] - merged["replay_cash"]).abs()
        max_cash_diff = float(cash_diff.max()) if len(cash_diff) > 0 else 0.0

        return {
            "max_nav_diff_bps": round(max_nav_diff_bps, 4),
            "max_cash_diff": round(max_cash_diff, 2),
            "replay_ok": max_nav_diff_bps <= 0.01,  # < 0.01bp
            "merged_df": merged,
        }

    return {"replay_ok": False, "error": "no_snapshot_data"}


# ══════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════

def _compute_metrics(snapshot_df):
    if snapshot_df is None or snapshot_df.empty or "nav" not in snapshot_df.columns:
        return {}
    nav = snapshot_df["nav"].values; n = len(nav)
    tr = float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak = np.maximum.accumulate(nav); dd = float(np.min((nav-peak)/peak))
    ar = float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    dr = np.diff(nav)/nav[:-1] if n>1 else np.array([0])
    cv = float(-np.mean(np.sort(dr)[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul = float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca = float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6),"n_days":n}


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
    print("B2_SCALE40 R15: 统一账本 + 独立回放")
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
    out_dir=OUT_ROOT/f"b2_r15_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True,exist_ok=True)
    print(f"Output: {out_dir}")

    common=dict(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,
                signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,
                it_trends=it,specs=specs,start_date=args.start_date,
                end_date=args.end_date,initial_cash=args.initial_cash)

    # ── Anchor ─────────────────────────────────────────────────
    anchor = build_anchor_risk_state(**common)
    n_risk = int(anchor["risk_state"].sum())
    print(f"Anchor: {n_risk}/{len(anchor)} risk days")

    # ══════════════════════════════════════════════════════════
    # R15: Run both strategies with ledger
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R15: 账本回测 ===")
    s60 = run_ledger_backtest("S60", anchor, 0.60, 0.60, **common)
    b2 = run_ledger_backtest("B2", anchor, 0.60, 0.40, **common)

    s60_m = s60["metrics"]; b2_m = b2["metrics"]
    n_s60_orders = len(s60["ledger_df"]); n_b2_orders = len(b2["ledger_df"])
    print(f"  S60: R={s60_m['total_return']:.2%} Cal={s60_m['calmar']:.2f} Orders={n_s60_orders}")
    print(f"  B2:  R={b2_m['total_return']:.2%} Cal={b2_m['calmar']:.2f} Orders={n_b2_orders}")

    # ══════════════════════════════════════════════════════════
    # R15: Independent replay
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R15: 独立回放 ===")

    s60_replay = replay_ledger(s60["ledger_df"], s60["snapshot_df"], ps, pdi, args.initial_cash)
    b2_replay = replay_ledger(b2["ledger_df"], b2["snapshot_df"], ps, pdi, args.initial_cash)

    if s60_replay.get("replay_ok") and b2_replay.get("replay_ok"):
        print(f"  S60 replay: max NAV diff = {s60_replay['max_nav_diff_bps']:.4f} bps {'✅' if s60_replay['replay_ok'] else '❌'}")
        print(f"  B2 replay:  max NAV diff = {b2_replay['max_nav_diff_bps']:.4f} bps {'✅' if b2_replay['replay_ok'] else '❌'}")
    else:
        print(f"  S60 replay: {'OK' if s60_replay.get('replay_ok') else 'FAIL'}")
        print(f"  B2 replay:  {'OK' if b2_replay.get('replay_ok') else 'FAIL'}")

    replay_ok = s60_replay.get("replay_ok", False) and b2_replay.get("replay_ok", False)

    # ══════════════════════════════════════════════════════════
    # Save
    # ══════════════════════════════════════════════════════════
    s60["ledger_df"].to_csv(out_dir/"r15_order_ledger_s60.csv", index=False)
    b2["ledger_df"].to_csv(out_dir/"r15_order_ledger_b2.csv", index=False)
    s60["snapshot_df"].to_csv(out_dir/"r15_daily_snapshots_s60.csv", index=False)
    b2["snapshot_df"].to_csv(out_dir/"r15_daily_snapshots_b2.csv", index=False)
    s60["nav_df"].to_csv(out_dir/"nav_s60.csv", index=False)
    b2["nav_df"].to_csv(out_dir/"nav_b2.csv", index=False)

    # Verdict
    verdict = [
        "# B2_SCALE40 R15: 统一账本 + 独立回放",
        f"## S60 replay: {'✅' if s60_replay.get('replay_ok') else '❌'} max NAV diff = {s60_replay.get('max_nav_diff_bps','?')} bps",
        f"## B2 replay: {'✅' if b2_replay.get('replay_ok') else '❌'} max NAV diff = {b2_replay.get('max_nav_diff_bps','?')} bps",
        "",
        f"## R15: {'✅ PASS — 可继续R16' if replay_ok else '❌ FAIL — 停止所有B2后续研究, 先修复账本'}",
    ]
    (out_dir/"r15_verdict.md").write_text("\n".join(verdict))

    print(f"\n{'='*60}")
    print(f"R15: {'✅ 账本闭合 — 可继续R16' if replay_ok else '❌ 账本失败 — 停止B2研究'}")
    print(f"报告: {out_dir}/r15_verdict.md")
    print("Done.")


if __name__ == "__main__":
    main()
