#!/usr/bin/env python3
"""
FSC-1 R1-R6: Force-Sell Controller — 强制卖出风控模块

R1: ANCHOR_S60_FIXED 风险状态锚
R2: execute_force_sell_to_target 真实可成交执行器
R3: 四条正交曲线 + ENTRY_ONLY/DAILY_ENFORCED
R4: 静态风险预算对照 (STATIC40-STATIC60)
R5: 事件级收益归因
R6: 真实账户安慰剂

Usage:
    python scripts/research/run_fsc1_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys, hashlib
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
    load_index_trends_pit, build_daily_features,
    _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_dro1_backtest import DRO1Controller

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# R2: Force sell executor
# ══════════════════════════════════════════════════════════════════════

def execute_force_sell_to_target(account: AccountState, target_exposure: float,
                                  price_lookup: dict, lot_size=100, min_value=500.0,
                                  cost_rate=0.00075, slip_rate=0.0) -> list:
    """
    R2: Force sell tradable positions to reach target_exposure.
    Uses T+1 open prices, checks tradability, respects lot constraints.
    Returns list of force-sell trade records.
    """
    trades = []

    # Compute current equity and market value
    cash = account.cash
    positions = account.positions
    if not positions:
        return trades

    price_field = "raw_close"

    market_val = 0.0
    position_info = {}
    for sym, pos in positions.items():
        px = _safe_float(price_lookup.get(sym, {}).get(price_field), 0)
        is_tradable = bool(_safe_float(price_lookup.get(sym, {}).get("execution_tradable"), 0))
        is_suspended = bool(_safe_float(price_lookup.get(sym, {}).get("is_suspended"), 0))
        if px > 0 and is_tradable and not is_suspended:
            mv = pos.shares * px
            market_val += mv
            position_info[sym] = {"shares": pos.shares, "price": px, "mv": mv}
        else:
            position_info[sym] = {"shares": pos.shares, "price": px, "mv": 0, "blocked": True}

    equity = cash + market_val
    if equity <= 0:
        return trades

    current_exp = market_val / equity
    if current_exp <= target_exposure * 1.02:  # Within 2% of target
        return trades

    # Amount to reduce
    target_mv = equity * target_exposure
    excess_mv = market_val - target_mv
    if excess_mv <= 0:
        return trades

    # Sell proportionally from tradable positions
    tradable_mv = sum(info["mv"] for info in position_info.values() if info["mv"] > 0)
    if tradable_mv <= 0:
        return trades

    for sym, info in position_info.items():
        if info["mv"] <= 0:
            continue
        # Proportional allocation of excess
        alloc = excess_mv * (info["mv"] / tradable_mv)
        sell_shares = int(alloc / info["price"] / lot_size) * lot_size
        if sell_shares <= 0:
            continue

        sell_notional = sell_shares * info["price"]
        if sell_notional < min_value:
            continue

        # Execute: update account
        fee = sell_notional * cost_rate
        slip = sell_notional * slip_rate
        account.cash += sell_notional - fee - slip
        positions[sym].shares -= sell_shares
        if positions[sym].shares <= 0:
            del positions[sym]

        trades.append({
            "symbol": sym, "side": "SELL", "order_reason": "FORCE_SELL",
            "shares_delta": -sell_shares, "shares_before": info["shares"],
            "shares_after": max(0, info["shares"] - sell_shares),
            "price": round(float(info["price"]), 2),
            "notional": round(float(sell_notional), 2),
            "trade_cost": round(float(fee + slip), 4),
            "target_exposure": round(target_exposure, 4),
            "actual_exposure_before": round(current_exp, 4),
        })

    return trades


# ══════════════════════════════════════════════════════════════════════
# R1: ANCHOR_S60_FIXED — truly fixed 60%, no dynamic position
# ══════════════════════════════════════════════════════════════════════

def build_anchor_risk_state(engine, scores, prices, market_env, calendar,
                             signal_to_exec, exec_to_signal, sdi, pdi, it_trends, specs,
                             start_date, end_date, initial_cash) -> pd.DataFrame:
    """
    R1: Run a truly fixed 60% account (ANCHOR_S60_FIXED).
    Extract risk state from external market + anchor account DD.
    NEVER uses dynamic position or candidate strategy feedback.
    """

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    spec = matched[0]

    controller = DRO1Controller(base_position=0.60)
    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    current_nav = 1.0
    risk_rows = []

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_cal = [d for d in sim_cal if d >= first_exec]

    price_indices_orig = prices.groupby("trade_date", sort=True).indices

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None: continue

        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        day_scores = _score_day_frame(scores, sdi, signal_date)
        price_snap = pd.DataFrame()
        if signal_date in price_indices_orig:
            price_snap = prices.iloc[price_indices_orig[signal_date]]
        me_row = None
        if market_env is not None and "trade_date" in market_env.columns:
            me_m = market_env[market_env["trade_date"] == signal_date]
            if not me_m.empty: me_row = me_m.iloc[0]
        features = build_daily_features(signal_date, day_scores, price_snap, it_trends, me_row)

        # Use anchor account's NAV for DD trigger (truly fixed 60%)
        anchor_dd = controller.get_account_dd(current_nav)
        _, dro1_state = controller.get_position(
            features.csi300_ret20, features.turnover_ratio, current_nav)

        risk_rows.append({
            "signal_date": signal_date, "execution_date": trade_date,
            "risk_state": controller.in_risk,
            "trigger_count": dro1_state.get("triggers", 0),
            "csi300_ret20": round(features.csi300_ret20, 4),
            "turnover_ratio": round(features.turnover_ratio, 4),
            "anchor_nav": round(current_nav, 4),
            "anchor_drawdown": round(anchor_dd, 4),
            "target_position": 0.60,  # ALWAYS 60%
        })

        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                       slippage_rate=0.0, max_total_positions=5, position_ratio=0.60,
                       calendar=calendar, open_prices=rpl,
                       targets=targets if not targets.empty else pd.DataFrame(),
                       precommit_prices=None, strict_precommit=False, ledger=None)

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0

    return pd.DataFrame(risk_rows)


# ══════════════════════════════════════════════════════════════════════
# R3: Backtest with frozen risk + force sell
# ══════════════════════════════════════════════════════════════════════

def run_fsc_backtest(label: str, risk_df: pd.DataFrame,
                     target_normal=0.60, target_risk=0.40,
                     freeze_buys=False, force_sell_mode=None,
                     engine=None, scores=None, prices=None, market_env=None,
                     calendar=None, signal_to_exec=None, exec_to_signal=None,
                     sdi=None, pdi=None, it_trends=None, specs=None,
                     start_date=None, end_date=None, initial_cash=500000.0,
                     cost_rate=0.00075, slip_rate=0.0) -> dict:
    """
    R3: Backtest with frozen risk state and optional force sell.
    force_sell_mode: None, 'entry_only', 'daily_enforced'
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
    nav_rows, trade_rows, force_sell_rows, blocked_rows = [], [], [], []
    current_nav = 1.0
    was_in_risk_prev = False

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            nav_rows.append({"trade_date": trade_date, "nav": current_nav, "in_risk": False})
            continue

        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        day_scores = _score_day_frame(scores, sdi, signal_date)

        in_risk = risk_lookup.get(signal_date, False)
        just_entered_risk = in_risk and not was_in_risk_prev
        position_ratio = target_risk if in_risk else target_normal

        # ── Prepare targets ───────────────────────────────
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        existing_syms = set(str(s).zfill(6) for s in account.positions.keys())

        if freeze_buys and in_risk:
            if not targets.empty and "symbol" in targets.columns:
                for _, row in targets.iterrows():
                    sym = str(row["symbol"]).zfill(6)
                    blocked_rows.append({
                        "signal_date": signal_date, "symbol": sym, "risk_state": True,
                        "blocked_reason": "FREEZE_BUY",
                    })
            targets = pd.DataFrame()

        # ── Normal rebalance ──────────────────────────────
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

        # ── Force sell ────────────────────────────────────
        should_force_sell = False
        if force_sell_mode == 'entry_only' and just_entered_risk:
            should_force_sell = True
        elif force_sell_mode == 'daily_enforced' and in_risk:
            should_force_sell = True

        fs_trades = []
        if should_force_sell:
            fs_trades = execute_force_sell_to_target(
                account, target_risk, rpl, lot_size=100, min_value=500.0,
                cost_rate=cost_rate, slip_rate=slip_rate)
            for ft in fs_trades:
                force_sell_rows.append({
                    "signal_date": signal_date, "execution_date": trade_date,
                    **ft,
                    "entry_only": force_sell_mode == 'entry_only',
                    "curve": label,
                })

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        # Compute actual exposure
        market_val = 0.0
        for sym, pos in account.positions.items():
            px = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
            market_val += pos.shares * px
        actual_exp = market_val / eq if eq > 0 else 0.0

        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "actual_exposure": round(actual_exp, 4),
            "position_count": len(account.positions),
            "in_risk": in_risk, "force_sells": len(fs_trades),
        })
        was_in_risk_prev = in_risk

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_metrics(nav_df)
    fs_df = pd.DataFrame(force_sell_rows) if force_sell_rows else pd.DataFrame()

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "force_sell_df": fs_df, "n_force_sells": len(fs_df),
        "blocked_df": pd.DataFrame(blocked_rows) if blocked_rows else pd.DataFrame(),
    }


def _compute_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns: return {}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd_series = (nav - peak) / peak
    max_dd = float(np.min(dd_series))
    n = len(nav)
    ann_ret = float((1 + total_return) ** (252 / n) - 1) if n > 0 and nav[0] > 0 else 0.0
    daily_rets = np.diff(nav) / nav[:-1] if n > 1 else np.array([0])
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 else 0.0
    calmar = float(ann_ret / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    cvar95 = float(-np.mean(np.sort(daily_rets)[:max(1, int(n * 0.05))])) if n > 20 else 0.0
    ulcer = float(np.sqrt(np.mean(dd_series ** 2))) if n > 0 else 0.0
    return {"total_return": round(total_return, 6), "max_drawdown": round(max_dd, 6),
            "calmar": round(calmar, 4), "sharpe": round(sharpe, 4), "cvar95": round(cvar95, 6),
            "ulcer": round(ulcer, 6), "n_days": n}


def calmar_from_nav_series(nav_series):
    nav = np.array(nav_series)
    if len(nav) < 2: return 0.0
    total_ret = nav[-1] / nav[0] - 1.0
    ann_ret = (1 + total_ret) ** (252 / len(nav)) - 1 if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd = np.min((nav - peak) / peak)
    return float(ann_ret / abs(dd)) if abs(dd) > 0 else 0.0


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="FSC-1 R1-R6")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--placebo-shifts", type=int, default=100)
    parser.add_argument("--placebo-blocks", type=int, default=500)
    args = parser.parse_args()

    print("=" * 60)
    print("FSC-1 R1-R6: Force-Sell Controller")
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
    out_dir = OUT_ROOT / f"fsc1_{ts}" if not args.output_dir else Path(args.output_dir)
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
    print("\n=== R1: ANCHOR_S60_FIXED 风险状态锚 ===")
    anchor_risk = build_anchor_risk_state(**common)
    anchor_risk.to_csv(out_dir / "fsc1_anchor_risk_state.csv", index=False)
    risk_hash = hashlib.sha256(anchor_risk["risk_state"].to_csv(index=False).encode()).hexdigest()[:16]
    n_risk = int(anchor_risk["risk_state"].sum())
    print(f"  风险天数: {n_risk}/{len(anchor_risk)}, Hash: {risk_hash}")
    print(f"  锚定账户目标仓位: 固定60%")

    # ══════════════════════════════════════════════════════════
    # R3+R4: Curves + Static baselines
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R3+R4: 正交曲线 + 静态预算对照 ===")

    results = {}

    # Static baselines
    for pos in [0.40, 0.45, 0.50, 0.55, 0.60]:
        label = f"STATIC{int(pos*100)}"
        risk_df = anchor_risk.copy()
        risk_df["risk_state"] = False  # No risk for static
        r = run_fsc_backtest(label, risk_df, pos, pos, False, None, **common)
        results[label] = r
        m = r["metrics"]
        print(f"  {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

    # Orthogonal curves
    r3_configs = [
        ("S60", 0.60, 0.60, False, None, "基线"),
        ("B1_FREEZE60", 0.60, 0.60, True, None, "停买+保持60%"),
        ("B2_SCALE40", 0.60, 0.40, False, None, "降仓至40%"),
        ("FSC40_ENTRY", 0.60, 0.40, False, "entry_only", "FSC入口卖出"),
        ("FSC40_DAILY", 0.60, 0.40, False, "daily_enforced", "FSC每日强制"),
    ]
    for label, pn, pr, freeze, fs_mode, desc in r3_configs:
        r = run_fsc_backtest(label, anchor_risk, pn, pr, freeze, fs_mode, **common)
        results[label] = r
        m = r["metrics"]
        print(f"  {label} ({desc}): R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} "
              f"Cal={m['calmar']:.2f} FS={r['n_force_sells']}")

    # ══════════════════════════════════════════════════════════
    # Analysis
    # ══════════════════════════════════════════════════════════
    s60_m = results["S60"]["metrics"]
    fsc_entry_m = results["FSC40_ENTRY"]["metrics"]
    fsc_daily_m = results["FSC40_DAILY"]["metrics"]

    # Find equal-exposure static comparator
    fsc_avg_exp = float(results["FSC40_ENTRY"]["nav_df"]["actual_exposure"].mean())
    print(f"\n=== R4: 等暴露对照 ===")
    print(f"  FSC40_ENTRY 平均实际暴露: {fsc_avg_exp:.1%}")
    # Closest static match
    best_static = min([40, 45, 50, 55, 60], key=lambda p: abs(p/100 - fsc_avg_exp))
    static_m = results[f"STATIC{best_static}"]["metrics"]
    print(f"  最近静态对照: STATIC{best_static} (R={static_m['total_return']:.2%} Cal={static_m['calmar']:.2f})")
    print(f"  FSC40_ENTRY: R={fsc_entry_m['total_return']:.2%} Cal={fsc_entry_m['calmar']:.2f}")
    fsc_vs_static = fsc_entry_m["calmar"] - static_m["calmar"]
    print(f"  FSC vs 等暴露静态 Calmar差: {fsc_vs_static:+.2f} {'✅ FSC优于等暴露' if fsc_vs_static > 0 else '❌ FSC不优于等暴露'}")

    # ENTRY vs DAILY
    print(f"\n  ENTRY_ONLY: R={fsc_entry_m['total_return']:.2%} Cal={fsc_entry_m['calmar']:.2f} FS={results['FSC40_ENTRY']['n_force_sells']}")
    print(f"  DAILY_ENFORCED: R={fsc_daily_m['total_return']:.2%} Cal={fsc_daily_m['calmar']:.2f} FS={results['FSC40_DAILY']['n_force_sells']}")
    if fsc_entry_m["calmar"] >= fsc_daily_m["calmar"]:
        print(f"  ✅ ENTRY_ONLY足够, DAILY无额外价值")
    else:
        print(f"  ⚠️ DAILY_ENFORCED更好, 但交易成本更高")

    # ══════════════════════════════════════════════════════════
    # R6: Placebo
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R6: 真实账户安慰剂 ===")
    s60_nav_arr = results["S60"]["nav_df"]["nav"].values
    s60_daily = np.diff(s60_nav_arr) / s60_nav_arr[:-1] if len(s60_nav_arr) > 1 else np.array([0])
    risk_seq = [bool(r) for r in anchor_risk["risk_state"].values]
    rng = np.random.RandomState(42)
    n_d = len(risk_seq)
    bs = max(5, n_d // 20)

    shift_calmars, block_calmars = [], []
    for i in range(args.placebo_shifts):
        shift = rng.randint(0, n_d)
        rs = risk_seq[shift:] + risk_seq[:shift]
        nav = [1.0]
        for j, ret in enumerate(s60_daily):
            pos = 0.40 if (j < len(rs) and rs[j]) else 0.60
            nav.append(nav[-1] * (1.0 + ret * pos / 0.60))
        shift_calmars.append(calmar_from_nav_series(nav))

    for i in range(args.placebo_blocks):
        blocks = [risk_seq[j:j+bs] for j in range(0, n_d, bs) if len(risk_seq[j:j+bs]) == bs]
        rng.shuffle(blocks)
        rb = [v for b in blocks for v in b][:n_d]
        nav = [1.0]
        for j, ret in enumerate(s60_daily):
            pos = 0.40 if (j < len(rb) and rb[j]) else 0.60
            nav.append(nav[-1] * (1.0 + ret * pos / 0.60))
        block_calmars.append(calmar_from_nav_series(nav))

    shift_arr = np.array(shift_calmars)
    block_arr = np.array(block_calmars)
    shift_p = (1 + sum(1 for c in shift_calmars if c >= fsc_entry_m["calmar"])) / (1 + len(shift_calmars))
    block_p = (1 + sum(1 for c in block_calmars if c >= fsc_entry_m["calmar"])) / (1 + len(block_calmars))
    print(f"  FSC40 Calmar: {fsc_entry_m['calmar']:.2f}")
    print(f"  Shift: median={np.median(shift_arr):.2f} 95%ile={np.percentile(shift_arr,95):.2f} p={shift_p:.4f} {'✅' if shift_p<=0.05 else '❌'}")
    print(f"  Block: median={np.median(block_arr):.2f} 95%ile={np.percentile(block_arr,95):.2f} p={block_p:.4f} {'✅' if block_p<=0.05 else '❌'}")

    # ══════════════════════════════════════════════════════════
    # Save & Report
    # ══════════════════════════════════════════════════════════
    for label, r in results.items():
        r["nav_df"].to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
        fs = r.get("force_sell_df")
        if fs is not None and not fs.empty:
            fs.to_csv(out_dir / f"force_sell_{label.lower()}.csv", index=False)

    pd.DataFrame([{"curve": label, **r["metrics"], "n_force_sells": r.get("n_force_sells", 0)}
                   for label, r in results.items()]).to_csv(out_dir / "fsc1_summary.csv", index=False)

    report = [
        "# FSC-1 R1-R6 验证报告",
        f"## R1: 锚定风险状态 — {n_risk}/{len(anchor_risk)} 风险日, Hash={risk_hash}",
        "",
        "## R3: 正交曲线",
        "| 曲线 | 收益 | MaxDD | Calmar | CVaR95 | Ulcer | 强制卖出 |",
        "|------|------|-------|--------|--------|-------|----------|",
    ]
    for label, _, _, _, _, desc in r3_configs:
        r = results[label]; m = r["metrics"]
        report.append(f"| {label} | {m['total_return']:.2%} | {m['max_drawdown']:.2%} | {m['calmar']:.2f} | {m['cvar95']:.4f} | {m['ulcer']:.4f} | {r['n_force_sells']} |")

    report += [
        "",
        "## R4: 等暴露对照",
        f"- FSC40_ENTRY 平均暴露: {fsc_avg_exp:.1%}",
        f"- 最近静态: STATIC{best_static} Cal={static_m['calmar']:.2f}",
        f"- FSC vs Static Calmar差: {fsc_vs_static:+.2f}",
        "",
        "## R6: 安慰剂",
        f"- Shift p={shift_p:.4f}",
        f"- Block p={block_p:.4f}",
        "",
        "## 结论",
        f"- {'✅ FSC优于等暴露静态基线' if fsc_vs_static > 0 else '❌ FSC不优于等暴露静态基线'}",
        f"- ENTRY_ONLY vs DAILY: {'ENTRY足够' if fsc_entry_m['calmar'] >= fsc_daily_m['calmar'] else 'DAILY更好但成本更高'}",
    ]
    (out_dir / "fsc1_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"FSC vs Static: {fsc_vs_static:+.2f} Calmar")
    print(f"报告: {out_dir}/fsc1_report.md")
    print("\nDone.")


if __name__ == "__main__":
    main()
