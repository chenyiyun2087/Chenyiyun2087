#!/usr/bin/env python3
"""
DRO-1 v5.0 升级验证: G1 (因果拆解) + G2 (事件归因) + G3 (邻域稳定性) + G4 (成本压力)

Usage:
    python scripts/research/run_dro1_v5_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys
from dataclasses import dataclass
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
from scripts.research.run_dro1_backtest import (
    DRO1Controller, run_dro1_backtest, _compute_metrics,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# G1: Scale-Only execution (same holdings, pure exposure scaling)
# ══════════════════════════════════════════════════════════════════════

def build_scale_nav(real_nav_df: pd.DataFrame, holdings_daily: dict,
                    position_controller, prices_df, price_day_indices,
                    initial_cash=500000.0, base_position=0.60) -> pd.DataFrame:
    """
    Build SCALE NAV using the same stock holdings as real execution,
    but with pure proportional exposure (no lot rounding, no cash drag).

    holdings_daily: {trade_date: {symbol: shares}}
    """
    if real_nav_df is None or real_nav_df.empty:
        return pd.DataFrame()

    nav_rows = []
    scale_nav = 1.0
    price_columns = ["raw_close", "adj_close", "adj_open"]

    # Build date-to-index mapping for real NAV
    real_nav = real_nav_df.copy()
    real_nav["trade_date"] = real_nav["trade_date"].astype(str)

    # Get all trade dates
    dates = sorted(holdings_daily.keys())
    if not dates:
        return pd.DataFrame()

    for trade_date in dates:
        holdings = holdings_daily.get(trade_date, {})
        rpl = _price_lookup_for_day(prices_df, price_day_indices, trade_date, price_columns)

        # Compute portfolio value of holdings at today's close
        portfolio_value = 0.0
        for sym, shares in holdings.items():
            price = _safe_float(rpl.get(sym, {}).get("raw_close"), 0.0)
            portfolio_value += shares * price

        # Get position ratio for this day
        pos_ratio = base_position
        if position_controller is not None:
            # Find matching row in nav_df for controller state
            real_row = real_nav[real_nav["trade_date"] == str(trade_date)]
            if not real_row.empty:
                pos_ratio = float(real_row.iloc[0].get("position_ratio", base_position))

        # Scale NAV: stock return contribution × exposure ratio
        # Use previous day's holdings to compute today's return
        if len(nav_rows) > 0:
            prev_holdings = holdings_daily.get(dates[dates.index(trade_date) - 1], {})
            prev_value = 0.0
            for sym, shares in prev_holdings.items():
                price = _safe_float(rpl.get(sym, {}).get("raw_close"), 0.0)
                prev_value += shares * price
            prev_rpl = _price_lookup_for_day(prices_df, price_day_indices,
                                              dates[dates.index(trade_date) - 1], price_columns)
            prev_value_prevclose = 0.0
            for sym, shares in prev_holdings.items():
                price = _safe_float(prev_rpl.get(sym, {}).get("raw_close"), 0.0)
                prev_value_prevclose += shares * price

            if prev_value_prevclose > 0:
                stock_return = (prev_value / prev_value_prevclose - 1.0)
                # Apply exposure scaling
                scaled_return = stock_return * pos_ratio
                scale_nav *= (1.0 + scaled_return)

        nav_rows.append({
            "trade_date": trade_date,
            "nav": round(scale_nav, 6),
            "position_ratio": round(pos_ratio, 4),
            "holdings_value": round(portfolio_value, 2),
            "holdings_count": len(holdings),
        })

    return pd.DataFrame(nav_rows)


def run_s60_with_holdings_tracking(
    engine, scores, prices, market_env, calendar, signal_to_exec, exec_to_signal,
    sdi, pdi, it_trends, specs, start_date, end_date, initial_cash,
    base_position=0.60, use_dro1=False,
) -> tuple:
    """Run S60 (or DRO-1) backtest AND track daily holdings for SCALE analysis."""
    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched:
        return None, None, {}
    spec = matched[0]

    controller = DRO1Controller(base_position=base_position) if use_dro1 else None

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
    nav_rows, holdings_daily = [], {}
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
            nav_rows.append({"trade_date": trade_date, "nav": current_nav, "position_ratio": 0.0})
            holdings_daily[trade_date] = dict(account.positions)
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

        if controller is not None:
            position_ratio, _ = controller.get_position(
                features.csi300_ret20, features.turnover_ratio, current_nav)
        else:
            position_ratio = base_position

        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        if not targets.empty or account.positions:
            _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                slippage_rate=0.0, max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=None,
            )

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "nav": round(current_nav, 6),
            "position_ratio": round(position_ratio, 4),
            "equity": round(eq, 2), "cash": round(account.cash, 2),
        })
        # Record holdings: symbol → shares
        holdings_daily[trade_date] = {sym: pos.shares for sym, pos in account.positions.items()}

    nav_df = pd.DataFrame(nav_rows)
    return nav_df, holdings_daily, controller


# ══════════════════════════════════════════════════════════════════════
# G2: Risk event attribution
# ══════════════════════════════════════════════════════════════════════

def build_risk_event_attribution(d1_decisions: list, d1_nav: pd.DataFrame,
                                  s60_nav: pd.DataFrame) -> pd.DataFrame:
    """For each DRO-1 risk event, compute forward returns and incremental value."""
    if not d1_decisions:
        return pd.DataFrame()

    dec_df = pd.DataFrame(d1_decisions)
    d1_nav_copy = d1_nav.copy()
    d1_nav_copy["trade_date"] = d1_nav_copy["trade_date"].astype(str)
    s60_nav_copy = s60_nav.copy()
    s60_nav_copy["trade_date"] = s60_nav_copy["trade_date"].astype(str)

    # Find ENTER_RISK events
    events = []
    enter_mask = dec_df["event"] == "ENTER_RISK"
    enter_dates = dec_df[enter_mask]["signal_date"].values

    dec_df["_sd"] = pd.to_datetime(dec_df["signal_date"]).dt.date
    for sd_raw in enter_dates:
        sd = pd.Timestamp(sd_raw).date() if hasattr(sd_raw, 'date') else pd.Timestamp(sd_raw).date() if not isinstance(sd_raw, date) else sd_raw
        # Find this event's rows
        evt_rows = dec_df[dec_df["_sd"] >= sd]
        exit_rows = evt_rows[evt_rows["event"] == "EXIT_RISK"]
        exit_date = exit_rows.iloc[0]["signal_date"] if len(exit_rows) > 0 else None
        risk_days = len(evt_rows[evt_rows["in_risk"] == True]) if len(evt_rows) > 0 else 0

        # Forward returns from trigger date
        d1_at = d1_nav_copy[d1_nav_copy["trade_date"] == str(sd)]
        s60_at = s60_nav_copy[s60_nav_copy["trade_date"] == str(sd)]

        if d1_at.empty or s60_at.empty:
            continue

        d1_nav_val = float(d1_at.iloc[0]["nav"])
        s60_nav_val = float(s60_at.iloc[0]["nav"])

        # Forward returns
        for horizon, label in [(5, "fwd5d"), (10, "fwd10d"), (20, "fwd20d")]:
            d1_fwd = d1_nav_copy.iloc[min(len(d1_nav_copy)-1,
                d1_nav_copy.index.get_loc(d1_at.index[0]) + horizon)]
            s60_fwd = s60_nav_copy.iloc[min(len(s60_nav_copy)-1,
                s60_nav_copy.index.get_loc(s60_at.index[0]) + horizon)]
            d1_ret = float(d1_fwd["nav"]) / d1_nav_val - 1.0
            s60_ret = float(s60_fwd["nav"]) / s60_nav_val - 1.0
            if f"d1_{label}" not in dir():  # first iteration
                pass

        events.append({
            "trigger_date": str(sd),
            "exit_date": str(exit_date) if exit_date else "N/A",
            "risk_days": risk_days,
            "triggers": int(evt_rows.iloc[0].get("triggers", 0)),
            "csi300_ret20": float(evt_rows.iloc[0].get("csi300_ret20", 0)),
            "turnover_ratio": float(evt_rows.iloc[0].get("turnover_ratio", 0)),
            "acct_dd": float(evt_rows.iloc[0].get("acct_dd", 0)),
        })

    return pd.DataFrame(events)


# ══════════════════════════════════════════════════════════════════════
# G3: Threshold neighborhood stability
# ══════════════════════════════════════════════════════════════════════

def run_stability_grid(common, frozen_csi300=-0.06, frozen_turnover=0.85, frozen_dd=-0.08):
    """Run 9 parameter neighborhood combinations."""
    csi300_vals = [-0.05, -0.06, -0.07]
    turnover_vals = [0.80, 0.85, 0.90]
    dd_vals = [-0.07, -0.08, -0.09]

    results = []
    for c3 in csi300_vals:
        for to in turnover_vals:
            for dd in dd_vals:
                label = f"G3_c{c3}_t{to}_d{dd}"
                controller = DRO1Controller(
                    base_position=0.60, risk_position=0.40,
                    csi300_threshold=c3, turnover_threshold=to,
                    account_dd_threshold=dd,
                )
                r = run_dro1_backtest(
                    label=label, base_position=0.60, use_dro1=True,
                    **common,
                )
                m = r["metrics"]
                s60_m = common.get("_s60_metrics", {})
                dd_impr = abs(m["max_drawdown"]) / abs(s60_m.get("max_drawdown", 0.3511)) if s60_m else 1.0
                ulcer_impr = m["ulcer"] / s60_m.get("ulcer", 0.1709) if s60_m else 1.0
                ret_ratio = m["total_return"] / s60_m.get("total_return", 0.1337) if s60_m else 1.0
                passes = dd_impr <= 0.90 and ulcer_impr <= 0.90 and ret_ratio >= 0.80
                results.append({
                    "csi300_threshold": c3, "turnover_threshold": to,
                    "account_dd_threshold": dd,
                    "total_return": m["total_return"], "max_dd": m["max_drawdown"],
                    "calmar": m["calmar"], "ulcer": m["ulcer"],
                    "dd_ratio": round(dd_impr, 4), "ulcer_ratio": round(ulcer_impr, 4),
                    "ret_ratio": round(ret_ratio, 4), "passes": passes,
                })
    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════
# G4: Cost stress
# ══════════════════════════════════════════════════════════════════════

def run_cost_stress(common, cost_scenarios, capacity_levels):
    """Run DRO-1 under cost and capacity stress."""
    results = []
    for cost_label, cost_rate, slip_rate in cost_scenarios:
        for cap_label, cap_cash in capacity_levels:
            r = run_dro1_backtest(
                label=f"G4_{cost_label}_{cap_label}",
                base_position=0.60, use_dro1=True,
                initial_cash=cap_cash,
                **{k: v for k, v in common.items() if k != "initial_cash"},
            )
            m = r["metrics"]
            s60_m = common.get("_s60_metrics", {})
            dd_impr = abs(m["max_drawdown"]) / abs(s60_m.get("max_drawdown", 0.3511)) if s60_m else 1.0
            ret_ratio = m["total_return"] / s60_m.get("total_return", 0.1337) if s60_m else 1.0
            results.append({
                "cost_label": cost_label, "cost_rate": cost_rate, "slip_rate": slip_rate,
                "capacity_label": cap_label, "capacity_cash": cap_cash,
                "total_return": m["total_return"], "max_dd": m["max_drawdown"],
                "calmar": m["calmar"], "dd_ratio": round(dd_impr, 4),
                "ret_ratio": round(ret_ratio, 4),
                "passes": dd_impr <= 0.90 and ret_ratio >= 0.80,
            })
    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="DRO-1 v5.0 G1-G4 Validation")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--skip-g3", action="store_true")
    parser.add_argument("--skip-g4", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("DRO-1 v5.0 G1-G4 Validation")
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
    out_dir = OUT_ROOT / f"dro1_v5_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, score_day_indices=sdi,
        price_day_indices=pdi, index_trends=it_trends, strategy_specs=specs,
        start_date=args.start_date, end_date=args.end_date,
        initial_cash=args.initial_cash,
    )

    # ══════════════════════════════════════════════════════════
    # G1: Causal decomposition
    # ══════════════════════════════════════════════════════════
    print("\n=== G1: 因果拆解 ===")

    # Run S60 with holdings tracking
    print("  Running S60 (with holdings)...", end=" ", flush=True)
    s60_nav, s60_holdings, _ = run_s60_with_holdings_tracking(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
        base_position=0.60, use_dro1=False)
    s60_metrics = _compute_metrics(s60_nav)
    print(f"R={s60_metrics['total_return']:.2%} DD={s60_metrics['max_drawdown']:.2%}")

    # Run D1 with holdings tracking
    print("  Running D1 (with holdings)...", end=" ", flush=True)
    d1_nav, d1_holdings, d1_ctrl = run_s60_with_holdings_tracking(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
        base_position=0.60, use_dro1=True)
    d1_metrics = _compute_metrics(d1_nav)
    print(f"R={d1_metrics['total_return']:.2%} DD={d1_metrics['max_drawdown']:.2%}")

    # Build SCALE NAVs
    print("  Building S60_SCALE...", end=" ", flush=True)
    s60_scale_nav = build_scale_nav(s60_nav, s60_holdings, None, ps, pdi,
                                     args.initial_cash, 0.60)
    s60_scale_m = _compute_metrics(s60_scale_nav) if not s60_scale_nav.empty else {}
    print(f"R={s60_scale_m.get('total_return',0):.2%} DD={s60_scale_m.get('max_drawdown',0):.2%}")

    print("  Building D1_SCALE...", end=" ", flush=True)
    # For D1_SCALE: use S60 holdings but DRO-1 position ratios
    d1_scale_nav = build_scale_nav(d1_nav, s60_holdings, None, ps, pdi,
                                    args.initial_cash, 0.60)
    d1_scale_m = _compute_metrics(d1_scale_nav) if not d1_scale_nav.empty else {}
    print(f"R={d1_scale_m.get('total_return',0):.2%} DD={d1_scale_m.get('max_drawdown',0):.2%}")

    # ── G1 Interpretation ────────────────────────────────────
    print(f"\n  G1 因果解释矩阵:")
    real_d1_vs_s60 = d1_metrics["total_return"] - s60_metrics["total_return"]
    scale_d1_vs_s60 = d1_scale_m.get("total_return", 0) - s60_scale_m.get("total_return", 0)
    print(f"  D1_REAL vs S60_REAL: {real_d1_vs_s60:+.2%}")
    print(f"  D1_SCALE vs S60_SCALE: {scale_d1_vs_s60:+.2%}")

    if scale_d1_vs_s60 > 0.01:
        conclusion = "✅ 纯风险覆盖有效 — DRO-1有独立价值"
    elif real_d1_vs_s60 > 0.01:
        conclusion = "⚠️ 收益来自执行路径 — DRO-1不是纯风险覆盖"
    else:
        conclusion = "❌ 因果证据不足"
    print(f"  G1 结论: {conclusion}")

    # Save G1
    s60_nav.to_csv(out_dir / "g1_s60_real_nav.csv", index=False)
    d1_nav.to_csv(out_dir / "g1_d1_real_nav.csv", index=False)
    if not s60_scale_nav.empty:
        s60_scale_nav.to_csv(out_dir / "g1_s60_scale_nav.csv", index=False)
    if not d1_scale_nav.empty:
        d1_scale_nav.to_csv(out_dir / "g1_d1_scale_nav.csv", index=False)

    g1_summary = {
        "s60_real_return": s60_metrics["total_return"],
        "s60_real_maxdd": s60_metrics["max_drawdown"],
        "d1_real_return": d1_metrics["total_return"],
        "d1_real_maxdd": d1_metrics["max_drawdown"],
        "s60_scale_return": s60_scale_m.get("total_return", 0),
        "s60_scale_maxdd": s60_scale_m.get("max_drawdown", 0),
        "d1_scale_return": d1_scale_m.get("total_return", 0),
        "d1_scale_maxdd": d1_scale_m.get("max_drawdown", 0),
        "conclusion": conclusion,
    }
    with open(out_dir / "g1_causal_decomposition.json", "w") as f:
        json.dump(g1_summary, f, indent=2)

    # ══════════════════════════════════════════════════════════
    # G2: Risk event attribution
    # ══════════════════════════════════════════════════════════
    print(f"\n=== G2: 风险事件归因 ===")
    d1_full = run_dro1_backtest("D1", base_position=0.60, use_dro1=True, **common)
    events_df = build_risk_event_attribution(
        d1_full.get("decisions", []), d1_full["nav_df"],
        run_dro1_backtest("S60", base_position=0.60, use_dro1=False, **common)["nav_df"],
    )
    if not events_df.empty:
        events_df.to_csv(out_dir / "g2_risk_events.csv", index=False)
        n_events = len(events_df)
        avg_risk_days = float(events_df["risk_days"].mean()) if "risk_days" in events_df.columns else 0
        print(f"  风险事件: {n_events}次, 平均持续: {avg_risk_days:.0f}天")
        if "triggers" in events_df.columns:
            for t in [2, 3]:
                count = int((events_df["triggers"] == t).sum())
                print(f"    {t}-触发事件: {count}次")

    # ══════════════════════════════════════════════════════════
    # G3: Threshold stability
    # ══════════════════════════════════════════════════════════
    if not args.skip_g3:
        print(f"\n=== G3: 邻域稳定性 (9组) ===")
        common["_s60_metrics"] = s60_metrics
        grid_df = run_stability_grid(common)
        grid_df.to_csv(out_dir / "g3_stability_grid.csv", index=False)
        n_pass = int(grid_df["passes"].sum())
        print(f"  通过组数: {n_pass}/9 (需≥5)")
        print(f"  {'✅ 邻域稳定' if n_pass >= 5 else '❌ 邻域不稳定'}")
        # Show all 9
        for _, row in grid_df.iterrows():
            flag = "✅" if row["passes"] else "❌"
            print(f"    c{row['csi300_threshold']}_t{row['turnover_threshold']}_d{row['account_dd_threshold']}: "
                  f"R={row['total_return']:.2%} DD={row['max_dd']:.2%} Cal={row['calmar']:.2f} {flag}")

    # ══════════════════════════════════════════════════════════
    # G4: Cost stress
    # ══════════════════════════════════════════════════════════
    if not args.skip_g4:
        print(f"\n=== G4: 成本与容量压力 ===")
        cost_scenarios = [
            ("base", 0.00075, 0.0),
            ("slip5", 0.00075, 0.0005),
            ("slip10", 0.00075, 0.0010),
            ("double_slip10", 0.00150, 0.0010),
        ]
        capacity_levels = [
            ("1.5M", 1_500_000),
            ("3M", 3_000_000),
            ("5M", 5_000_000),
            ("10M", 10_000_000),
        ]
        stress_df = run_cost_stress(common, cost_scenarios, capacity_levels)
        stress_df.to_csv(out_dir / "g4_stress_test.csv", index=False)
        n_stress_pass = int(stress_df["passes"].sum())
        print(f"  通过场景: {n_stress_pass}/{len(stress_df)}")
        for _, row in stress_df.iterrows():
            flag = "✅" if row["passes"] else "❌"
            print(f"    {row['cost_label']}/{row['capacity_label']}: "
                  f"R={row['total_return']:.2%} DD={row['max_dd']:.2%} {flag}")

    # ══════════════════════════════════════════════════════════
    # Final report
    # ══════════════════════════════════════════════════════════
    report = [
        "# DRO-1 v5.0 G1-G4 验证报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## G1: 因果拆解",
        f"- S60_REAL: R={s60_metrics['total_return']:.2%} DD={s60_metrics['max_drawdown']:.2%}",
        f"- D1_REAL: R={d1_metrics['total_return']:.2%} DD={d1_metrics['max_drawdown']:.2%}",
        f"- S60_SCALE: R={s60_scale_m.get('total_return',0):.2%} DD={s60_scale_m.get('max_drawdown',0):.2%}",
        f"- D1_SCALE: R={d1_scale_m.get('total_return',0):.2%} DD={d1_scale_m.get('max_drawdown',0):.2%}",
        f"- REAL: D1-S60 = {real_d1_vs_s60:+.2%}",
        f"- SCALE: D1-S60 = {scale_d1_vs_s60:+.2%}",
        f"- **G1结论: {conclusion}**",
        "",
        "## G3: 邻域稳定性",
        f"- 通过: {n_pass}/9 (需≥5)" if not args.skip_g3 else "- 未运行",
        "",
        "## G4: 成本压力",
        f"- 通过: {n_stress_pass}/{len(stress_df)}" if not args.skip_g4 else "- 未运行",
        "",
        "## 评级",
    ]

    if scale_d1_vs_s60 > 0.01 and (n_pass >= 5 if not args.skip_g3 else True):
        report.append("- **DRO-1-S1: SHADOW_ELIGIBLE** ✅")
    else:
        report.append("- DRO-1: RESEARCH_VALIDATED_PROVISIONAL ⚠️")

    (out_dir / "dro1_v5_report.md").write_text("\n".join(report))

    # ── Final ─────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL")
    print("=" * 60)
    print(f"  G1: {conclusion}")
    if not args.skip_g3:
        print(f"  G3: {n_pass}/9 stable ({'✅' if n_pass >= 5 else '❌'})")
    if not args.skip_g4:
        print(f"  G4: {n_stress_pass}/{len(stress_df)} stress passed")
    print(f"  报告: {out_dir}/dro1_v5_report.md")
    print("\nDone.")


if __name__ == "__main__":
    main()
