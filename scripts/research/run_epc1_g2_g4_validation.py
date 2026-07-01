#!/usr/bin/env python3
"""
EPC-1 G2-G4: 执行动作消融 + 订单级归因 + 留一事件验证

G2: E0(S60_SCALE) E1(停买) E2(卖出) E3(停买+卖出=EPC-1) E4(+延迟恢复)
G3: D1 vs S60 逐笔订单差异 + forward return
G4: 3次风险事件独立验证

Usage:
    python scripts/research/run_epc1_g2_g4_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys
from dataclasses import dataclass, field
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
    _build_targets_cache, _equity, _trade_day_count, _round_lot,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, build_daily_features,
    _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_dro1_backtest import DRO1Controller, _compute_metrics

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# EPC-1 Controller with action flags
# ══════════════════════════════════════════════════════════════════════

class EPC1Controller(DRO1Controller):
    """
    Extended DRO-1 with per-action control for G2 ablation.
    """
    def __init__(self, allow_new_buys_in_risk=True, force_sells_in_risk=True,
                 delayed_recovery=False, **kwargs):
        super().__init__(**kwargs)
        self.allow_new_buys_in_risk = allow_new_buys_in_risk
        self.force_sells_in_risk = force_sells_in_risk
        self.delayed_recovery = delayed_recovery
        self.recovering = False

    def get_effective_position(self, current_nav: float, features) -> tuple:
        """Returns (target_position, risk_state_dict, action_flags)."""
        target, state = self.get_position(
            features.csi300_ret20, features.turnover_ratio, current_nav)

        # Recovery mode: if just exited risk and delayed_recovery, stay at 40% longer
        if self.delayed_recovery and state["event"] == "EXIT_RISK":
            self.recovering = True
        if self.recovering and state["event"] == "NORMAL":
            # Stay at reduced position for a few days during recovery
            if self.recovery_count < self.recovery_days_needed:
                target = self.risk
            else:
                self.recovering = False

        flags = {
            "allow_new_buys": self.allow_new_buys_in_risk if self.in_risk else True,
            "force_sells": self.force_sells_in_risk if self.in_risk else False,
            "in_risk": self.in_risk,
        }
        return target, state, flags


# ══════════════════════════════════════════════════════════════════════
# G2: Execution action backtest with order tracking
# ══════════════════════════════════════════════════════════════════════

def run_epc1_action_backtest(
    label: str, base_position=0.60,
    allow_new_buys_in_risk=True, force_sells_in_risk=True,
    delayed_recovery=False, use_controller=True,
    track_orders=False,
    engine=None, scores=None, prices=None, market_env=None,
    calendar=None, signal_to_exec=None, exec_to_signal=None,
    sdi=None, pdi=None, it_trends=None, specs=None,
    start_date=None, end_date=None, initial_cash=500000.0,
) -> dict:
    """Run backtest with parameterized EPC-1 actions."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched:
        return {"label": label, "error": "strategy_not_found"}
    spec = matched[0]

    controller = None
    if use_controller:
        controller = EPC1Controller(
            base_position=base_position,
            allow_new_buys_in_risk=allow_new_buys_in_risk,
            force_sells_in_risk=force_sells_in_risk,
            delayed_recovery=delayed_recovery,
        )

    price_columns = [
        "raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
        "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
        "amount", "volume", "security_status_available", "execution_tradable",
        "universe_is_tradable", "is_listed", "circ_mv",
    ]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=5,
    )

    account = AccountState(cash=float(initial_cash))
    nav_rows, order_rows, decision_rows = [], [], []
    current_nav = 1.0

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_cal = [d for d in sim_cal if d >= first_exec]

    price_indices_orig = prices.groupby("trade_date", sort=True).indices

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            nav_rows.append({"trade_date": trade_date, "nav": current_nav,
                             "position_ratio": 0.0, "in_risk": False})
            continue

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

        # ── Position & action flags ──────────────────────
        in_risk = False
        allow_buys = True
        force_sells = False

        if controller is not None:
            position_ratio, dro1_state, flags = controller.get_effective_position(
                current_nav, features)
            in_risk = flags["in_risk"]
            allow_buys = flags["allow_new_buys"]
            force_sells = flags["force_sells"]
        else:
            position_ratio = base_position

        # ── Track orders ──────────────────────────────────
        prev_positions = set(account.positions.keys())
        prev_cash = account.cash

        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            # Filter targets if not allowing new buys during risk
            if not allow_buys and in_risk:
                # Only keep existing positions as targets, no new ones
                existing_syms = set(str(s).zfill(6) for s in account.positions.keys())
                if not targets.empty and "symbol" in targets.columns:
                    targets = targets[targets["symbol"].astype(str).str.zfill(6).isin(existing_syms)]
                if targets.empty:
                    targets = pd.DataFrame()

            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                slippage_rate=0.0, max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=None,
            )

            if track_orders and trades:
                for t in trades:
                    sym = str(t.get("symbol", "")).zfill(6)
                    side = t.get("side", "?")
                    shares = t.get("shares", 0) or 0
                    price = t.get("price", 0) or 0
                    order_rows.append({
                        "trade_date": trade_date, "signal_date": signal_date,
                        "symbol": sym, "side": side, "shares": shares,
                        "price": round(float(price), 2),
                        "in_risk": in_risk, "allow_buys": allow_buys,
                        "force_sells": force_sells,
                        "label": label,
                    })

        # ── Record ──────────────────────────────────────────
        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "in_risk": in_risk,
        })

        if controller is not None:
            decision_rows.append({
                "signal_date": signal_date, "in_risk": in_risk,
                "position_ratio": round(position_ratio, 4),
                "allow_buys": allow_buys, "force_sells": force_sells,
                "event": dro1_state.get("event", ""),
                "triggers": dro1_state.get("triggers", 0),
                "csi300_ret20": dro1_state.get("csi300_ret20", 0),
                "turnover_ratio": dro1_state.get("turnover_ratio", 0),
                "acct_dd": dro1_state.get("acct_dd", 0),
            })

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_metrics(nav_df)
    orders_df = pd.DataFrame(order_rows) if order_rows else pd.DataFrame()

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "orders_df": orders_df, "decisions": decision_rows,
    }


# ══════════════════════════════════════════════════════════════════════
# G3: Order-level P&L attribution
# ══════════════════════════════════════════════════════════════════════

def build_order_attribution(s60_orders: pd.DataFrame, epc_orders: pd.DataFrame,
                             prices_df, pdi) -> pd.DataFrame:
    """Compare orders that differ between S60 and EPC, compute forward returns."""
    if s60_orders.empty or epc_orders.empty:
        return pd.DataFrame()

    price_columns = ["raw_close"]

    rows = []
    # Find orders in EPC that differ from S60
    s60_buys = s60_orders[s60_orders["side"] == "BUY"]
    epc_buys = epc_orders[epc_orders["side"] == "BUY"]
    epc_sells = epc_orders[epc_orders["side"] == "SELL"]

    # Skipped buys: in S60 but NOT in EPC on same date
    for _, s60_row in s60_buys.iterrows():
        td = s60_row["trade_date"]
        sym = s60_row["symbol"]
        epc_match = epc_buys[(epc_buys["trade_date"] == td) & (epc_buys["symbol"] == sym)]
        if epc_match.empty:
            # This buy was skipped by EPC-1
            rpl = _price_lookup_for_day(prices_df, pdi, td, price_columns)
            entry_price = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
            rows.append({
                "trade_date": td, "symbol": sym, "action": "SKIPPED_BUY",
                "s60_shares": s60_row["shares"], "entry_price": round(float(entry_price), 2),
                "in_risk": s60_row.get("in_risk", False),
            })

    # Forced sells: in EPC but NOT in S60
    s60_sells = s60_orders[s60_orders["side"] == "SELL"]
    for _, epc_row in epc_sells.iterrows():
        td = epc_row["trade_date"]
        sym = epc_row["symbol"]
        s60_match = s60_sells[(s60_sells["trade_date"] == td) & (s60_sells["symbol"] == sym)]
        if s60_match.empty and epc_row.get("force_sells", False):
            rpl = _price_lookup_for_day(prices_df, pdi, td, price_columns)
            exit_price = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
            rows.append({
                "trade_date": td, "symbol": sym, "action": "FORCED_SELL",
                "epc_shares": epc_row["shares"], "exit_price": round(float(exit_price), 2),
                "in_risk": True,
            })

    if not rows:
        return pd.DataFrame()

    attr_df = pd.DataFrame(rows)

    # Compute forward returns for each different order
    fwd_5d, fwd_10d, fwd_20d = [], [], []
    price_dates = sorted(pdi.keys())
    for _, row in attr_df.iterrows():
        td = row["trade_date"]
        sym = row["symbol"]
        try:
            idx = price_dates.index(td)
        except ValueError:
            fwd_5d.append(0); fwd_10d.append(0); fwd_20d.append(0)
            continue

        entry_rpl = _price_lookup_for_day(prices_df, pdi, td, price_columns)
        entry_px = _safe_float(entry_rpl.get(sym, {}).get("raw_close"), 0)
        if entry_px <= 0:
            fwd_5d.append(0); fwd_10d.append(0); fwd_20d.append(0)
            continue

        for horizon, fwd_list in [(5, fwd_5d), (10, fwd_10d), (20, fwd_20d)]:
            fwd_idx = min(idx + horizon, len(price_dates) - 1)
            fwd_rpl = _price_lookup_for_day(prices_df, pdi, price_dates[fwd_idx], price_columns)
            fwd_px = _safe_float(fwd_rpl.get(sym, {}).get("raw_close"), 0)
            ret = (fwd_px / entry_px - 1.0) if fwd_px > 0 and entry_px > 0 else 0.0
            fwd_list.append(round(ret, 6))

    attr_df["fwd_5d"] = fwd_5d
    attr_df["fwd_10d"] = fwd_10d
    attr_df["fwd_20d"] = fwd_20d

    return attr_df


def summarize_attribution(attr_df: pd.DataFrame) -> dict:
    """Summarize order-level attribution."""
    if attr_df.empty:
        return {}

    summary = {}
    for action in ["SKIPPED_BUY", "FORCED_SELL"]:
        sub = attr_df[attr_df["action"] == action]
        if sub.empty:
            continue
        summary[f"{action}_count"] = len(sub)
        for horizon in ["fwd_5d", "fwd_10d", "fwd_20d"]:
            vals = sub[horizon].dropna()
            summary[f"{action}_{horizon}_mean"] = round(float(vals.mean()), 6) if len(vals) > 0 else 0
            summary[f"{action}_{horizon}_pct_positive"] = round(float((vals > 0).mean()), 4) if len(vals) > 0 else 0

    # Top contributors
    total_pnl = 0
    if "SKIPPED_BUY" in attr_df["action"].values:
        skipped = attr_df[attr_df["action"] == "SKIPPED_BUY"]
        # PnL = shares * entry_price * (-fwd_10d) [skipping a buy that went up = opportunity cost, went down = saving]
        for _, row in skipped.iterrows():
            pnl = -row.get("s60_shares", 0) * row.get("entry_price", 0) * row.get("fwd_10d", 0)
            total_pnl += abs(pnl)

    # Concentration check
    if "symbol" in attr_df.columns:
        sym_pnl = attr_df.groupby("symbol").size().sort_values(ascending=False)
        if len(sym_pnl) > 0:
            summary["top_symbol_pct"] = round(float(sym_pnl.iloc[0] / len(attr_df)), 4)

    return summary


# ══════════════════════════════════════════════════════════════════════
# G4: Leave-one-event-out
# ══════════════════════════════════════════════════════════════════════

def run_loo_validation(s60_nav: pd.DataFrame, epc_nav: pd.DataFrame,
                       event_dates: list, recovery_windows: int = 60) -> list:
    """Test each risk event independently."""
    s60_nav = s60_nav.copy()
    epc_nav = epc_nav.copy()
    s60_nav["trade_date"] = s60_nav["trade_date"].astype(str)
    epc_nav["trade_date"] = epc_nav["trade_date"].astype(str)

    results = []
    for i, evt_date in enumerate(event_dates):
        evt_str = str(evt_date)
        # Find event window: from evt_date to evt_date + recovery_windows trading days
        s60_sub = s60_nav[s60_nav["trade_date"] >= evt_str].head(recovery_windows + 120)
        epc_sub = epc_nav[epc_nav["trade_date"] >= evt_str].head(recovery_windows + 120)

        if s60_sub.empty or epc_sub.empty:
            continue

        s60_m = _compute_metrics(s60_sub)
        epc_m = _compute_metrics(epc_sub)

        results.append({
            "event": i + 1,
            "trigger_date": evt_str,
            "window_days": len(s60_sub),
            "s60_return": s60_m["total_return"],
            "epc_return": epc_m["total_return"],
            "s60_maxdd": s60_m["max_drawdown"],
            "epc_maxdd": epc_m["max_drawdown"],
            "s60_calmar": s60_m["calmar"],
            "epc_calmar": epc_m["calmar"],
            "epc_wins": epc_m["calmar"] > s60_m["calmar"],
        })

    return results


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="EPC-1 G2-G4")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("EPC-1 G2-G4: 执行动作消融 + 订单归因 + 留一验证")
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
    out_dir = OUT_ROOT / f"epc1_g2g4_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common_kw = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
    )

    # ══════════════════════════════════════════════════════════
    # G2: Execution action ablation
    # ══════════════════════════════════════════════════════════
    print("\n=== G2: 执行动作消融 ===")

    g2_configs = [
        ("S60", False, True, True, False, "固定60%基线"),
        ("E0", True, True, True, False, "纯比例降仓(SCALE)"),
        ("E1", True, False, False, False, "仅停止新增买入"),
        ("E2", True, True, True, False, "仅卖出降仓(允许新买)"),
        ("E3", True, False, True, False, "停买+卖出(=EPC-1)"),
        ("E4", True, False, True, True, "E3+延迟恢复"),
    ]

    g2_results = {}
    for label, use_ctrl, allow_buys, force_sells, delayed_rec, desc in g2_configs:
        print(f"  {label} ({desc})...", end=" ", flush=True)
        track = (label in ("S60", "E3"))
        r = run_epc1_action_backtest(
            label, use_controller=use_ctrl,
            allow_new_buys_in_risk=allow_buys,
            force_sells_in_risk=force_sells,
            delayed_recovery=delayed_rec,
            track_orders=track,
            **common_kw,
        )
        g2_results[label] = r
        m = r["metrics"]
        print(f"R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

    # ── G2 summary ───────────────────────────────────────────
    s60_m = g2_results["S60"]["metrics"]
    print(f"\n  G2 动作消融总结 (vs S60: R={s60_m['total_return']:.2%} DD={s60_m['max_drawdown']:.2%}):")
    g2_summary = []
    for label, _, _, _, _, desc in g2_configs:
        m = g2_results[label]["metrics"]
        delta_r = m["total_return"] - s60_m["total_return"]
        dd_ratio = abs(m["max_drawdown"]) / max(abs(s60_m["max_drawdown"]), 0.001)
        g2_summary.append({
            "curve": label, "description": desc,
            "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
            "calmar": m["calmar"], "delta_return": round(delta_r, 6),
            "dd_ratio_vs_s60": round(dd_ratio, 4),
        })
        print(f"    {label}: ΔR={delta_r:+.2%} DD/S60={dd_ratio:.2f} Cal={m['calmar']:.2f}")

    pd.DataFrame(g2_summary).to_csv(out_dir / "g2_ablation.csv", index=False)

    # ── Identify dominant action ──────────────────────────────
    best_e = max(
        [(l, g2_results[l]["metrics"]["calmar"]) for l in ["E1", "E2", "E3", "E4"]
         if l in g2_results],
        key=lambda x: x[1],
    )
    print(f"\n  主导动作: {best_e[0]} (Calmar={best_e[1]:.2f})")

    # ══════════════════════════════════════════════════════════
    # G3: Order-level attribution
    # ══════════════════════════════════════════════════════════
    print(f"\n=== G3: 订单级归因 ===")
    s60_orders = g2_results.get("S60", {}).get("orders_df", pd.DataFrame())
    e3_orders = g2_results.get("E3", {}).get("orders_df", pd.DataFrame())

    if not s60_orders.empty and not e3_orders.empty:
        attr_df = build_order_attribution(s60_orders, e3_orders, ps, pdi)
        if not attr_df.empty:
            attr_df.to_csv(out_dir / "g3_order_attribution.csv", index=False)
            attr_summary = summarize_attribution(attr_df)

            print(f"  差异化订单总数: {len(attr_df)}")
            for action in ["SKIPPED_BUY", "FORCED_SELL"]:
                n = attr_summary.get(f"{action}_count", 0)
                if n == 0: continue
                fwd5 = attr_summary.get(f"{action}_fwd_5d_mean", 0)
                fwd10 = attr_summary.get(f"{action}_fwd_10d_mean", 0)
                fwd20 = attr_summary.get(f"{action}_fwd_20d_mean", 0)
                pos5 = attr_summary.get(f"{action}_fwd_5d_pct_positive", 0)
                print(f"    {action}: {n}笔, fwd5d={fwd5:+.2%} fwd10d={fwd10:+.2%} fwd20d={fwd20:+.2%} pos5d={pos5:.0%}")

            top_sym = attr_summary.get("top_symbol_pct", 0)
            print(f"    单一股票最大占比: {top_sym:.0%} {'✅ <40%' if top_sym < 0.40 else '❌ ≥40%'}")

            with open(out_dir / "g3_attribution_summary.json", "w") as f:
                json.dump(attr_summary, f, indent=2)

    # ══════════════════════════════════════════════════════════
    # G4: Leave-one-event-out
    # ══════════════════════════════════════════════════════════
    print(f"\n=== G4: 留一事件验证 ===")
    risk_events = [date(2023, 12, 5), date(2024, 4, 9), date(2024, 11, 18)]
    s60_nav = g2_results["S60"]["nav_df"]
    e3_nav = g2_results["E3"]["nav_df"]

    loo_results = run_loo_validation(s60_nav, e3_nav, risk_events)
    n_wins = sum(1 for r in loo_results if r["epc_wins"])
    print(f"  有效事件: {n_wins}/{len(loo_results)} (需≥2)")

    total_excess = 0
    for r in loo_results:
        excess = r["epc_return"] - r["s60_return"]
        total_excess += abs(excess)
        dd_flag = "✅" if abs(r["epc_maxdd"]) < abs(r["s60_maxdd"]) else "❌"
        print(f"    事件{r['event']} ({r['trigger_date']}): "
              f"EPC R={r['epc_return']:.2%} vs S60 R={r['s60_return']:.2%} "
              f"EPC DD={r['epc_maxdd']:.2%} vs S60 DD={r['s60_maxdd']:.2%} {dd_flag}")

    # Check concentration
    if loo_results:
        for r in loo_results:
            excess = r["epc_return"] - r["s60_return"]
            pct = abs(excess) / max(total_excess, 0.001)
            if pct > 0.60:
                print(f"    ⚠️ 事件{r['event']} 贡献 {pct:.0%} 超额收益 (>60%)")

    pd.DataFrame(loo_results).to_csv(out_dir / "g4_loo_validation.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # Save NAVs & Report
    # ══════════════════════════════════════════════════════════
    for label, r in g2_results.items():
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf.to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)

    # Report
    report = [
        "# EPC-1 G2-G4 验证报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## G2: 执行动作消融",
        "| 曲线 | 动作 | 总收益 | MaxDD | Calmar | ΔR vs S60 |",
        "|------|------|--------|-------|--------|-----------|",
    ]
    for label, _, _, _, _, desc in g2_configs:
        m = g2_results[label]["metrics"]
        dr = m["total_return"] - s60_m["total_return"]
        report.append(f"| {label} | {desc} | {m['total_return']:.2%} | {m['max_drawdown']:.2%} | {m['calmar']:.2f} | {dr:+.2%} |")

    report += [
        f"",
        f"**主导动作: {best_e[0]} (Calmar={best_e[1]:.2f})**",
        "",
        "## G3: 订单级归因",
        f"- 差异化订单: {len(attr_df)} 笔" if not attr_df.empty else "- 无数据",
        "",
        "## G4: 留一事件验证",
        f"- 有效事件: {n_wins}/{len(loo_results)}",
        "",
        "## 评级",
    ]
    if n_wins >= 2 and best_e[0] in ("E1", "E3") and not attr_df.empty:
        report.append("- **EPC-1: RESEARCH_CANDIDATE** — 有初步事件级和订单级证据")
    else:
        report.append("- EPC-1: RESEARCH_ONLY — 证据不足以升级")

    (out_dir / "epc1_g2g4_report.md").write_text("\n".join(report))

    # ── Final ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL")
    print(f"  G2 主导动作: {best_e[0]}")
    print(f"  G3 差异化订单: {len(attr_df) if not attr_df.empty else 0} 笔")
    print(f"  G4 有效事件: {n_wins}/{len(loo_results)}")
    print(f"  报告: {out_dir}/epc1_g2g4_report.md")
    print("\nDone.")


if __name__ == "__main__":
    main()
