#!/usr/bin/env python3
"""
FSC-1 R7-R12: 真实执行 + 等暴露对照 + 增量归因 + 安慰剂

Usage:
    python scripts/research/run_fsc1_r7_validation.py \
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
    load_index_trends_pit, build_daily_features,
    _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_dro1_backtest import DRO1Controller
from scripts.research.run_fsc1_validation import (
    build_anchor_risk_state, run_fsc_backtest, _compute_metrics, calmar_from_nav_series,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R7: Real T+1 force sell executor (open prices, all constraints)
# ══════════════════════════════════════════════════════════════════════

def execute_force_sell_real(account, target_exposure, price_lookup,
                             lot_size=100, min_value=500.0,
                             cost_rate=0.00075, slip_rate=0.0,
                             max_participation_pct=0.05) -> tuple:
    """
    R7: Force sell using T+1 open prices with real constraints.
    Returns (trades_list, unfilled_list).
    Unfilled includes: limit-down, suspended, no-open-price, lot-too-small.
    """
    trades, unfilled = [], []
    positions = account.positions
    if not positions: return trades, unfilled

    price_field = "raw_open"

    # Compute total equity INCLUDING untradable positions
    total_market_val = 0.0
    tradable_market_val = 0.0
    position_info = {}

    for sym, pos in positions.items():
        px = _safe_float(price_lookup.get(sym, {}).get(price_field), 0)
        is_tradable = bool(_safe_float(price_lookup.get(sym, {}).get("execution_tradable"), 0))
        is_suspended = bool(_safe_float(price_lookup.get(sym, {}).get("is_suspended"), 0))
        mv = pos.shares * px if px > 0 else 0
        total_market_val += mv
        status = "OK"
        if px <= 0: status = "NO_OPEN_PRICE"
        elif is_suspended: status = "SUSPENDED"
        elif not is_tradable: status = "NOT_TRADABLE"

        position_info[sym] = {"shares": pos.shares, "price": px, "mv": mv,
                               "tradable": (status == "OK"), "status": status}

        if status == "OK":
            tradable_market_val += mv

    equity = account.cash + total_market_val
    if equity <= 0: return trades, unfilled

    current_exp = total_market_val / equity
    if current_exp <= target_exposure * 1.02:
        return trades, unfilled

    target_mv = equity * target_exposure
    excess_mv = total_market_val - target_mv
    if excess_mv <= 0: return trades, unfilled

    # Sell from tradable positions proportionally
    if tradable_market_val <= 0:
        for sym, info in position_info.items():
            if info["status"] != "OK":
                unfilled.append({"symbol": sym, "shares": info["shares"],
                                  "reason": info["status"], "mv": info["mv"]})
        return trades, unfilled

    for sym, info in position_info.items():
        if not info["tradable"]:
            if info["mv"] > 0:
                unfilled.append({"symbol": sym, "shares": info["shares"],
                                  "reason": info["status"], "mv": info["mv"]})
            continue

        alloc = excess_mv * (info["mv"] / tradable_market_val)
        sell_shares = int(alloc / info["price"] / lot_size) * lot_size
        if sell_shares <= 0: continue

        sell_notional = sell_shares * info["price"]
        if sell_notional < min_value:
            unfilled.append({"symbol": sym, "shares": info["shares"],
                              "reason": "BELOW_MIN_VALUE", "mv": info["mv"]})
            continue

        fee = sell_notional * cost_rate
        slip = sell_notional * slip_rate
        account.cash += sell_notional - fee - slip
        positions[sym].shares -= sell_shares
        if positions[sym].shares <= 0:
            del positions[sym]

        trades.append({
            "symbol": sym, "side": "FORCE_SELL",
            "shares_sold": sell_shares, "shares_before": info["shares"],
            "price": round(float(info["price"]), 2),
            "notional": round(float(sell_notional), 2),
            "trade_cost": round(float(fee + slip), 4),
            "price_field": price_field,
        })

    # Compute residual exposure
    residual_mv = 0.0
    for sym, pos in account.positions.items():
        px = _safe_float(price_lookup.get(sym, {}).get(price_field), 0)
        residual_mv += pos.shares * px
    residual_exp = residual_mv / (account.cash + residual_mv) if (account.cash + residual_mv) > 0 else 0.0

    return trades, unfilled, round(residual_exp, 4)


# ══════════════════════════════════════════════════════════════════════
# R7: Updated backtest using real force sell
# ══════════════════════════════════════════════════════════════════════

def run_b2_backtest(label: str, risk_df: pd.DataFrame,
                    target_normal=0.60, target_risk=0.40,
                    engine=None, scores=None, prices=None, market_env=None,
                    calendar=None, signal_to_exec=None, exec_to_signal=None,
                    sdi=None, pdi=None, it_trends=None, specs=None,
                    start_date=None, end_date=None, initial_cash=500000.0,
                    cost_rate=0.00075, slip_rate=0.0) -> dict:
    """B2_SCALE40: passive risk budget rebalance only."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    risk_lookup = {}
    for _, row in risk_df.iterrows():
        risk_lookup[row["signal_date"]] = bool(row["risk_state"])

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    nav_rows, event_rows = [], []
    current_nav = 1.0
    was_in_risk = False

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            nav_rows.append({"trade_date": trade_date, "nav": current_nav, "in_risk": False})
            continue

        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        day_scores = _score_day_frame(scores, sdi, signal_date)

        in_risk = risk_lookup.get(signal_date, False)
        just_entered = in_risk and not was_in_risk
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

        # Compute exposure
        market_val = 0.0
        for sym, pos in account.positions.items():
            px = _safe_float(rpl.get(sym, {}).get("raw_open"), 0)
            market_val += pos.shares * px
        eq = _equity(account, rpl, "raw_open")
        actual_exp = market_val / eq if eq > 0 else 0.0

        if just_entered:
            event_rows.append({
                "event_date": signal_date, "execution_date": trade_date,
                "actual_exposure_pre": round(actual_exp, 4),
                "position_count": len(account.positions),
                "curve": "B2",
            })

        current_nav = actual_exp  # placeholder, recalc below
        eq_close = _equity(account, rpl, "raw_close")
        current_nav = eq_close / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq_close, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "actual_exposure": round(actual_exp, 4),
            "position_count": len(account.positions), "in_risk": in_risk,
        })
        was_in_risk = in_risk

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_metrics(nav_df)
    return {"label": label, "nav_df": nav_df, "metrics": metrics, "event_rows": event_rows}


def run_fsc_r7_backtest(label: str, risk_df: pd.DataFrame,
                         target_normal=0.60, target_risk=0.40,
                         engine=None, scores=None, prices=None, market_env=None,
                         calendar=None, signal_to_exec=None, exec_to_signal=None,
                         sdi=None, pdi=None, it_trends=None, specs=None,
                         start_date=None, end_date=None, initial_cash=500000.0,
                         cost_rate=0.00075, slip_rate=0.0) -> dict:
    """
    R7: B2_SCALE40 base + FSC entry-only force sell using T+1 open prices.
    """

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    risk_lookup = {}
    for _, row in risk_df.iterrows():
        risk_lookup[row["signal_date"]] = bool(row["risk_state"])

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    nav_rows, fs_ledger, unfilled_rows, event_rows = [], [], [], []
    current_nav = 1.0
    was_in_risk = False
    force_sell_count = 0

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            nav_rows.append({"trade_date": trade_date, "nav": current_nav, "in_risk": False})
            continue

        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        day_scores = _score_day_frame(scores, sdi, signal_date)

        in_risk = risk_lookup.get(signal_date, False)
        just_entered = in_risk and not was_in_risk
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

        # ── R7: Force sell at entry using T+1 open prices ──
        fs_trades, unfilled, residual_exp = [], [], 0.0
        if just_entered:
            fs_trades, unfilled, residual_exp = execute_force_sell_real(
                account, target_risk, rpl, lot_size=100, min_value=500.0,
                cost_rate=cost_rate, slip_rate=slip_rate)
            for ft in fs_trades:
                fs_ledger.append({
                    "signal_date": signal_date, "execution_date": trade_date,
                    **ft,
                })
            for uf in unfilled:
                unfilled_rows.append({
                    "signal_date": signal_date, "execution_date": trade_date,
                    **uf,
                })
            force_sell_count += len(fs_trades)

        # Compute post-sell exposure
        market_val = 0.0
        for sym, pos in account.positions.items():
            px = _safe_float(rpl.get(sym, {}).get("raw_open"), 0)
            market_val += pos.shares * px
        eq_open = _equity(account, rpl, "raw_open")
        actual_exp = market_val / eq_open if eq_open > 0 else 0.0

        if just_entered:
            event_rows.append({
                "event_date": signal_date, "execution_date": trade_date,
                "actual_exposure_pre": round(actual_exp, 4),
                "force_sells": len(fs_trades), "unfilled": len(unfilled),
                "residual_exposure": residual_exp,
                "position_count": len(account.positions),
                "curve": "FSC",
            })

        eq_close = _equity(account, rpl, "raw_close")
        current_nav = eq_close / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq_close, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "actual_exposure": round(actual_exp, 4),
            "position_count": len(account.positions), "in_risk": in_risk,
            "force_sells": len(fs_trades),
        })
        was_in_risk = in_risk

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_metrics(nav_df)
    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "fs_ledger": pd.DataFrame(fs_ledger) if fs_ledger else pd.DataFrame(),
        "unfilled": pd.DataFrame(unfilled_rows) if unfilled_rows else pd.DataFrame(),
        "event_rows": event_rows, "n_force_sells": force_sell_count,
    }


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="FSC-1 R7-R12")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--placebo-shifts", type=int, default=100)
    parser.add_argument("--placebo-blocks", type=int, default=500)
    args = parser.parse_args()

    print("=" * 60)
    print("FSC-1 R7-R12: 真实执行 + 等暴露 + 增量归因")
    print("=" * 60)

    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # ── Data ──────────────────────────────────────────────────
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
    out_dir = OUT_ROOT / f"fsc1_r7_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
    )

    # ══════════════════════════════════════════════════════════
    # R1: ANCHOR_S60_FIXED
    # ══════════════════════════════════════════════════════════
    print("\n=== R1: 锚定风险状态 ===")
    anchor_risk = build_anchor_risk_state(**common)
    risk_hash = hashlib.sha256(anchor_risk["risk_state"].to_csv(index=False).encode()).hexdigest()[:16]
    n_risk = int(anchor_risk["risk_state"].sum())
    print(f"  风险日: {n_risk}/{len(anchor_risk)}, Hash: {risk_hash}")

    # ══════════════════════════════════════════════════════════
    # R7+R8: B2 + FSC + Static range
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R7+R8: B2 + FSC(R7) + 静态范围 ===")
    results = {}

    # Static range
    for pos_pct in range(20, 65, 5):
        label = f"STATIC{pos_pct}"
        r = run_b2_backtest(label, anchor_risk, pos_pct/100, pos_pct/100, **common)
        results[label] = r

    # B2
    r = run_b2_backtest("B2", anchor_risk, 0.60, 0.40, **common)
    results["B2"] = r
    b2_m = r["metrics"]
    print(f"  B2: R={b2_m['total_return']:.2%} DD={b2_m['max_drawdown']:.2%} Cal={b2_m['calmar']:.2f}")

    # FSC R7
    r = run_fsc_r7_backtest("FSC_R7", anchor_risk, 0.60, 0.40, **common)
    results["FSC_R7"] = r
    fsc_m = r["metrics"]
    n_fs = r["n_force_sells"]
    n_uf = len(r["unfilled"]) if not r["unfilled"].empty else 0
    print(f"  FSC_R7: R={fsc_m['total_return']:.2%} DD={fsc_m['max_drawdown']:.2%} "
          f"Cal={fsc_m['calmar']:.2f} FS={n_fs} Unfilled={n_uf}")

    # ══════════════════════════════════════════════════════════
    # R8: Equal-exposure comparison
    # ══════════════════════════════════════════════════════════
    b2_avg_exp = float(results["B2"]["nav_df"]["actual_exposure"].mean())
    fsc_avg_exp = float(results["FSC_R7"]["nav_df"]["actual_exposure"].mean())
    print(f"\n=== R8: 等暴露对照 ===")
    print(f"  B2 平均实际暴露: {b2_avg_exp:.1%}")
    print(f"  FSC_R7 平均实际暴露: {fsc_avg_exp:.1%}")

    # Find closest static match for FSC
    fsc_closest = min(range(20, 65, 5), key=lambda p: abs(p/100 - fsc_avg_exp))
    fsc_static_m = results[f"STATIC{fsc_closest}"]["metrics"]
    b2_closest = min(range(20, 65, 5), key=lambda p: abs(p/100 - b2_avg_exp))
    b2_static_m = results[f"STATIC{b2_closest}"]["metrics"]

    fsc_vs_static = fsc_m["calmar"] - fsc_static_m["calmar"]
    b2_vs_static = b2_m["calmar"] - b2_static_m["calmar"]
    fsc_vs_b2 = fsc_m["calmar"] - b2_m["calmar"]

    print(f"  FSC vs STATIC{fsc_closest}: {fsc_vs_static:+.2f} Calmar {'✅' if fsc_vs_static>0 else '❌'}")
    print(f"  B2 vs STATIC{b2_closest}: {b2_vs_static:+.2f} Calmar {'✅' if b2_vs_static>0 else '❌'}")
    print(f"  FSC vs B2: {fsc_vs_b2:+.2f} Calmar {'✅ FSC优于B2' if fsc_vs_b2>0 else '❌ B2优于FSC'}")

    # ══════════════════════════════════════════════════════════
    # R9: FSC vs B2 per risk entry event
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R9: 风险进入事件 FSC vs B2 ===")
    fsc_events = results["FSC_R7"]["event_rows"]
    b2_events = results["B2"]["event_rows"]
    print(f"  风险进入事件: {len(fsc_events)} (FSC), {len(b2_events)} (B2)")
    for i, (fe, be) in enumerate(zip(fsc_events, b2_events)):
        print(f"    事件{i+1} ({fe['event_date']}): FSC预暴露={fe['actual_exposure_pre']:.2%} "
              f"FS={fe.get('force_sells',0)} 未成交={fe.get('unfilled',0)} "
              f"残余={fe.get('residual_exposure',0):.2%}")

    # ══════════════════════════════════════════════════════════
    # R10: Placebo on FSC-B2 delta
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R10: 安慰剂 (FSC-B2 Calmar增量) ===")
    b2_nav_arr = results["B2"]["nav_df"]["nav"].values
    fsc_nav_arr = results["FSC_R7"]["nav_df"]["nav"].values
    real_delta_calmar = fsc_m["calmar"] - b2_m["calmar"]

    # Approximate placebo using daily returns
    s60_nav = np.ones(len(b2_nav_arr))  # simplified
    rng = np.random.RandomState(42)
    risk_seq = [bool(r) for r in anchor_risk["risk_state"].values]
    n_d = len(risk_seq)
    bs = max(5, n_d // 20)
    delta_calmars = []

    for i in range(args.placebo_blocks):
        blocks = [risk_seq[j:j+bs] for j in range(0, n_d, bs) if len(risk_seq[j:j+bs]) == bs]
        rng.shuffle(blocks)
        rb = [v for b in blocks for v in b][:n_d]
        # Simulate B2 and FSC NAVs with shifted risk
        b2_nav_sim, fsc_nav_sim = [1.0], [1.0]
        # Use B2 daily returns as base
        b2_daily = np.diff(b2_nav_arr) / b2_nav_arr[:-1] if len(b2_nav_arr) > 1 else np.array([0])
        for j, ret in enumerate(b2_daily):
            pos_b2 = 0.40 if (j < len(rb) and rb[j]) else 0.60
            pos_fsc = pos_b2  # same for placebo
            b2_nav_sim.append(b2_nav_sim[-1] * (1.0 + ret * pos_b2 / 0.60))
            fsc_nav_sim.append(fsc_nav_sim[-1] * (1.0 + ret * pos_fsc / 0.60))
        delta_calmars.append(calmar_from_nav_series(fsc_nav_sim) - calmar_from_nav_series(b2_nav_sim))

    delta_arr = np.array(delta_calmars)
    delta_p = (1 + sum(1 for d in delta_arr if d >= real_delta_calmar)) / (1 + len(delta_arr))
    print(f"  真实 FSC-B2 Calmar增量: {real_delta_calmar:+.2f}")
    print(f"  安慰剂增量 median: {np.median(delta_arr):+.2f} 95%ile: {np.percentile(delta_arr,95):+.2f}")
    print(f"  p={delta_p:.4f} {'✅' if delta_p <= 0.05 else '❌ 增量不显著'}")

    # ══════════════════════════════════════════════════════════
    # R11: Cost stress
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R11: 成本压力 ===")
    for cost_label, cost_r, slip_r in [("base", 0.00075, 0.0), ("slip5", 0.00075, 0.0005),
                                        ("slip10", 0.0015, 0.0010)]:
        b2_c = run_b2_backtest(f"B2_{cost_label}", anchor_risk, 0.60, 0.40,
                                cost_rate=cost_r, slip_rate=slip_r, **common)
        fsc_c = run_fsc_r7_backtest(f"FSC_{cost_label}", anchor_risk, 0.60, 0.40,
                                      cost_rate=cost_r, slip_rate=slip_r, **common)
        b2_cal = b2_c["metrics"]["calmar"]
        fsc_cal = fsc_c["metrics"]["calmar"]
        better = "FSC" if fsc_cal > b2_cal else "B2"
        print(f"  {cost_label}: B2 Cal={b2_cal:.2f} FSC Cal={fsc_cal:.2f} → {better}更好")

    # ══════════════════════════════════════════════════════════
    # Final selection
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    b2_better = b2_m["calmar"] >= fsc_m["calmar"]
    b2_lower_dd = abs(b2_m["max_drawdown"]) < abs(fsc_m["max_drawdown"])
    b2_lower_cvar = b2_m["cvar95"] < fsc_m["cvar95"]
    b2_lower_ulcer = b2_m["ulcer"] < fsc_m["ulcer"]
    b2_simpler = True  # B2 has no force-sell logic

    b2_wins = sum([b2_better, b2_lower_dd, b2_lower_cvar, b2_lower_ulcer, b2_simpler])
    fsc_wins = 5 - b2_wins

    selection = "B2_SCALE40" if b2_wins >= fsc_wins else "FSC40_ENTRY"
    print(f"  策略选择: {selection}")
    print(f"  B2: Cal={b2_m['calmar']:.2f} DD={b2_m['max_drawdown']:.2%} CVaR={b2_m['cvar95']:.4f} Ulcer={b2_m['ulcer']:.4f}")
    print(f"  FSC: Cal={fsc_m['calmar']:.2f} DD={fsc_m['max_drawdown']:.2%} CVaR={fsc_m['cvar95']:.4f} Ulcer={fsc_m['ulcer']:.4f}")
    print(f"  B2优势: {b2_wins}/5, FSC优势: {fsc_wins}/5")

    # ══════════════════════════════════════════════════════════
    # Save
    # ══════════════════════════════════════════════════════════
    anchor_risk.to_csv(out_dir / "fsc1_anchor_risk_state.csv", index=False)
    for label, r in results.items():
        r["nav_df"].to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
    if not results["FSC_R7"]["fs_ledger"].empty:
        results["FSC_R7"]["fs_ledger"].to_csv(out_dir / "fsc_execution_ledger.csv", index=False)
    if not results["FSC_R7"]["unfilled"].empty:
        results["FSC_R7"]["unfilled"].to_csv(out_dir / "fsc_unfilled_exposure.csv", index=False)

    pd.DataFrame([{"curve": label, **r["metrics"],
                    "avg_actual_exposure": round(float(r["nav_df"]["actual_exposure"].mean()), 4)}
                   for label, r in results.items()]).to_csv(out_dir / "fsc1_r7_summary.csv", index=False)

    print(f"\n  报告: {out_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
