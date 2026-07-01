#!/usr/bin/env python3
"""
EPC-1 CR9-CR12: 动作分离与真实账户验证

CR9-A: 冻结S60风险状态序列
CR9-B: B1(freeze60) B2(scale40) B3(freeze40) B4(forcesell40) — 统一风险序列
CR10: 逐股E0-Scale重放
CR12: 完整账户安慰剂

Usage:
    python scripts/research/run_epc1_cr9_validation.py \
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
from scripts.research.run_dro1_backtest import _compute_metrics, DRO1Controller

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _compute_full_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd_series = (nav - peak) / peak
    max_dd = float(np.min(dd_series))
    n = len(nav)
    ann_ret = float((1 + total_return) ** (252 / n) - 1) if n > 0 and nav[0] > 0 else 0.0
    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 else 0.0
    calmar = float(ann_ret / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    cvar95 = float(-np.mean(np.sort(daily_rets)[:max(1, int(n * 0.05))])) if n > 20 else 0.0
    ulcer = float(np.sqrt(np.mean(dd_series ** 2))) if n > 0 else 0.0
    return {"total_return": round(total_return, 6), "max_drawdown": round(max_dd, 6),
            "calmar": round(calmar, 4), "sharpe": round(sharpe, 4), "cvar95": round(cvar95, 6),
            "ulcer": round(ulcer, 6), "n_days": n}


# ══════════════════════════════════════════════════════════════════════
# CR9-A: Freeze risk state from S60
# ══════════════════════════════════════════════════════════════════════

def freeze_risk_state(engine, scores, prices, market_env, calendar,
                      signal_to_exec, exec_to_signal, sdi, pdi, it_trends, specs,
                      start_date, end_date, initial_cash) -> pd.DataFrame:
    """Run S60 and extract the DRO-1 risk state at each signal date."""

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

        position_ratio, dro1_state = controller.get_position(
            features.csi300_ret20, features.turnover_ratio, current_nav)

        risk_rows.append({
            "signal_date": signal_date, "execution_date": trade_date,
            "risk_state": controller.in_risk,
            "trigger_count": dro1_state.get("triggers", 0),
            "csi300_ret20": round(features.csi300_ret20, 4),
            "turnover_ratio": round(features.turnover_ratio, 4),
            "base_nav": round(current_nav, 4),
        })

        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0, trade_cost_rate=0.00075,
                       slippage_rate=0.0, max_total_positions=5, position_ratio=position_ratio,
                       calendar=calendar, open_prices=rpl,
                       targets=targets if not targets.empty else pd.DataFrame(),
                       precommit_prices=None, strict_precommit=False, ledger=None)

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0

    return pd.DataFrame(risk_rows)


# ══════════════════════════════════════════════════════════════════════
# CR9-B: Run backtest with FROZEN risk state
# ══════════════════════════════════════════════════════════════════════

def run_with_frozen_risk(label: str, risk_df: pd.DataFrame,
                         target_position_normal=0.60, target_position_risk=0.40,
                         freeze_buys=False, force_sells=False,
                         engine=None, scores=None, prices=None, market_env=None,
                         calendar=None, signal_to_exec=None, exec_to_signal=None,
                         sdi=None, pdi=None, it_trends=None, specs=None,
                         start_date=None, end_date=None, initial_cash=500000.0,
                         cost_rate=0.00075, slip_rate=0.0) -> dict:
    """Run backtest using FROZEN risk state from risk_df."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in specs if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    # Build risk lookup: signal_date → (in_risk, trigger_count)
    risk_lookup = {}
    for _, row in risk_df.iterrows():
        risk_lookup[row["signal_date"]] = (bool(row["risk_state"]), int(row.get("trigger_count", 0)))

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    nav_rows, trade_rows, blocked_rows = [], [], []
    current_nav = 1.0

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

        # ── FROZEN risk state ────────────────────────────
        in_risk, trig_count = risk_lookup.get(signal_date, (False, 0))
        position_ratio = target_position_risk if in_risk else target_position_normal

        # ── Prepare targets ───────────────────────────────
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())
        existing_syms = set(str(s).zfill(6) for s in account.positions.keys())

        if freeze_buys and in_risk:
            # Block ALL buys — filter targets to empty
            blocked_syms = set()
            if not targets.empty and "symbol" in targets.columns:
                for _, row in targets.iterrows():
                    sym = str(row["symbol"]).zfill(6)
                    if sym not in existing_syms:
                        blocked_syms.add(sym)
                    else:
                        blocked_syms.add(sym)  # block existing adds too
                for sym in blocked_syms:
                    blocked_rows.append({
                        "signal_date": signal_date, "symbol": sym,
                        "risk_state": True, "blocked_reason": "ALL_BUY_FROZEN",
                    })
            targets = pd.DataFrame()  # Empty = no new buys

        # ── Execute ───────────────────────────────────────
        prev_positions = set(account.positions.keys())
        if not targets.empty or account.positions:
            _rebalance(account=account, signal_date=signal_date, execution_date=trade_date,
                       day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                       lot_size=100, min_trade_value=500.0,
                       trade_cost_rate=cost_rate, slippage_rate=slip_rate,
                       max_total_positions=5, position_ratio=position_ratio,
                       calendar=calendar, open_prices=rpl,
                       targets=targets, precommit_prices=None,
                       strict_precommit=False, ledger=None)

        # ── B4: Force sell to reach target exposure ──────
        force_sell_count = 0
        if force_sells and in_risk:
            eq = _equity(account, rpl, "raw_close")
            # Compute current exposure
            market_val = 0.0
            for sym, pos in account.positions.items():
                px = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
                market_val += pos.shares * px
            current_exp = market_val / eq if eq > 0 else 0.0
            target_exp = target_position_risk

            if current_exp > target_exp * 1.05:  # More than 5% above target
                excess_ratio = (current_exp - target_exp) / max(current_exp, 0.001)
                for sym, pos in list(account.positions.items()):
                    px = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
                    if px <= 0: continue
                    sell_shares = int(pos.shares * excess_ratio / 100) * 100
                    if sell_shares <= 0: continue
                    notional = sell_shares * px
                    fee = notional * cost_rate
                    account.cash += notional - fee
                    pos.shares -= sell_shares
                    if pos.shares <= 0:
                        del account.positions[sym]
                    trade_rows.append({
                        "signal_date": signal_date, "execution_date": trade_date,
                        "symbol": sym, "side": "SELL", "order_reason": "FORCE_SELL",
                        "shares_delta": -sell_shares, "price": round(float(px), 2),
                        "notional": round(float(notional), 2),
                        "risk_state": True, "curve": label,
                    })
                    force_sell_count += 1

        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "in_risk": in_risk, "force_sells": force_sell_count,
        })

    nav_df = pd.DataFrame(nav_rows)
    metrics = _compute_full_metrics(nav_df)
    trade_df = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()
    blocked_df = pd.DataFrame(blocked_rows) if blocked_rows else pd.DataFrame()

    n_new_entry_risk = 0
    if not trade_df.empty:
        n_new_entry_risk = int(((trade_df["risk_state"]==True)&(trade_df["side"]=="BUY")).sum())

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "trade_df": trade_df, "blocked_df": blocked_df,
        "n_blocked": len(blocked_df), "n_risk_new_entry": n_new_entry_risk,
        "n_force_sells": int((trade_df["order_reason"]=="FORCE_SELL").sum()) if not trade_df.empty else 0,
    }


# ══════════════════════════════════════════════════════════════════════
# CR12: Placebo with proper Calmar
# ══════════════════════════════════════════════════════════════════════

def calmar_from_nav_series(nav_series):
    nav = np.array(nav_series)
    if len(nav) < 2: return 0.0
    total_ret = nav[-1] / nav[0] - 1.0
    ann_ret = (1 + total_ret) ** (252 / len(nav)) - 1 if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd = np.min((nav - peak) / peak)
    return float(ann_ret / abs(dd)) if abs(dd) > 0 else 0.0


def run_placebo(risk_sequence: list, s60_daily_returns: np.ndarray,
                n_shifts: int, n_blocks: int, target_normal=0.60, target_risk=0.40) -> dict:
    """CR12: Placebo with full NAV series construction."""
    rng = np.random.RandomState(42)
    n_d = len(risk_sequence)

    shift_calmars, block_calmars = [], []
    bs = max(5, n_d // 20)

    for i in range(n_shifts):
        shift = rng.randint(0, n_d)
        rs = risk_sequence[shift:] + risk_sequence[:shift]
        nav = [1.0]
        for j, ret in enumerate(s60_daily_returns):
            pos = target_risk if (j < len(rs) and rs[j]) else target_normal
            nav.append(nav[-1] * (1.0 + ret * pos / target_normal))
        shift_calmars.append(calmar_from_nav_series(nav))

    for i in range(n_blocks):
        valid_blocks = [risk_sequence[j:j+bs] for j in range(0, n_d, bs) if len(risk_sequence[j:j+bs]) == bs]
        rng.shuffle(valid_blocks)
        rb = [v for b in valid_blocks for v in b][:n_d]
        nav = [1.0]
        for j, ret in enumerate(s60_daily_returns):
            pos = target_risk if (j < len(rb) and rb[j]) else target_normal
            nav.append(nav[-1] * (1.0 + ret * pos / target_normal))
        block_calmars.append(calmar_from_nav_series(nav))

    shift_arr = np.array(shift_calmars)
    block_arr = np.array(block_calmars)
    return {
        "shift_median": float(np.median(shift_arr)), "shift_95": float(np.percentile(shift_arr, 95)),
        "block_median": float(np.median(block_arr)), "block_95": float(np.percentile(block_arr, 95)),
        "shift_pvalue": float((1 + np.sum(shift_arr >= shift_arr[0])) / (1 + len(shift_arr))),
        "block_pvalue": float((1 + np.sum(block_arr >= block_arr[0])) / (1 + len(block_arr))),
    }


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="EPC-1 CR9-CR12")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--placebo-shifts", type=int, default=200)
    parser.add_argument("--placebo-blocks", type=int, default=1000)
    args = parser.parse_args()

    print("=" * 60)
    print("EPC-1 CR9-CR12: 动作分离")
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
    out_dir = OUT_ROOT / f"epc1_cr9_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    common = dict(
        engine=engine, scores=ss, prices=ps, market_env=me, calendar=calendar,
        signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi,
        it_trends=it_trends, specs=specs, start_date=args.start_date,
        end_date=args.end_date, initial_cash=args.initial_cash,
    )

    # ══════════════════════════════════════════════════════════
    # CR9-A: Freeze risk state from S60
    # ══════════════════════════════════════════════════════════
    print("\n=== CR9-A: 冻结S60风险状态 ===")
    risk_df = freeze_risk_state(**common)
    risk_df.to_csv(out_dir / "risk_state_frozen.csv", index=False)
    risk_hash = hashlib.sha256(risk_df["risk_state"].to_csv(index=False).encode()).hexdigest()[:16]
    n_risk = int(risk_df["risk_state"].sum())
    print(f"  风险天数: {n_risk}/{len(risk_df)}, Hash: {risk_hash}")

    # ══════════════════════════════════════════════════════════
    # CR9-B: Five orthogonal curves
    # ══════════════════════════════════════════════════════════
    print(f"\n=== CR9-B: 五条正交策略曲线 (冻结风险: {risk_hash}) ===")

    cr9_configs = [
        ("S60", 0.60, 0.60, False, False, "基线"),
        ("B1_FREEZE60", 0.60, 0.60, True, False, "停买+保持60%"),
        ("B2_SCALE40", 0.60, 0.40, False, False, "降仓至40%+正常买入"),
        ("B3_FREEZE40", 0.60, 0.40, True, False, "停买+降仓至40%"),
        ("B4_FORCESELL40", 0.60, 0.40, False, True, "正常买入+强制卖至40%"),
    ]

    results = {}
    for label, pos_n, pos_r, freeze, force, _desc in cr9_configs:
        print(f"  {label}...", end=" ", flush=True)
        r = run_with_frozen_risk(label, risk_df, pos_n, pos_r, freeze, force, **common)
        results[label] = r
        m = r["metrics"]
        print(f"R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f} "
              f"Blocked={r['n_blocked']} RiskNew={r['n_risk_new_entry']} "
              f"ForceSell={r['n_force_sells']}")

    # ══════════════════════════════════════════════════════════
    # CR12: Placebo
    # ══════════════════════════════════════════════════════════
    print(f"\n=== CR12: 完整账户安慰剂 ===")
    s60_nav = results["S60"]["nav_df"]["nav"].values
    s60_daily = np.diff(s60_nav) / s60_nav[:-1] if len(s60_nav) > 1 else np.array([0])
    risk_seq = [bool(r) for r in risk_df["risk_state"].values]

    # Placebo for B3 position (40% risk, 60% normal)
    placebo_b3 = run_placebo(risk_seq, s60_daily, args.placebo_shifts, args.placebo_blocks, 0.60, 0.40)
    b3_calmar = results["B3_FREEZE40"]["metrics"]["calmar"]

    print(f"  B3 Calmar={b3_calmar:.2f}")
    print(f"  Shift median={placebo_b3['shift_median']:.2f} 95%ile={placebo_b3['shift_95']:.2f} p={placebo_b3['shift_pvalue']:.4f}")
    print(f"  Block median={placebo_b3['block_median']:.2f} 95%ile={placebo_b3['block_95']:.2f} p={placebo_b3['block_pvalue']:.4f}")

    # ══════════════════════════════════════════════════════════
    # Action decomposition
    # ══════════════════════════════════════════════════════════
    print(f"\n=== CR9 动作拆解 ===")
    s60_m = results["S60"]["metrics"]
    b1_m = results["B1_FREEZE60"]["metrics"]
    b2_m = results["B2_SCALE40"]["metrics"]
    b3_m = results["B3_FREEZE40"]["metrics"]
    b4_m = results["B4_FORCESELL40"]["metrics"]

    # Pure stop-buy effect (B1 - S60, same 60% position)
    pure_stop_buy = b1_m["total_return"] - s60_m["total_return"]
    # Pure scale effect (B2 - S60, same buy rules, lower position)
    pure_scale = b2_m["total_return"] - s60_m["total_return"]
    # Interaction (B3 - S60) vs sum of individual effects
    interactive = b3_m["total_return"] - s60_m["total_return"]
    sum_individual = pure_stop_buy + pure_scale
    interaction_effect = interactive - sum_individual
    # Force sell contribution (B4 - B2, both at 40% with normal buys)
    force_sell_effect = b4_m["total_return"] - b2_m["total_return"]

    print(f"  纯停买效应 (B1-S60):          {pure_stop_buy:+.2%}")
    print(f"  纯降仓效应 (B2-S60):          {pure_scale:+.2%}")
    print(f"  停买+降仓 (B3-S60):           {interactive:+.2%}")
    print(f"  个体效应之和:                  {sum_individual:+.2%}")
    print(f"  交互效应:                      {interaction_effect:+.2%}")
    print(f"  强制卖出增量 (B4-B2):          {force_sell_effect:+.2%}")

    dominant = max(
        ("纯停买", abs(pure_stop_buy)),
        ("纯降仓", abs(pure_scale)),
        ("停买+降仓交互", abs(interactive)),
        ("强制卖出", abs(force_sell_effect)),
        key=lambda x: x[1],
    )
    print(f"\n  主导效应: {dominant[0]}")

    # ══════════════════════════════════════════════════════════
    # Save & Report
    # ══════════════════════════════════════════════════════════
    for label, r in results.items():
        r["nav_df"].to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)

    pd.DataFrame([{"curve": label, **r["metrics"], "n_blocked": r["n_blocked"],
                    "n_risk_new_entry": r["n_risk_new_entry"],
                    "n_force_sells": r["n_force_sells"]}
                   for label, r in results.items()]).to_csv(out_dir / "cr9_summary.csv", index=False)

    report = [
        "# EPC-1 CR9-CR12 动作分离报告",
        f"## 风险状态: {n_risk}/{len(risk_df)} 风险日, Hash={risk_hash}",
        "",
        "## CR9-B: 五条正交曲线",
        "| 曲线 | 收益 | MaxDD | Calmar | 被阻止 | 强制卖出 |",
        "|------|------|-------|--------|--------|----------|",
    ]
    for label, _, _, _, _, _ in cr9_configs:
        r = results[label]; m = r["metrics"]
        report.append(f"| {label} | {m['total_return']:.2%} | {m['max_drawdown']:.2%} | {m['calmar']:.2f} | {r['n_blocked']} | {r['n_force_sells']} |")

    report += [
        "",
        "## 动作拆解",
        f"- 纯停买效应: {pure_stop_buy:+.2%}",
        f"- 纯降仓效应: {pure_scale:+.2%}",
        f"- 停买+降仓: {interactive:+.2%}",
        f"- 交互效应: {interaction_effect:+.2%}",
        f"- 强制卖出增量: {force_sell_effect:+.2%}",
        f"- **主导效应: {dominant[0]}**",
    ]

    (out_dir / "cr9_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"主导效应: {dominant[0]}")
    print(f"报告: {out_dir}/cr9_report.md")
    print("\nDone.")


def _sim_nav(risk_seq, daily_rets, rng=None):
    nav = [1.0]
    for j, ret in enumerate(daily_rets):
        pos = 0.40 if (j < len(risk_seq) and risk_seq[j]) else 0.60
        nav.append(nav[-1] * (1.0 + ret * pos / 0.60))
    return nav


if __name__ == "__main__":
    main()
