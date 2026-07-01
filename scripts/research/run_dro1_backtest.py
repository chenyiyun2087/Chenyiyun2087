#!/usr/bin/env python3
"""
DRO-1: Downside Risk Overlay v1 — 只降风险的被动覆盖层

G0: S55/S60 基线确认 + Scale-Only 对照
DRO-1: 60%基础仓位, 2-of-3触发降为40%, 不主动加仓
Walk-Forward: 3折滚动验证

触发条件(需≥2项):
  A: CSI300 20日收益 ≤ -6%
  B: 全市场成交额/20日均值 ≤ 0.85
  C: 策略账户从历史高点回撤 ≥ 8%

恢复条件:
  连续5日不满足2项触发 AND 账户回撤 > -5% → 恢复60%

Usage:
    python scripts/research/run_dro1_backtest.py \
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
from scripts.research.run_b1_t3_r2_validation import run_single_backtest

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# DRO-1 Position Controller
# ══════════════════════════════════════════════════════════════════════

class DRO1Controller:
    """
    Passive downside risk overlay.
    Normal: 60%. Risk: 40%. Never goes above 60%.
    """

    def __init__(self, base_position=0.60, risk_position=0.40,
                 csi300_threshold=-0.06, turnover_threshold=0.85,
                 account_dd_threshold=-0.08, recovery_days=5,
                 recovery_dd_threshold=-0.05):
        self.base = base_position
        self.risk = risk_position
        self.csi300_threshold = csi300_threshold
        self.turnover_threshold = turnover_threshold
        self.account_dd_threshold = account_dd_threshold
        self.recovery_days_needed = recovery_days
        self.recovery_dd_threshold = recovery_dd_threshold
        self.in_risk = False
        self.recovery_count = 0
        self.peak_nav = 1.0
        self.decision_log = []

    def update_peak(self, nav: float):
        if nav > self.peak_nav:
            self.peak_nav = nav

    def get_account_dd(self, current_nav: float) -> float:
        if self.peak_nav <= 0:
            return 0.0
        return (current_nav / self.peak_nav - 1.0)

    def get_position(self, csi300_ret20: float, turnover_ratio: float,
                     current_nav: float) -> tuple:
        """Returns (target_position, state_dict)."""
        self.update_peak(current_nav)
        acct_dd = self.get_account_dd(current_nav)

        # Count triggers
        triggers = 0
        reasons = []
        if csi300_ret20 <= self.csi300_threshold:
            triggers += 1; reasons.append(f"CSI300_ret20={csi300_ret20:.4f}≤{self.csi300_threshold}")
        if turnover_ratio <= self.turnover_threshold:
            triggers += 1; reasons.append(f"turnover={turnover_ratio:.3f}≤{self.turnover_threshold}")
        if acct_dd <= self.account_dd_threshold:
            triggers += 1; reasons.append(f"acct_dd={acct_dd:.4f}≤{self.account_dd_threshold}")

        prev_state = self.in_risk

        if triggers >= 2:
            self.in_risk = True
            self.recovery_count = 0
            event = "ENTER_RISK" if not prev_state else "STAY_RISK"
        elif self.in_risk:
            if acct_dd > self.recovery_dd_threshold:
                self.recovery_count += 1
                event = "RECOVERING"
            else:
                self.recovery_count = 0
                event = "STAY_RISK"

            if self.recovery_count >= self.recovery_days_needed:
                self.in_risk = False
                self.recovery_count = 0
                event = "EXIT_RISK"
        else:
            event = "NORMAL"

        target = self.risk if self.in_risk else self.base
        state = {
            "in_risk": self.in_risk, "event": event, "triggers": triggers,
            "csi300_ret20": round(csi300_ret20, 4),
            "turnover_ratio": round(turnover_ratio, 4),
            "acct_dd": round(acct_dd, 4),
            "recovery_count": self.recovery_count,
            "reasons": "; ".join(reasons),
        }
        return target, state


# ══════════════════════════════════════════════════════════════════════
# Backtest runner with DRO-1
# ══════════════════════════════════════════════════════════════════════

def run_dro1_backtest(
    label: str, base_position: float, use_dro1: bool = False,
    ideal_locking: bool = False,
    engine=None, scores=None, prices=None, market_env=None,
    calendar=None, signal_to_exec=None, exec_to_signal=None,
    score_day_indices=None, price_day_indices=None, index_trends=None,
    strategy_specs=None, start_date=None, end_date=None,
    initial_cash=500000.0,
) -> dict:
    """Run backtest with optional DRO-1 overlay."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in strategy_specs if s.name == strategy_name]
    if not matched:
        return {"label": label, "error": "strategy_not_found"}
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
    nav_rows, decision_rows, trade_count = [], [], 0

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_cal = [d for d in sim_cal if d >= first_exec]

    price_indices_orig = prices.groupby("trade_date", sort=True).indices
    current_nav = 1.0

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            eq = account.cash
            current_nav = eq / initial_cash
            nav_rows.append({"trade_date": trade_date, "nav": current_nav,
                             "position_ratio": 0.0, "position_count": 0,
                             "equity": eq, "cash": account.cash})
            continue

        # ── Market features ─────────────────────────────
        day_scores = _score_day_frame(scores, score_day_indices, signal_date)
        price_snap = pd.DataFrame()
        if signal_date in price_indices_orig:
            price_snap = prices.iloc[price_indices_orig[signal_date]]
        me_row = None
        if market_env is not None and "trade_date" in market_env.columns:
            me_m = market_env[market_env["trade_date"] == signal_date]
            if not me_m.empty: me_row = me_m.iloc[0]
        features = build_daily_features(signal_date, day_scores, price_snap, index_trends, me_row)

        # ── Position ratio ─────────────────────────────
        if controller is not None:
            position_ratio, dro1_state = controller.get_position(
                features.csi300_ret20, features.turnover_ratio, current_nav)
            decision_rows.append({
                "signal_date": signal_date, "execution_date": trade_date,
                **dro1_state, "target_position": round(position_ratio, 4),
            })
        else:
            position_ratio = base_position

        # ── Execute ─────────────────────────────────────
        rpl = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            # For ideal locking: allow selling locked positions during risk
            # (D1-ideal: bypass hold_days constraint)
            override_hold = 0 if (ideal_locking and controller is not None and controller.in_risk) else 10

            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5,
                hold_days=override_hold, lot_size=100, min_trade_value=500.0,
                trade_cost_rate=0.00075, slippage_rate=0.0,
                max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=None,
            )
            trade_count += int(meta.get("executed", 0))

        # ── Record ──────────────────────────────────────
        eq = _equity(account, rpl, "raw_close")
        current_nav = eq / initial_cash if initial_cash > 0 else 1.0
        locked_value = 0.0
        for sym, pos in account.positions.items():
            price = _safe_float(rpl.get(sym, {}).get("raw_close"), 0)
            locked_value += pos.shares * price

        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "nav": round(current_nav, 6), "equity": round(eq, 2),
            "cash": round(account.cash, 2),
            "position_ratio": round(position_ratio, 4),
            "position_count": len(account.positions),
            "locked_value": round(locked_value, 2),
            "in_risk": controller.in_risk if controller else False,
        })

    nav_df = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    metrics = _compute_metrics(nav_df)

    # DRO-1 specific stats
    dro1_stats = {}
    if decision_rows:
        dd = pd.DataFrame(decision_rows)
        risk_days = int((dd["in_risk"] == True).sum())
        enter_events = int((dd["event"] == "ENTER_RISK").sum())
        dro1_stats = {
            "risk_days": risk_days, "risk_enter_events": enter_events,
            "risk_pct": round(risk_days / len(dd) * 100, 1) if len(dd) > 0 else 0,
            "avg_risk_position": round(dd[dd["in_risk"] == True]["target_position"].mean(), 4) if risk_days > 0 else 0,
        }
        # Actual exposure during risk
        risk_nav = nav_df[nav_df["in_risk"] == True]
        if len(risk_nav) > 0:
            risk_nav_copy = risk_nav.copy()
            risk_nav_copy["actual_exp"] = (risk_nav_copy["equity"] - risk_nav_copy["cash"]) / risk_nav_copy["equity"].replace(0, np.nan)
            dro1_stats["avg_actual_exposure_in_risk"] = round(float(risk_nav_copy["actual_exp"].mean()), 4)
            dro1_stats["avg_locked_value_in_risk"] = round(float(risk_nav["locked_value"].mean()), 0)

    return {
        "label": label, "nav_df": nav_df, "metrics": metrics,
        "decisions": decision_rows, "dro1_stats": dro1_stats,
        "trade_count": trade_count,
    }


def _compute_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "max_drawdown": 0, "calmar": 0}
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
    trough_idx = int(np.argmin(dd_series))
    dd_start = trough_idx
    for i in range(trough_idx, -1, -1):
        if dd_series[i] > -0.001: dd_start = i + 1; break
    dd_end = trough_idx
    for i in range(trough_idx, n):
        if dd_series[i] > -0.001: dd_end = i; break
    return {
        "total_return": round(total_return, 6), "annualized_return": round(ann_ret, 6),
        "max_drawdown": round(max_dd, 6), "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4), "volatility": round(vol, 6),
        "cvar95": round(cvar95, 6), "ulcer": round(ulcer, 6),
        "max_dd_duration": dd_end - dd_start, "n_days": n,
    }


def run_walkforward_fold(fold_name, train_sd, train_ed, test_sd, test_ed,
                          engine, scores, prices, market_env, calendar,
                          signal_to_exec, exec_to_signal, sdi, pdi, it_trends,
                          strategy_specs, initial_cash) -> dict:
    """Run S55, S60, D1 on a single walk-forward test fold."""
    common = dict(
        engine=engine, scores=scores, prices=prices, market_env=market_env,
        calendar=calendar, signal_to_exec=signal_to_exec,
        exec_to_signal=exec_to_signal, score_day_indices=sdi,
        price_day_indices=pdi, index_trends=it_trends,
        strategy_specs=strategy_specs, initial_cash=initial_cash,
    )
    fold_results = {}
    for label, base_pos, use_dro1, ideal_lock in [
        ("S55", 0.55, False, False),
        ("S60", 0.60, False, False),
        ("D1", 0.60, True, False),
        ("D1_ideal", 0.60, True, True),
    ]:
        r = run_dro1_backtest(
            label=f"{fold_name}_{label}", base_position=base_pos,
            use_dro1=use_dro1, ideal_locking=ideal_lock,
            start_date=test_sd, end_date=test_ed, **common,
        )
        fold_results[label] = r
    return fold_results


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="DRO-1 Backtest")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--skip-wf", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("DRO-1: 只降风险的被动覆盖层")
    print("=" * 60)

    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # ── Load data ────────────────────────────────────────────
    print("Loading...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    print(f"  Calendar: {len(calendar)} days")

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

    # ── Output ───────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"dro1_{ts}" if not args.output_dir else Path(args.output_dir)
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
    # G0: Static baselines + DRO-1 full-sample
    # ══════════════════════════════════════════════════════════
    print("\n=== G0 + DRO-1 全样本 ===")
    results = {}
    for label, base_pos, use_dro1, ideal_lock, desc in [
        ("S55", 0.55, False, False, "固定55%基线"),
        ("S60", 0.60, False, False, "固定60%基线"),
        ("D1", 0.60, True, False, "DRO-1 严格锁定"),
        ("D1_ideal", 0.60, True, True, "DRO-1 理想(无锁定阻塞)"),
    ]:
        print(f"  {label} ({desc})...", end=" ", flush=True)
        r = run_dro1_backtest(label, base_pos, use_dro1, ideal_lock, **common)
        results[label] = r
        m = r["metrics"]
        ds = r.get("dro1_stats", {})
        risk_info = f" RiskDays={ds.get('risk_days',0)} ActExpRisk={ds.get('avg_actual_exposure_in_risk',0):.1%}" if ds else ""
        print(f"R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f} "
              f"CVaR95={m['cvar95']:.4f} Ulcer={m['ulcer']:.4f}{risk_info}")

    # ── DRO-1 acceptance check ───────────────────────────────
    s60 = results["S60"]["metrics"]
    d1 = results["D1"]["metrics"]
    d1_ideal = results["D1_ideal"]["metrics"]
    d1_stats = results["D1"].get("dro1_stats", {})

    print(f"\n=== DRO-1 准入评估 (D1 vs S60) ===")
    dd_improve = abs(d1["max_drawdown"]) / abs(s60["max_drawdown"])  # < 1.0 means improvement
    ulcer_improve = d1["ulcer"] / s60["ulcer"]  # < 1.0 means improvement
    cvar_change = (d1["cvar95"] - s60["cvar95"]) / s60["cvar95"] * 100  # < +5% means non-deterioration
    ret_ratio = d1["total_return"] / s60["total_return"] if s60["total_return"] != 0 else 0

    checks = [
        ("MaxDD降低≥15%", dd_improve <= 0.85, f"D1/S60={dd_improve:.3f}"),
        ("Ulcer降低≥15%", ulcer_improve <= 0.85, f"D1/S60={ulcer_improve:.3f}"),
        ("CVaR95不恶化>5%", cvar_change <= 5, f"{cvar_change:+.1f}%"),
        ("收益保留≥85%", ret_ratio >= 0.85, f"{ret_ratio:.1%}"),
    ]
    for name, passed, val in checks:
        print(f"  {'✅' if passed else '❌'} {name}: {val}")

    # ── Locking constraint cost ──────────────────────────────
    print(f"\n=== G0: 持仓锁定约束成本 ===")
    print(f"  D1 实际暴露(风险期): {d1_stats.get('avg_actual_exposure_in_risk', 0):.1%}")
    print(f"  D1 锁定市值(风险期均值): {d1_stats.get('avg_locked_value_in_risk', 0):,.0f}")
    print(f"  D1_ideal Calmar: {d1_ideal['calmar']:.2f} vs D1 Calmar: {d1['calmar']:.2f}")
    locking_cost = d1_ideal["calmar"] - d1["calmar"]
    print(f"  锁定约束的Calmar成本: {locking_cost:+.2f}")

    # ── Scale-only approximation (G0) ────────────────────────
    print(f"\n=== G0: Scale-Only对照 ===")
    s60_nav = results["S60"]["nav_df"]["nav"].values
    s55_nav = results["S55"]["nav_df"]["nav"].values
    if len(s60_nav) > 0 and len(s55_nav) > 0:
        # Approximate scale-only: daily returns scaled by position ratio difference
        s60_daily = np.diff(s60_nav) / s60_nav[:-1] if len(s60_nav) > 1 else np.array([0])
        scale_only_nav = [1.0]
        for r in s60_daily:
            # Scale return by 55/60 ratio, but keep cash drag effect
            scaled_r = r * 0.55 / 0.60
            scale_only_nav.append(scale_only_nav[-1] * (1 + scaled_r))
        scale_only_ret = scale_only_nav[-1] - 1.0
        scale_only_peak = np.maximum.accumulate(scale_only_nav)
        scale_only_dd = float(np.min((np.array(scale_only_nav) - scale_only_peak) / scale_only_peak))
        print(f"  Scale-Only 55%: R={scale_only_ret:.2%} DD={scale_only_dd:.2%}")
        print(f"  Real S55:       R={results['S55']['metrics']['total_return']:.2%} DD={results['S55']['metrics']['max_drawdown']:.2%}")
        real_vs_scale = results["S55"]["metrics"]["total_return"] - scale_only_ret
        print(f"  执行路径效应: {real_vs_scale:+.2%} (Real - ScaleOnly)")

    # ══════════════════════════════════════════════════════════
    # Walk-Forward
    # ══════════════════════════════════════════════════════════
    wf_results = []
    if not args.skip_wf:
        print(f"\n=== Walk-Forward (3折) ===")
        folds = [
            ("Fold1", "2023-01-03", "2024-06-28", "2024-07-01", "2025-01-31"),
            ("Fold2", "2023-01-03", "2025-01-31", "2025-02-05", "2025-08-29"),
            ("Fold3", "2023-01-03", "2025-08-29", "2025-09-01", "2026-06-30"),
        ]
        for fname, tr_sd, tr_ed, te_sd, te_ed in folds:
            print(f"  {fname}: train={tr_sd}→{tr_ed}, test={te_sd}→{te_ed}")
            fr = run_walkforward_fold(
                fname, tr_sd, tr_ed, te_sd, te_ed,
                engine, ss, ps, me, calendar, s2e, e2s, sdi, pdi, it_trends, specs, args.initial_cash,
            )
            for label, r in fr.items():
                m = r["metrics"]
                print(f"    {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")
            wf_results.append(fr)

    # ══════════════════════════════════════════════════════════
    # Save outputs
    # ══════════════════════════════════════════════════════════
    for label, r in results.items():
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf.to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
        decs = r.get("decisions")
        if decs:
            pd.DataFrame(decs).to_csv(out_dir / f"decisions_{label.lower()}.csv", index=False)

    summary_rows = []
    for label, r in results.items():
        m = r["metrics"]; ds = r.get("dro1_stats", {})
        summary_rows.append({
            "curve": label, "use_dro1": "D1" in label,
            "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
            "calmar": m["calmar"], "sharpe": m["sharpe"],
            "cvar95": m["cvar95"], "ulcer": m["ulcer"],
            "max_dd_duration": m["max_dd_duration"],
            "risk_days": ds.get("risk_days", 0),
            "avg_actual_exp_risk": ds.get("avg_actual_exposure_in_risk", 0),
        })
    pd.DataFrame(summary_rows).to_csv(out_dir / "dro1_summary.csv", index=False)

    # ── Report ───────────────────────────────────────────────
    report = [
        "# DRO-1 回测报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 全样本结果",
        "| 曲线 | 总收益 | MaxDD | Calmar | CVaR95 | Ulcer | DDdur | 风险天数 |",
        "|------|--------|-------|--------|--------|-------|------|----------|",
    ]
    for label in ["S55", "S60", "D1", "D1_ideal"]:
        r = results[label]; m = r["metrics"]; ds = r.get("dro1_stats", {})
        report.append(
            f"| {label} | {m['total_return']:.2%} | {m['max_drawdown']:.2%} | "
            f"{m['calmar']:.2f} | {m['cvar95']:.4f} | {m['ulcer']:.4f} | "
            f"{m['max_dd_duration']}d | {ds.get('risk_days',0)} |")

    report += [
        "",
        "## DRO-1 准入评估 (D1 vs S60)",
        f"- MaxDD: {dd_improve:+.1f}% {'✅' if dd_improve <= -15 else '❌'}",
        f"- Ulcer: {ulcer_improve:+.1f}% {'✅' if ulcer_improve <= -15 else '❌'}",
        f"- CVaR95: {cvar_change:+.1f}% {'✅' if cvar_change <= 5 else '❌'}",
        f"- 收益保留: {ret_ratio:.1%} {'✅' if ret_ratio >= 0.85 else '❌'}",
        "",
        "## 持仓锁定约束成本",
        f"- D1_ideal Calmar: {d1_ideal['calmar']:.2f}",
        f"- D1 Calmar: {d1['calmar']:.2f}",
        f"- 锁定成本: {locking_cost:+.2f} Calmar",
        "",
        "## Walk-Forward",
    ]

    if wf_results:
        for fi, fr in enumerate(wf_results):
            fname = folds[fi][0]
            report.append(f"### {fname}")
            for label, r in fr.items():
                m = r["metrics"]
                report.append(f"- {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

    (out_dir / "dro1_report.md").write_text("\n".join(report))

    # ── Final summary ────────────────────────────────────────
    print(f"\n{'='*60}")
    print("FINAL DECISION")
    print("=" * 60)
    all_checks_pass = all(c[1] for c in checks)
    print(f"  DRO-1 准入: {'✅ 全部通过' if all_checks_pass else '❌ 未通过'} ({sum(1 for c in checks if c[1])}/{len(checks)})")
    if all_checks_pass:
        print(f"  评级: RESEARCH_VALIDATED — 可进入60日无资金前瞻记录")
    else:
        print(f"  评级: RESEARCH_ARCHIVED — 降仓效果不足以补偿收益损失")
    print(f"\n  报告: {out_dir}/dro1_report.md")
    print("\nDone.")


if __name__ == "__main__":
    main()
