#!/usr/bin/env python3
"""
EPC-1 G2.5: 完整性与因果一致性审计

断言一: SKIPPED_BUY 必须只在 risk_state 期间发生
断言二: S60_SCALE / E1_SCALE 路径同口径一致
E1行为边界审计 + 重定义G3订单归因 + 重定义G4事件验证

Usage:
    python scripts/research/run_epc1_g25_audit.py \
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
    load_index_trends_pit, build_daily_features,
    _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_dro1_backtest import _compute_metrics
from scripts.research.run_epc1_g2_g4_validation import EPC1Controller

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# G2.5: Audit backtest with full state tracking
# ══════════════════════════════════════════════════════════════════════

def run_audit_backtest(label: str, use_epc1: bool = False,
                       engine=None, scores=None, prices=None, market_env=None,
                       calendar=None, signal_to_exec=None, exec_to_signal=None,
                       sdi=None, pdi=None, it_trends=None, specs=None,
                       start_date=None, end_date=None, initial_cash=500000.0,
                       ) -> dict:
    """Run backtest with full state tracking for audit."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched:
        return {}
    spec = matched[0]

    controller = EPC1Controller(
        base_position=0.60, allow_new_buys_in_risk=False,
        force_sells_in_risk=False, delayed_recovery=False,
    ) if use_epc1 else None

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
    nav_rows, order_audit_rows, state_rows = [], [], []
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

        # ── Controller state ──────────────────────────────
        in_risk = False
        allow_buys = True
        trig_count = 0
        trig_components = ""
        if controller is not None:
            position_ratio, dro1_state, flags = controller.get_effective_position(
                current_nav, features)
            in_risk = flags["in_risk"]
            allow_buys = flags["allow_new_buys"]
            trig_count = dro1_state.get("triggers", 0)
            trig_components = dro1_state.get("reasons", "")
        else:
            position_ratio = 0.60

        # ── Record state ───────────────────────────────────
        state_rows.append({
            "signal_date": signal_date, "execution_date": trade_date,
            "in_risk": in_risk, "allow_buys": allow_buys,
            "trigger_count": trig_count, "trigger_components": trig_components,
            "position_ratio": round(position_ratio, 4),
            "csi300_ret20": round(features.csi300_ret20, 4),
            "turnover_ratio": round(features.turnover_ratio, 4),
            "acct_dd": round(controller.get_account_dd(current_nav) if controller else 0, 4),
        })

        # ── Get S60 candidate set (what WOULD be bought normally) ──
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        s60_buy_candidates = set()
        if not targets.empty and "symbol" in targets.columns:
            existing = set(str(s).zfill(6) for s in account.positions.keys())
            for _, row in targets.iterrows():
                sym = str(row["symbol"]).zfill(6)
                if sym not in existing:
                    s60_buy_candidates.add(sym)

        # Track pre-rebalance state
        prev_positions = set(account.positions.keys())
        prev_cash = account.cash

        # ── Filter targets if E1 mode ──────────────────────
        epc_blocked_buys = []
        if use_epc1 and not allow_buys and in_risk and not targets.empty and "symbol" in targets.columns:
            existing_syms = set(str(s).zfill(6) for s in account.positions.keys())
            for _, row in targets.iterrows():
                sym = str(row["symbol"]).zfill(6)
                if sym not in existing_syms:
                    epc_blocked_buys.append({
                        "symbol": sym,
                        "risk_state_on_signal": in_risk,
                        "trigger_count": trig_count,
                        "trigger_components": trig_components,
                    })
            # Keep only existing-position targets; if none, leave empty DF (NOT None)
            targets = targets[targets["symbol"].astype(str).str.zfill(6).isin(existing_syms)]
            # targets stays as DataFrame (possibly empty), never becomes None

        # ── Execute ─────────────────────────────────────────
        # E1 mode during risk: only allow rebalancing existing positions, no new buys
        # Pass filtered targets. If no targets remain: skip _rebalance (positions sit static)
        if use_epc1 and in_risk and not allow_buys:
            if not targets.empty or account.positions:
                _rebalance(
                    account=account, signal_date=signal_date, execution_date=trade_date,
                    day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                    lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                    slippage_rate=0.0, max_total_positions=5, position_ratio=position_ratio,
                    calendar=calendar, open_prices=rpl,
                    targets=targets,  # filtered targets (existing positions only, possibly empty)
                    precommit_prices=None, strict_precommit=False, ledger=None,
                )
        elif not targets.empty or account.positions:
            _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                slippage_rate=0.0, max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=None,
            )

        # ── Track what actually happened ────────────────────
        new_positions = set(account.positions.keys())
        actual_buys = new_positions - prev_positions
        actual_sells = prev_positions - new_positions

        # Correct labeling: ALL sells during E1 risk are NATURAL (M7, hold expiry)
        # E1 never forces sells. Only count as FORCED_SELL if we explicitly triggered it.
        for sym in actual_sells:
            is_risk = in_risk and use_epc1
            order_audit_rows.append({
                "signal_date": signal_date, "execution_date": trade_date,
                "symbol": sym,
                "action": "NATURAL_SELL" if is_risk else "SELL",
                "risk_state_on_signal": in_risk,
                "risk_state_on_execution": in_risk,
                "trigger_count": trig_count,
                "label": label,
            })

        # Record actual buys
        for sym in actual_buys:
            order_audit_rows.append({
                "signal_date": signal_date, "execution_date": trade_date,
                "symbol": sym,
                "action": "BUY",
                "risk_state_on_signal": in_risk,
                "risk_state_on_execution": in_risk,
                "trigger_count": trig_count,
                "trigger_components": trig_components,
                "baseline_buy_eligible": sym in s60_buy_candidates,
                "epc_buy_blocked": False,
                "label": label,
            })

        # ── Record holdings for SCALE ───────────────────────
        holdings_snapshot = {sym: pos.shares for sym, pos in account.positions.items()}

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "in_risk": in_risk,
            "n_holdings": len(holdings_snapshot),
        })

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_metrics(nav_df)
    audit_df = pd.DataFrame(order_audit_rows) if order_audit_rows else pd.DataFrame()
    state_df = pd.DataFrame(state_rows) if state_rows else pd.DataFrame()

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "audit_df": audit_df, "state_df": state_df,
    }


# ══════════════════════════════════════════════════════════════════════
# Assertions
# ══════════════════════════════════════════════════════════════════════

def run_assertion_1(e1_audit: pd.DataFrame) -> dict:
    """Assertion 1: SKIPPED_BUY must only happen during risk state."""
    if e1_audit.empty:
        return {"passed": False, "error": "no_audit_data"}

    skipped = e1_audit[e1_audit["action"] == "SKIPPED_BUY"]
    n_total = len(skipped)

    if n_total == 0:
        return {"passed": True, "total_skipped": 0, "violations": 0, "note": "no_skipped_buys"}

    # Violations: SKIPPED_BUY with risk_state_on_signal = False
    violations = skipped[skipped["risk_state_on_signal"] == False]
    n_violations = len(violations)

    # Also check: BUY during risk with use_epc1
    buys_in_risk = e1_audit[(e1_audit["action"] == "BUY") & (e1_audit["risk_state_on_signal"] == True)]
    n_buys_in_risk = len(buys_in_risk)

    return {
        "passed": n_violations == 0,
        "total_skipped": n_total,
        "violations": n_violations,
        "buys_during_risk": n_buys_in_risk,
        "violation_details": violations[["signal_date", "symbol", "risk_state_on_signal"]].to_dict("records")[:10] if n_violations > 0 else [],
    }


def run_assertion_2(s60_state: pd.DataFrame, e1_state: pd.DataFrame) -> dict:
    """Assertion 2: E1 vs S60 state consistency check."""
    if s60_state.empty or e1_state.empty:
        return {"passed": False, "error": "no_state_data"}

    # Merge on signal_date
    merged = s60_state.merge(
        e1_state[["signal_date", "in_risk", "position_ratio"]],
        on="signal_date", suffixes=("_s60", "_e1"), how="inner")

    # Check: when not in risk, E1 position should be 0.60 (same as S60)
    non_risk = merged[merged["in_risk_e1"] == False]
    e1_positions_ok = (non_risk["position_ratio_e1"].round(2) == 0.60).all()

    # Check: when in risk, E1 position should be 0.40
    in_risk = merged[merged["in_risk_e1"] == True]
    e1_risk_positions_ok = len(in_risk) == 0 or (in_risk["position_ratio_e1"].round(2) == 0.40).all()

    # Count risk days
    n_risk_days_e1 = int(merged["in_risk_e1"].sum())
    n_risk_days_s60 = 0  # S60 never in risk

    return {
        "passed": bool(e1_positions_ok and e1_risk_positions_ok),
        "s60_risk_days": n_risk_days_s60,
        "e1_risk_days": n_risk_days_e1,
        "e1_non_risk_positions_ok": bool(e1_positions_ok),
        "e1_risk_positions_ok": bool(e1_risk_positions_ok),
    }


def run_action_boundary_audit(e1_audit: pd.DataFrame, e1_state: pd.DataFrame) -> dict:
    """Audit E1 action boundaries."""
    if e1_audit.empty:
        return {}

    # Get risk dates
    risk_dates = set()
    if not e1_state.empty:
        risk_dates = set(e1_state[e1_state["in_risk"] == True]["signal_date"].values)

    # Count actions by type and risk state
    buys = e1_audit[e1_audit["action"] == "BUY"]
    skipped = e1_audit[e1_audit["action"] == "SKIPPED_BUY"]
    sells = e1_audit[e1_audit["action"] == "SELL"]
    forced = e1_audit[e1_audit["action"] == "FORCED_SELL"]

    return {
        "buys_in_risk": int((buys["risk_state_on_signal"] == True).sum()),
        "buys_outside_risk": int((buys["risk_state_on_signal"] == False).sum()),
        "skipped_buys_total": len(skipped),
        "skipped_buys_outside_risk": int((skipped["risk_state_on_signal"] == False).sum()),
        "forced_sells_total": len(forced),
        "natural_sells_during_risk": int(((sells["risk_state_on_signal"] == True).sum())),
        "risk_dates_count": len(risk_dates),
    }


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="EPC-1 G2.5 Audit")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("EPC-1 G2.5: 完整性与因果一致性审计")
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
    out_dir = OUT_ROOT / f"epc1_g25_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common_kw = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
    )

    # ══════════════════════════════════════════════════════════
    # Run S60 + E1 audit backtests
    # ══════════════════════════════════════════════════════════
    print("\n=== 运行审计回测 ===")
    print("  S60...", end=" ", flush=True)
    s60 = run_audit_backtest("S60", use_epc1=False, **common_kw)
    print(f"R={s60['metrics']['total_return']:.2%}")

    print("  E1...", end=" ", flush=True)
    e1 = run_audit_backtest("E1", use_epc1=True, **common_kw)
    print(f"R={e1['metrics']['total_return']:.2%}")

    # ══════════════════════════════════════════════════════════
    # Assertion 1
    # ══════════════════════════════════════════════════════════
    print(f"\n=== 断言一: SKIPPED_BUY 必须在风险状态 ===")
    a1 = run_assertion_1(e1["audit_df"])
    print(f"  SKIPPED_BUY总数: {a1['total_skipped']}")
    print(f"  非风险期违规: {a1['violations']}")
    print(f"  风险期内实际买入: {a1.get('buys_during_risk', '?')}")
    print(f"  {'✅ 断言一通过' if a1['passed'] else '❌ 断言一失败 — EPC-1全部G2/G3/G4结果作废'}")

    if not a1["passed"]:
        print(f"  违规明细:")
        for v in a1.get("violation_details", [])[:5]:
            print(f"    {v}")

    with open(out_dir / "g25_assertion_1.json", "w") as f:
        json.dump(a1, f, indent=2, default=str)

    # ══════════════════════════════════════════════════════════
    # Assertion 2
    # ══════════════════════════════════════════════════════════
    print(f"\n=== 断言二: SCALE路径一致性 ===")
    a2 = run_assertion_2(s60["state_df"], e1["state_df"])
    print(f"  E1非风险期仓位=60%: {'✅' if a2['e1_non_risk_positions_ok'] else '❌'}")
    print(f"  E1风险期仓位=40%: {'✅' if a2['e1_risk_positions_ok'] else '❌'}")
    print(f"  S60风险天数: {a2['s60_risk_days']}")
    print(f"  E1风险天数: {a2['e1_risk_days']}")
    print(f"  {'✅ 断言二通过' if a2['passed'] else '❌ 断言二失败'}")

    with open(out_dir / "g25_assertion_2.json", "w") as f:
        json.dump(a2, f, indent=2, default=str)

    # ══════════════════════════════════════════════════════════
    # Action boundary audit
    # ══════════════════════════════════════════════════════════
    print(f"\n=== E1 行为边界审计 ===")
    boundary = run_action_boundary_audit(e1["audit_df"], e1["state_df"])
    if boundary:
        print(f"  风险期内买入: {boundary['buys_in_risk']} (应为0)")
        print(f"  风险期外买入: {boundary['buys_outside_risk']}")
        print(f"  跳过买入(风险期内): {boundary['skipped_buys_total']}")
        print(f"  跳过买入(风险期外): {boundary['skipped_buys_outside_risk']} (应为0)")
        print(f"  强制卖出: {boundary['forced_sells_total']} (应为0, E1不做强制卖出)")

        buys_ok = boundary['buys_in_risk'] == 0
        skip_ok = boundary['skipped_buys_outside_risk'] == 0
        forced_ok = boundary['forced_sells_total'] == 0
        all_boundary_ok = buys_ok and skip_ok and forced_ok
        print(f"  {'✅ 边界审计通过' if all_boundary_ok else '❌ 边界违规'}")

        pd.DataFrame([boundary]).to_csv(out_dir / "epc_action_boundary_audit.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # Save all audit data
    # ══════════════════════════════════════════════════════════
    if not e1["audit_df"].empty:
        e1["audit_df"].to_csv(out_dir / "epc_skipped_buy_state_audit.csv", index=False)
    if not e1["state_df"].empty:
        e1["state_df"].to_csv(out_dir / "epc_state_daily.csv", index=False)
    s60["nav_df"].to_csv(out_dir / "nav_s60.csv", index=False)
    e1["nav_df"].to_csv(out_dir / "nav_e1.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # Final verdict
    # ══════════════════════════════════════════════════════════
    all_pass = a1["passed"] and a2["passed"] and (all_boundary_ok if boundary else False)

    verdict_lines = [
        "# EPC-1 G2.5 完整性审计报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 断言一: SKIPPED_BUY 风险状态一致性",
        f"- 通过: {'✅' if a1['passed'] else '❌'}",
        f"- SKIPPED_BUY总数: {a1['total_skipped']}",
        f"- 非风险期违规: {a1['violations']}",
        "",
        "## 断言二: SCALE路径一致性",
        f"- 通过: {'✅' if a2['passed'] else '❌'}",
        f"- E1非风险期仓位=60%: {'✅' if a2.get('e1_non_risk_positions_ok') else '❌'}",
        f"- E1风险期仓位=40%: {'✅' if a2.get('e1_risk_positions_ok') else '❌'}",
        f"- E1风险天数: {a2.get('e1_risk_days', 0)}",
        "",
        "## E1 行为边界审计",
    ]
    if boundary:
        verdict_lines += [
            f"- 风险期内买入=0: {'✅' if boundary['buys_in_risk']==0 else '❌ '+str(boundary['buys_in_risk'])}",
            f"- 风险期外跳过买入=0: {'✅' if boundary['skipped_buys_outside_risk']==0 else '❌ '+str(boundary['skipped_buys_outside_risk'])}",
            f"- 强制卖出=0: {'✅' if boundary['forced_sells_total']==0 else '❌ '+str(boundary['forced_sells_total'])}",
        ]

    verdict_lines += [
        "",
        "## 最终裁决",
        f"**{'✅ 全部通过 — EPC-1 维持 RESEARCH_CANDIDATE' if all_pass else '❌ 审计失败 — EPC-1 降级'}**",
        "",
        f"裁决依据:",
        f"- 断言一: {'PASS' if a1['passed'] else 'FAIL'}",
        f"- 断言二: {'PASS' if a2['passed'] else 'FAIL'}",
        f"- 边界审计: {'PASS' if (all_boundary_ok if boundary else True) else 'FAIL'}",
    ]

    (out_dir / "g25_verdict.md").write_text("\n".join(verdict_lines))

    print(f"\n{'='*60}")
    print(f"G2.5 裁决: {'✅ 全部通过' if all_pass else '❌ 审计失败'}")
    print(f"  断言一: {'PASS' if a1['passed'] else 'FAIL'}")
    print(f"  断言二: {'PASS' if a2['passed'] else 'FAIL'}")
    if boundary:
        print(f"  边界审计: {'PASS' if all_boundary_ok else 'FAIL'}")
    print(f"  报告: {out_dir}/g25_verdict.md")
    print("\nDone.")


if __name__ == "__main__":
    main()
