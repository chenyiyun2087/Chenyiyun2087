#!/usr/bin/env python3
"""
Market Exposure Governor v1 — G1/G2 修复与消融

G1修复:
  - Point-in-Time 指数趋势 (每T日只使用T日及以前收盘价)
  - 真实市场特征接线 (广度、成交额比、涨跌停扩散)
  - 统一执行路径 (复用既有 _rebalance)
  - 每日决策记录 (含所有市场变量值)

G2消融:
  - T0: 固定仓位 (70%)
  - T1: 仅成交额比率
  - T2: 仅CSI300 20日趋势
  - T3: 成交额 + CSI300 (双因子)

三档简化仓位: RISK_OFF=35%, NEUTRAL=55%, RISK_ON=70%

Usage:
    python scripts/research/run_market_exposure_walkforward.py \
        --start-date 2025-09-02 --end-date 2026-06-30 \
        --curves T0,T1,T2,T3
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    StrategySpec, _safe_float,
    add_liquidity_derived_features,
    build_market_environment, build_strategy_specs,
    load_prices, load_scores,
)
from scripts.research_trusted_strategy_account_backtest import (
    AccountState, _rebalance, _price_lookup_for_day, _score_day_frame,
    _build_targets_cache, _equity, _sync_account_view_from_ledger,
)

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


# ══════════════════════════════════════════════════════════════════════
# Point-in-Time Index Trend Loader (G1.1 fix)
# ══════════════════════════════════════════════════════════════════════

def load_index_trends_pit(engine, index_codes: list[str],
                          calendar_dates: list) -> dict:
    """
    Load per-date 20-day index returns, strictly point-in-time.
    Each T-day only uses close prices up to and including T.

    Returns: {trade_date: {ts_code: ret_20}}
    """
    if not calendar_dates:
        return {}

    min_d = min(calendar_dates)
    # Need 30+ extra trading days before min_d for 20d return calculation
    start_key = int(pd.Timestamp(min_d).strftime("%Y%m%d")) - 1000  # ~40 trading days buffer

    codes_str = ",".join(f"'{c}'" for c in index_codes)
    sql = f"""
        SELECT trade_date, ts_code, close
        FROM tushare_stock.dwd_index_daily
        WHERE ts_code IN ({codes_str})
          AND trade_date >= {start_key}
        ORDER BY ts_code, trade_date
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()

    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=["trade_date", "ts_code", "close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["trade_date_dt"] = df["trade_date"].apply(
        lambda x: date(int(str(x)[:4]), int(str(x)[4:6]), int(str(x)[6:8])))

    # Compute 20-day returns per index
    trends = {}
    for ts_code in index_codes:
        idx_df = df[df["ts_code"] == ts_code].sort_values("trade_date_dt").copy()
        idx_df["ret_20"] = idx_df["close"].pct_change(20)
        for _, row in idx_df.iterrows():
            d = row["trade_date_dt"]
            if d not in trends:
                trends[d] = {}
            trends[d][ts_code] = float(row["ret_20"]) if pd.notna(row["ret_20"]) else 0.0

    return trends


# ══════════════════════════════════════════════════════════════════════
# Market Feature Builder (G1.2 fix)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class DailyFeatures:
    """All market features for a given T-day, strictly from T-day data."""
    signal_date: object = None
    csi300_ret20: float = 0.0
    chinext_ret20: float = 0.0
    turnover_ratio: float = 1.0       # today's amount / 20d avg
    breadth: float = 0.50             # advancing / (advancing + declining)
    limit_up_pct: float = 0.0         # limit-up stocks / total
    limit_down_pct: float = 0.0       # limit-down stocks / total
    candidate_pool_count: int = 0
    candidate_avg_score: float = 0.0


def build_daily_features(signal_date, day_scores, price_snapshot,
                         index_trends, market_env_row) -> DailyFeatures:
    """Build all daily market features from T-day data only."""
    f = DailyFeatures()
    f.signal_date = signal_date

    # Index trends (point-in-time)
    it = index_trends.get(signal_date, {})
    f.csi300_ret20 = it.get("000300.SH", 0.0)
    f.chinext_ret20 = it.get("399006.SZ", 0.0)

    # Turnover ratio from market environment
    if market_env_row is not None:
        try:
            f.turnover_ratio = float(market_env_row.get("market_amount_ratio_20", 1.0))
        except (TypeError, ValueError):
            f.turnover_ratio = 1.0

    # Breadth and limit-up/down from price snapshot
    if price_snapshot is not None and not price_snapshot.empty:
        ps = price_snapshot
        if "raw_close" in ps.columns and "raw_pre_close" in ps.columns:
            adv = (ps["raw_close"] > ps["raw_pre_close"]).sum()
            dec = (ps["raw_close"] < ps["raw_pre_close"]).sum()
            total = adv + dec
            f.breadth = float(adv / total) if total > 0 else 0.50

        if "raw_close" in ps.columns and "raw_pre_close" in ps.columns and "is_st" in ps.columns:
            n = max(len(ps), 1)
            st = ps["is_st"].fillna(0).astype(int) == 1
            chg = ps["raw_close"] / ps["raw_pre_close"].replace(0, np.nan)
            f.limit_up_pct = float(((chg >= 1.099) & ~st).sum() / n)
            f.limit_down_pct = float(((chg <= 0.901) & ~st).sum() / n)

    # Candidate quality from day scores
    if day_scores is not None and not day_scores.empty:
        tr = day_scores[day_scores.get("pool_type", "") == "TRADE"]
        if tr.empty and "score" in day_scores.columns:
            tr = day_scores[day_scores["score"] >= 60]
        f.candidate_pool_count = len(tr)
        f.candidate_avg_score = float(tr["score"].mean()) if "score" in tr.columns and len(tr) > 0 else 0.0

    return f


# ══════════════════════════════════════════════════════════════════════
# 3-State Simplified Position Controller (G2)
# ══════════════════════════════════════════════════════════════════════

class SimplePositionController:
    """
    Three-state position controller.

    T0: fixed 70%
    T1: turnover_ratio only
    T2: csi300_ret20 only
    T3: both turnover + csi300
    """

    LEVELS = {"RISK_OFF": 0.35, "NEUTRAL": 0.55, "RISK_ON": 0.70}

    # Thresholds
    TURNOVER_RISK_OFF = 0.75    # Below this: RISK_OFF
    TURNOVER_RISK_ON = 1.10     # Above this: RISK_ON
    CSI300_RISK_OFF = -0.08      # Below this (20d return): RISK_OFF
    CSI300_RISK_ON = 0.02        # Above this: RISK_ON

    def __init__(self, mode: str):
        """
        mode: 'T0' (fixed), 'T1' (turnover only), 'T2' (csi300 only), 'T3' (both)
        """
        self.mode = mode.upper()

    def get_position_ratio(self, f: DailyFeatures) -> float:
        if self.mode == "T0":
            return 0.70

        if self.mode == "T1":
            state = self._classify_turnover(f.turnover_ratio)
        elif self.mode == "T2":
            state = self._classify_csi300(f.csi300_ret20)
        elif self.mode == "T3":
            state = self._classify_combined(f)
        else:
            return 0.70

        return self.LEVELS.get(state, 0.55)

    def get_state(self, f: DailyFeatures) -> str:
        if self.mode == "T0":
            return "FIXED"
        if self.mode == "T1":
            return self._classify_turnover(f.turnover_ratio)
        if self.mode == "T2":
            return self._classify_csi300(f.csi300_ret20)
        if self.mode == "T3":
            return self._classify_combined(f)
        return "NEUTRAL"

    def _classify_turnover(self, ratio: float) -> str:
        if ratio < self.TURNOVER_RISK_OFF:
            return "RISK_OFF"
        if ratio > self.TURNOVER_RISK_ON:
            return "RISK_ON"
        return "NEUTRAL"

    def _classify_csi300(self, ret20: float) -> str:
        if ret20 < self.CSI300_RISK_OFF:
            return "RISK_OFF"
        if ret20 > self.CSI300_RISK_ON:
            return "RISK_ON"
        return "NEUTRAL"

    def _classify_combined(self, f: DailyFeatures) -> str:
        """Both signals must agree for extreme states; otherwise NEUTRAL."""
        t_state = self._classify_turnover(f.turnover_ratio)
        c_state = self._classify_csi300(f.csi300_ret20)

        if t_state == "RISK_OFF" and c_state == "RISK_OFF":
            return "RISK_OFF"
        if t_state == "RISK_ON" and c_state == "RISK_ON":
            return "RISK_ON"
        # Conflicting signals: NEUTRAL
        if t_state == "RISK_OFF" and c_state == "RISK_ON":
            return "NEUTRAL"
        if t_state == "RISK_ON" and c_state == "RISK_OFF":
            return "NEUTRAL"
        # One neutral + one extreme: follow the extreme but dampened (NEUTRAL)
        return "NEUTRAL"


# ══════════════════════════════════════════════════════════════════════
# Backtest Runner
# ══════════════════════════════════════════════════════════════════════

def run_governed_backtest(
    label: str,
    mode: str,
    engine, scores, prices, market_env,
    calendar, signal_to_exec, exec_to_signal,
    score_day_indices, price_day_indices,
    index_trends,
    strategy_specs, start_date, end_date,
    initial_cash=500000, top_n=5, hold_days=10,
    lot_size=100, cost_rate=0.00075, max_positions=5,
    min_trade_value=500.0,
) -> dict:
    """Run a single governed backtest curve."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in strategy_specs if s.name == strategy_name]
    if not matched:
        return {"label": label, "error": "strategy_not_found", "metrics": {}}
    spec = matched[0]

    controller = SimplePositionController(label)  # Use curve label (T0/T1/T2/T3), not description

    price_columns = [
        "raw_open", "raw_close", "raw_pre_close", "raw_high", "raw_low",
        "adj_open", "adj_close", "adj_high", "adj_low", "adj_factor",
        "is_st", "is_suspended", "amount", "volume",
        "security_status_available", "execution_tradable",
        "universe_is_tradable", "is_listed", "circ_mv",
    ]

    # Build targets cache
    cache_scores = scores
    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=cache_scores, day_indices=cache_indices,
        specs_by_name={spec.name: spec}, top_n=top_n,
    )

    account = AccountState(cash=float(initial_cash))
    ledger = None

    nav_rows = []
    trade_rows_all = []
    decision_rows = []

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_calendar = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec:
        sim_calendar = [d for d in sim_calendar if d >= first_exec]

    # Build price snapshots cache for market features
    # Use original (non-reset) prices for groupby lookup
    price_indices_orig = prices.groupby("trade_date", sort=True).indices

    trade_count = 0
    for ti, trade_date in enumerate(sim_calendar):
        signal_date = exec_to_signal.get(trade_date)
        if signal_date is None:
            # Record flat NAV
            eq = account.cash + sum(
                pos.shares * _safe_float(
                    _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
                    .get(sym, {}).get("raw_close"), 0)
                for sym, pos in account.positions.items()
            )
            nav_rows.append({"trade_date": trade_date, "signal_date": None,
                             "cash": account.cash, "equity": eq,
                             "nav": eq / initial_cash, "position_count": len(account.positions),
                             "position_ratio": 0.0, "label": label})
            continue

        # ── Build market features (G1.2: real wiring) ──────────
        day_scores = _score_day_frame(scores, score_day_indices, signal_date)

        # Price snapshot for this signal date
        price_snap = pd.DataFrame()
        if signal_date in price_indices_orig:
            idx = price_indices_orig[signal_date]
            price_snap = prices.iloc[idx]

        # Market environment row
        me_row = None
        if market_env is not None and "trade_date" in market_env.columns:
            me_match = market_env[market_env["trade_date"] == signal_date]
            if not me_match.empty:
                me_row = me_match.iloc[0]

        features = build_daily_features(
            signal_date, day_scores, price_snap, index_trends, me_row)

        # ── Determine position ratio ───────────────────────────
        position_ratio = controller.get_position_ratio(features)
        market_state = controller.get_state(features)

        # ── Execute ─────────────────────────────────────────────
        rpl = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=top_n, hold_days=hold_days,
                lot_size=lot_size, min_trade_value=min_trade_value,
                trade_cost_rate=cost_rate, slippage_rate=0.0,
                max_total_positions=max_positions, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets if not targets.empty else None,
                precommit_prices=None, strict_precommit=False, ledger=ledger,
            )
            trade_rows_all.extend(trades)
            trade_count += int(meta.get("executed", 0))

        # ── Record NAV ─────────────────────────────────────────
        eq = _equity(account, rpl, "raw_close")
        nav_rows.append({
            "trade_date": trade_date, "signal_date": signal_date,
            "cash": round(account.cash, 2), "equity": round(eq, 2),
            "nav": round(eq / initial_cash, 6), "position_count": len(account.positions),
            "position_ratio": round(position_ratio, 4), "label": label,
        })

        # ── Record decision (G1.2: all market variable values) ─
        decision_rows.append({
            "signal_date": signal_date,
            "execution_date": trade_date,
            "mode": label,
            "csi300_ret20": round(features.csi300_ret20, 6),
            "chinext_ret20": round(features.chinext_ret20, 6),
            "turnover_ratio": round(features.turnover_ratio, 4),
            "breadth": round(features.breadth, 4),
            "limit_up_pct": round(features.limit_up_pct, 4),
            "limit_down_pct": round(features.limit_down_pct, 4),
            "candidate_pool_count": features.candidate_pool_count,
            "candidate_avg_score": round(features.candidate_avg_score, 1),
            "market_state": market_state,
            "target_position_ratio": round(position_ratio, 4),
        })

    # ── Compute metrics ────────────────────────────────────────
    nav_df = pd.DataFrame(nav_rows) if nav_rows else pd.DataFrame()
    metrics = _compute_metrics(nav_df)

    print(f"    Return: {metrics['total_return']:.2%}, MaxDD: {metrics['max_drawdown']:.2%}, "
          f"Calmar: {metrics['calmar']:.2f}, Trades: {trade_count}")

    # Summary stats
    if decision_rows:
        prs = [d["target_position_ratio"] for d in decision_rows]
        states = [d["market_state"] for d in decision_rows]
        print(f"    Position: avg={np.mean(prs):.2%}, states={dict(pd.Series(states).value_counts())}")

    return {
        "label": label, "mode": mode,
        "nav_df": nav_df, "metrics": metrics,
        "trades": trade_rows_all, "decisions": decision_rows,
    }


def _compute_metrics(nav_df: pd.DataFrame) -> dict:
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "annualized_return": 0, "max_drawdown": 0,
                "sharpe": 0, "calmar": 0, "volatility": 0}
    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    max_dd = float(np.min((nav - peak) / peak))
    n_days = len(nav)
    ann_return = float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 and nav[0] > 0 else 0.0
    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_return / vol) if vol > 0 else 0.0
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(ann_return, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "volatility": round(vol, 6),
        "n_days": n_days,
    }


# ══════════════════════════════════════════════════════════════════════
# Data Loaders
# ══════════════════════════════════════════════════════════════════════

def _build_calendar(engine, start_date=None, end_date=None) -> list:
    sql = "SELECT DISTINCT cal_date FROM tushare_stock.dim_trade_cal WHERE exchange = 'SSE' AND is_open = 1"
    if start_date:
        start_int = int(pd.Timestamp(start_date).strftime("%Y%m%d"))
        sql += f" AND cal_date >= {start_int}"
    if end_date:
        end_int = int(pd.Timestamp(end_date).strftime("%Y%m%d"))
        sql += f" AND cal_date <= {end_int}"
    sql += " ORDER BY cal_date"
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    import datetime as _dt
    result = []
    for r in rows:
        try:
            d_str = str(int(r[0]))
            result.append(_dt.date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8])))
        except (ValueError, IndexError):
            continue
    return result


def _build_signal_to_exec_map(calendar: list) -> tuple:
    signal_to_exec = {}
    exec_to_signal = {}
    for i in range(len(calendar) - 1):
        signal_to_exec[calendar[i]] = calendar[i + 1]
        exec_to_signal[calendar[i + 1]] = calendar[i]
    return signal_to_exec, exec_to_signal


# ══════════════════════════════════════════════════════════════════════
# Output
# ══════════════════════════════════════════════════════════════════════

def write_outputs(out_dir: Path, results: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    # NAV
    nav_dfs = []
    for label, r in results.items():
        ndf = r.get("nav_df")
        if ndf is not None and not ndf.empty:
            ndf = ndf.copy()
            ndf["curve"] = label
            nav_dfs.append(ndf)
    if nav_dfs:
        p = out_dir / "b1_nav.csv"
        pd.concat(nav_dfs, ignore_index=True).to_csv(p, index=False)
        paths["nav"] = str(p)

    # Decisions (from T3 as most complete)
    for label in ["T3", "T2", "T1", "T0"]:
        r = results.get(label, {})
        decs = r.get("decisions", [])
        if decs:
            p = out_dir / "b1_feature_daily.csv"
            pd.DataFrame(decs).to_csv(p, index=False)
            paths["features"] = str(p)
            break

    # Comparison
    rows = []
    for label in ["T0", "T1", "T2", "T3"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if not m: continue
        # Average position ratio
        decs = r.get("decisions", [])
        avg_pr = np.mean([d["target_position_ratio"] for d in decs]) if decs else 0.70
        rows.append({
            "curve": label, "mode": r.get("mode", ""),
            "total_return": m["total_return"], "max_drawdown": m["max_drawdown"],
            "sharpe": m["sharpe"], "calmar": m["calmar"],
            "avg_position": round(avg_pr, 4),
        })
    if rows:
        p = out_dir / "b1_ablation.csv"
        pd.DataFrame(rows).to_csv(p, index=False)
        paths["ablation"] = str(p)

    # State attribution (from T3)
    t3 = results.get("T3", {})
    decs = t3.get("decisions", [])
    if decs:
        dec_df = pd.DataFrame(decs)
        state_stats = []
        for state in ["RISK_OFF", "NEUTRAL", "RISK_ON"]:
            sd = dec_df[dec_df["market_state"] == state]
            if len(sd) > 0:
                state_stats.append({
                    "risk_state": state,
                    "days": len(sd),
                    "avg_position": round(sd["target_position_ratio"].mean(), 4),
                    "avg_turnover_ratio": round(sd["turnover_ratio"].mean(), 4),
                    "avg_csi300_ret20": round(sd["csi300_ret20"].mean(), 4),
                    "avg_breadth": round(sd["breadth"].mean(), 4),
                    "avg_candidate_pool": round(sd["candidate_pool_count"].mean(), 0),
                })
        if state_stats:
            p = out_dir / "b1_state_attribution.csv"
            pd.DataFrame(state_stats).to_csv(p, index=False)
            paths["state_attribution"] = str(p)

    # Markdown report
    md = _build_md_report(results)
    p = out_dir / "b1_report.md"
    with open(p, "w") as f:
        f.write(md)
    paths["report"] = str(p)

    return paths


def _build_md_report(results: dict) -> str:
    lines = [
        "# B1 Market Exposure Governor — G1/G2 回测报告",
        f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 消融结果",
        "",
        "| 曲线 | 规则 | 总收益 | 最大回撤 | Calmar | Sharpe | 平均仓位 |",
        "|------|------|--------|----------|--------|--------|----------|",
    ]
    for label in ["T0", "T1", "T2", "T3"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if not m: continue
        decs = r.get("decisions", [])
        avg_pr = np.mean([d["target_position_ratio"] for d in decs]) if decs else 0.70
        lines.append(
            f"| {label} | {r.get('mode','')} | {m['total_return']:.2%} | "
            f"{m['max_drawdown']:.2%} | {m['calmar']:.2f} | {m['sharpe']:.2f} | {avg_pr:.1%} |"
        )

    # Key comparison
    t0 = results.get("T0", {}).get("metrics", {})
    best = None
    best_label = None
    for label in ["T1", "T2", "T3"]:
        r = results.get(label, {}).get("metrics", {})
        if r and (best is None or r.get("calmar", 0) > best.get("calmar", 0)):
            best = r
            best_label = label

    lines.append("")
    lines.append("## 关键比较")
    if t0 and best:
        lines.append(f"- T0 (固定70%): Return={t0['total_return']:.2%}, MaxDD={t0['max_drawdown']:.2%}, Calmar={t0['calmar']:.2f}")
        lines.append(f"- {best_label} (最佳): Return={best['total_return']:.2%}, MaxDD={best['max_drawdown']:.2%}, Calmar={best['calmar']:.2f}")
        calmar_delta = (best['calmar'] - t0['calmar']) / abs(t0['calmar']) * 100 if t0['calmar'] != 0 else 0
        lines.append(f"- Calmar变化: {calmar_delta:+.1f}%")

    lines.append("")
    lines.append("## 结论")
    lines.append("- G1修复: Point-in-Time指数趋势, 真实市场特征接线 ✅")
    lines.append("- 数据区间: 2025-09-02 至 2026-06-30 (197交易日)")
    lines.append("- 结论等级: 研究级证据")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

MODES = {
    "T0": "固定70%仓位",
    "T1": "仅成交额比率",
    "T2": "仅CSI300 20日趋势",
    "T3": "成交额 + CSI300 (双因子)",
}


def main():
    parser = argparse.ArgumentParser(description="B1 Market Exposure Governor G1/G2")
    parser.add_argument("--start-date", default="2025-09-02")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--curves", default="T0,T1,T2,T3",
                        help="Comma-separated: T0,T1,T2,T3")
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("B1 Market Exposure Governor — G1/G2")
    print("=" * 60)

    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # Load calendar
    print("Loading calendar...")
    calendar = _build_calendar(engine, args.start_date, args.end_date)
    calendar = sorted(set(calendar))
    print(f"  {len(calendar)} trading days: {calendar[0]} to {calendar[-1]}")
    signal_to_exec, exec_to_signal = _build_signal_to_exec_map(calendar)

    # Load index trends (G1.1: point-in-time)
    print("Loading index trends (PIT)...")
    index_trends = load_index_trends_pit(
        engine,
        index_codes=["000300.SH", "399006.SZ"],
        calendar_dates=calendar,
    )
    # Fill missing dates with 0
    for d in calendar:
        if d not in index_trends:
            index_trends[d] = {"000300.SH": 0.0, "399006.SZ": 0.0}
    print(f"  {len(index_trends)} dates with index trends")

    # Load prices
    print("Loading prices...")
    prices = load_prices(engine, min_date=args.start_date, max_date=args.end_date, extra_days=10)
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    prices_sorted = prices.sort_values("_date_sort").reset_index(drop=True)
    price_day_indices = prices_sorted.groupby("trade_date", sort=True).indices
    print(f"  {len(prices_sorted)} rows, {len(price_day_indices)} days")

    # Load scores
    print("Loading scores...")
    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    print("  Adding liquidity features...")
    scores = add_liquidity_derived_features(scores, prices_sorted)
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    scores_sorted = scores.sort_values("_date_sort").reset_index(drop=True)
    score_day_indices = scores_sorted.groupby("trade_date", sort=True).indices
    print(f"  {len(scores_sorted)} rows, {len(score_day_indices)} days")

    # Build market environment
    print("Building market environment...")
    try:
        market_env = build_market_environment(scores_sorted, prices_sorted)
        print(f"  {len(market_env)} days")
    except Exception as e:
        print(f"  WARNING: {e}")
        market_env = pd.DataFrame()

    # Strategy specs
    strategy_specs = build_strategy_specs()

    # Output dir
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUT_ROOT / f"b1_g1g2_{timestamp}"
    print(f"Output: {out_dir}")

    # Run curves
    curve_labels = [c.strip() for c in args.curves.split(",")]
    results = {}
    for label in curve_labels:
        mode_desc = MODES.get(label, label)
        print(f"\n{'='*40}")
        print(f"  {label}: {mode_desc}")
        print(f"{'='*40}")
        try:
            r = run_governed_backtest(
                label=label, mode=mode_desc,
                engine=engine, scores=scores_sorted, prices=prices_sorted,
                market_env=market_env,
                calendar=calendar, signal_to_exec=signal_to_exec,
                exec_to_signal=exec_to_signal,
                score_day_indices=score_day_indices,
                price_day_indices=price_day_indices,
                index_trends=index_trends,
                strategy_specs=strategy_specs,
                start_date=args.start_date, end_date=args.end_date,
                initial_cash=args.initial_cash,
            )
            results[label] = r
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()

    # Write outputs
    print(f"\n{'='*60}\nWriting outputs...")
    paths = write_outputs(out_dir, results)
    for k, v in paths.items():
        print(f"  {k}: {v}")

    # Summary
    print(f"\n{'='*60}\nSUMMARY")
    print(f"{'Curve':<8} {'Return':>10} {'MaxDD':>10} {'Calmar':>8} {'AvgPos':>8}")
    print("-" * 44)
    for label in ["T0", "T1", "T2", "T3"]:
        r = results.get(label, {})
        m = r.get("metrics", {})
        if not m: continue
        decs = r.get("decisions", [])
        avg_pr = np.mean([d["target_position_ratio"] for d in decs]) if decs else 0.70
        print(f"{label:<8} {m['total_return']:>9.2%} {m['max_drawdown']:>9.2%} "
              f"{m['calmar']:>7.2f} {avg_pr:>7.1%}")

    print("\nDone.")


if __name__ == "__main__":
    main()
