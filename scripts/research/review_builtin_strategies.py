#!/usr/bin/env python3
"""Build a read-only, auditable review of every registered strategy card.

The script intentionally consumes immutable account-backtest exports.  It does
not connect to the broker, alter strategy cards, or silently treat missing
research/legacy implementations as backtestable strategies.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "strategy_cards"
EXPORT_ROOT = ROOT / "exports" / "signal_research"
DOC_ROOT = ROOT / "docs" / "03_backtest_reports"

# These are the latest saved runs that provide a coherent account ledger for
# each current strategy family.  A new database run can override them through
# --source strategy=/absolute/or/relative/run/directory.
DEFAULT_SOURCES = {
    "baseline_full_liquidity_detail_vol_position": "20260604_152142_206060_trusted_account_backtest",
    "adaptive_market_style": "20260605_004258_229723_trusted_account_backtest",
    "tiered_liquidity_then_bs_v2": "20260603_202728_444675_trusted_account_backtest",
    "ashare_auto_shadow": "20260604_163941_308980_trusted_account_backtest",
    "ashare_trend_breakout_shadow": "20260604_163941_308980_trusted_account_backtest",
    "ashare_hybrid_conservative_shadow": "20260604_163941_308980_trusted_account_backtest",
    "dual_system_adaptive_route": "20260604_163941_308980_trusted_account_backtest",
}

WINDOWS = (("full_history", None), ("1y", 252), ("6m", 126), ("3m", 63))
DEFAULT_MIN_WINDOW_COVERAGE = 0.90
DEFAULT_CAPITALS = (500_000, 1_000_000, 1_500_000, 3_000_000)
DEFAULT_COST_RATES = (0.00075, 0.0015, 0.0030, 0.0050)
DEFAULT_SLIPPAGE_BPS = (10, 25, 50, 100)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def load_csi300_from_db() -> pd.DataFrame:
    from sqlalchemy import create_engine, text
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scoreRank.core.db_config import build_sqlalchemy_url

    engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)
    return pd.read_sql(
        text("""SELECT trade_date, `close` FROM tushare_stock.dwd_index_daily
                WHERE ts_code = '000300.SH' ORDER BY trade_date"""),
        engine,
    ).assign(trade_date=lambda x: x.trade_date.astype(str))


def load_csi300_from_market_environment(path: Path) -> pd.DataFrame:
    frame = read_csv(path)
    required = {"trade_date", "market_hs300_pct_chg"}
    if not required.issubset(frame.columns):
        raise ValueError(f"market environment missing {sorted(required - set(frame.columns))}")
    returns = pd.to_numeric(frame.market_hs300_pct_chg, errors="coerce").fillna(0) / 100.0
    return pd.DataFrame({"trade_date": frame.trade_date.astype(str), "close": (1 + returns).cumprod()})


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_cards() -> pd.DataFrame:
    rows = []
    for path in sorted(CARDS.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows.append(
            {
                "strategy": raw.get("strategy_id"),
                "strategy_version": str(raw.get("strategy_version", "unknown")),
                "release_id": raw.get("release_id", ""),
                "status": raw.get("status", "RESEARCH"),
                "description": " ".join(str(raw.get("description", "")).split()),
                "holding_days": raw.get("holding_days", 10),
                "max_positions": raw.get("max_positions", 5),
                "signal_time": raw.get("signal_time", ""),
                "execution_time": raw.get("execution_time", ""),
                "candidate_pool": raw.get("candidate_pool", ""),
                "card_file": str(path.relative_to(ROOT)),
            }
        )
    return pd.DataFrame(rows)


def parse_sources(values: list[str]) -> dict[str, Path]:
    sources = {k: EXPORT_ROOT / v for k, v in DEFAULT_SOURCES.items()}
    for value in values:
        if "=" not in value:
            raise ValueError("--source must be strategy=directory")
        strategy, raw_path = value.split("=", 1)
        path = Path(raw_path)
        sources[strategy] = path if path.is_absolute() else ROOT / path
    return sources


def max_drawdown(nav: pd.Series) -> float:
    nav = pd.to_numeric(nav, errors="coerce").dropna()
    if nav.empty:
        return np.nan
    return float((nav / nav.cummax() - 1).min())


def _drawdown_recovery_days(nav: pd.Series) -> float:
    values = pd.to_numeric(nav, errors="coerce").dropna().reset_index(drop=True)
    if values.empty:
        return np.nan
    drawdown = values / values.cummax() - 1
    trough = int(drawdown.idxmin())
    peak_value = float(values.iloc[: trough + 1].max())
    recovered = values.iloc[trough + 1 :]
    hits = recovered[recovered >= peak_value]
    return float(int(hits.index[0]) - trough) if len(hits) else np.nan


def nav_metrics(nav: pd.DataFrame, label: str, days: int | None,
                min_coverage: float = DEFAULT_MIN_WINDOW_COVERAGE) -> dict:
    x = nav.sort_values("trade_date").copy()
    available_days = len(x)
    if days:
        x = x.tail(days)
    if x.empty:
        return {"window": label, "comparable": False}
    daily = pd.to_numeric(x["total_equity"], errors="coerce").pct_change().dropna()
    start = float(x["total_equity"].iloc[0])
    end = float(x["total_equity"].iloc[-1])
    total_ret = end / start - 1 if start else np.nan
    n = max(len(daily), 1)
    ann_ret = (1 + total_ret) ** (252 / n) - 1 if total_ret > -1 else -1.0
    vol = float(daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 else np.nan
    sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252)) if len(daily) > 1 and daily.std(ddof=1) else np.nan
    mdd = max_drawdown(pd.to_numeric(x["total_equity"], errors="coerce"))
    calmar = ann_ret / abs(mdd) if pd.notna(mdd) and mdd < 0 else np.nan
    coverage = min(1.0, available_days / days) if days else 1.0
    comparable = not days or coverage >= min_coverage
    downside = daily[daily < 0].std(ddof=1)
    sortino = float(daily.mean() / downside * math.sqrt(252)) if pd.notna(downside) and downside else np.nan
    var_95 = float(daily.quantile(0.05)) if len(daily) else np.nan
    cvar_95 = float(daily[daily <= var_95].mean()) if len(daily) else np.nan
    monthly = x.assign(month=pd.to_datetime(x["trade_date"]).dt.to_period("M")).groupby("month")["total_equity"].agg(["first", "last"])
    monthly_ret = monthly["last"] / monthly["first"] - 1 if len(monthly) else pd.Series(dtype=float)
    return {
        "window": label,
        "comparable": comparable,
        "coverage_ratio": coverage,
        "coverage_status": "FULL" if comparable else "INSUFFICIENT_SAMPLE",
        "window_start": str(x["trade_date"].iloc[0]),
        "window_end": str(x["trade_date"].iloc[-1]),
        "trading_days": len(x),
        "total_return": total_ret,
        "annualized_return": ann_ret if comparable else np.nan,
        "max_drawdown": mdd,
        "annualized_volatility": vol,
        "sharpe": sharpe if comparable else np.nan,
        "sortino": sortino if comparable else np.nan,
        "calmar": calmar if comparable else np.nan,
        "daily_win_rate": float((daily > 0).mean()) if len(daily) else np.nan,
        "monthly_win_rate": float((monthly_ret > 0).mean()) if len(monthly_ret) else np.nan,
        "var_95_daily": var_95,
        "cvar_95_daily": cvar_95,
        "worst_day": float(daily.min()) if len(daily) else np.nan,
        "worst_month": float(monthly_ret.min()) if len(monthly_ret) else np.nan,
        "drawdown_recovery_trading_days": _drawdown_recovery_days(x["total_equity"]),
    }


def benchmark_metrics(nav: pd.DataFrame, benchmark: pd.DataFrame, windows: tuple = WINDOWS) -> pd.DataFrame:
    columns = ["strategy", "window", "coverage_ratio", "benchmark", "strategy_return", "benchmark_return",
               "excess_return", "tracking_error", "information_ratio", "up_capture", "down_capture"]
    if nav.empty or benchmark.empty:
        return pd.DataFrame(columns=columns)
    bm = benchmark.copy()
    date_col = "trade_date" if "trade_date" in bm else "date"
    close_col = next((c for c in ("close", "adj_close", "total_equity") if c in bm), None)
    if close_col is None:
        raise ValueError("benchmark CSV requires close, adj_close, or total_equity")
    bm = bm.rename(columns={date_col: "trade_date", close_col: "benchmark_close"})
    bm["trade_date"] = bm.trade_date.astype(str)
    rows = []
    for strategy, group in nav.groupby("strategy"):
        merged = group[["trade_date", "total_equity"]].merge(bm[["trade_date", "benchmark_close"]], on="trade_date").sort_values("trade_date")
        for label, days in windows:
            x = merged.tail(days) if days else merged
            sr = pd.to_numeric(x.total_equity, errors="coerce").pct_change().dropna()
            br = pd.to_numeric(x.benchmark_close, errors="coerce").pct_change().dropna()
            aligned = pd.concat([sr.rename("strategy"), br.rename("benchmark")], axis=1).dropna()
            if len(aligned) < 2:
                continue
            active = aligned.strategy - aligned.benchmark
            te = active.std(ddof=1) * math.sqrt(252)
            up = aligned[aligned.benchmark > 0]
            down = aligned[aligned.benchmark < 0]
            coverage = min(1.0, len(x) / days) if days else 1.0
            rows.append({
                "strategy": strategy, "window": label, "coverage_ratio": coverage,
                "benchmark": "CSI300", "strategy_return": float((1 + aligned.strategy).prod() - 1),
                "benchmark_return": float((1 + aligned.benchmark).prod() - 1),
                "excess_return": float((1 + aligned.strategy).prod() / (1 + aligned.benchmark).prod() - 1),
                "tracking_error": float(te),
                "information_ratio": float(active.mean() * 252 / te) if te else np.nan,
                "up_capture": float(up.strategy.mean() / up.benchmark.mean()) if len(up) and up.benchmark.mean() else np.nan,
                "down_capture": float(down.strategy.mean() / down.benchmark.mean()) if len(down) and down.benchmark.mean() else np.nan,
            })
    return pd.DataFrame(rows, columns=columns)


def stress_matrix(summary: pd.DataFrame, candidates: pd.DataFrame, capitals=DEFAULT_CAPITALS,
                  cost_rates=DEFAULT_COST_RATES, slippage_bps=DEFAULT_SLIPPAGE_BPS) -> pd.DataFrame:
    """Conservative first-order capacity/cost stress using saved ledger proxies."""
    rows = []
    proxy_cols = {"estimated_turnover_impact", "unfilled_ratio_proxy", "adjusted_target_weight"}
    for _, s in summary[summary.evaluation_status.eq("evaluated")].iterrows():
        group = candidates[candidates.strategy.eq(s.strategy)] if not candidates.empty else pd.DataFrame()
        impact = pd.to_numeric(group.get("estimated_turnover_impact", pd.Series(dtype=float)), errors="coerce")
        unfilled = pd.to_numeric(group.get("unfilled_ratio_proxy", pd.Series(dtype=float)), errors="coerce")
        has_activity = int(s.get("trade_count", 0) or 0) > 0 and not group.empty
        proxy_available = has_activity and proxy_cols.intersection(group.columns) == proxy_cols
        base_turnover = float(s.get("turnover", 0) or 0)
        initial_cash = float(s.get("initial_cash", 500_000) or 500_000)
        for capital in capitals:
            scale = capital / initial_cash
            scaled_impact = impact * scale if len(impact) else impact
            for cost in cost_rates:
                for slip_bps in slippage_bps:
                    # The refreshed production ledger already includes 7.5bp cost and 10bp slippage.
                    incremental_drag = base_turnover * max(0.0, cost - 0.00075) + base_turnover * max(0, slip_bps - 10) / 10_000
                    rows.append({
                        "strategy": s.strategy, "capital": capital, "cost_rate": cost,
                        "slippage_bps": slip_bps, "estimated_return_after_incremental_friction": float(s.total_return) - incremental_drag,
                        "proxy_available": proxy_available,
                        "p95_adv_or_turnover_impact": float(scaled_impact.quantile(.95)) if len(scaled_impact.dropna()) else np.nan,
                        "max_adv_or_turnover_impact": float(scaled_impact.max()) if len(scaled_impact.dropna()) else np.nan,
                        "unfilled_ratio": float((unfilled > 0).mean()) if len(unfilled.dropna()) else np.nan,
                        "capacity_status": "INSUFFICIENT_TRADES" if not has_activity else ("UNVERIFIED" if not proxy_available else ("FAIL" if (scaled_impact > .03).any() or (unfilled > 0).mean() > .08 else "PASS")),
                    })
    return pd.DataFrame(rows)


def pair_round_trips(trades: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """FIFO-pair buys and sells, allocating entry/exit cost per share."""
    lots: dict[tuple[str, str], deque] = defaultdict(deque)
    closed = []
    unmatched_sells = 0
    for _, row in trades.sort_values(["trade_date", "side"]).iterrows():
        key = (str(row["strategy"]), str(row["symbol"]).zfill(6))
        shares = int(float(row.get("shares", 0) or 0))
        if shares <= 0:
            continue
        price = float(row.get("price", 0) or 0)
        cost_per_share = float(row.get("cost", 0) or 0) / shares
        if str(row["side"]).upper() == "BUY":
            lots[key].append(
                {
                    "shares": shares,
                    "entry_date": str(row["trade_date"]),
                    "entry_price": price,
                    "entry_cost_ps": cost_per_share,
                    "name": row.get("name", ""),
                    "industry": row.get("industry", ""),
                }
            )
            continue
        remaining = shares
        while remaining > 0 and lots[key]:
            lot = lots[key][0]
            qty = min(remaining, lot["shares"])
            gross_pnl = (price - lot["entry_price"]) * qty
            cost = (lot["entry_cost_ps"] + cost_per_share) * qty
            net_pnl = gross_pnl - cost
            invested = lot["entry_price"] * qty
            closed.append(
                {
                    "strategy": key[0], "symbol": key[1], "name": lot["name"],
                    "industry": lot["industry"], "entry_date": lot["entry_date"],
                    "exit_date": str(row["trade_date"]), "shares": qty,
                    "entry_price": lot["entry_price"], "exit_price": price,
                    "gross_pnl": gross_pnl, "cost": cost, "net_pnl": net_pnl,
                    "gross_return": gross_pnl / invested if invested else np.nan,
                    "net_return": net_pnl / invested if invested else np.nan,
                    "exit_reason": row.get("reason", ""),
                }
            )
            lot["shares"] -= qty
            remaining -= qty
            if lot["shares"] == 0:
                lots[key].popleft()
        if remaining:
            unmatched_sells += 1
    open_lots = sum(len(v) for v in lots.values())
    return pd.DataFrame(closed), open_lots + unmatched_sells


def consecutive_losses(values: pd.Series) -> int:
    longest = current = 0
    for value in values.fillna(0):
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def completed_trade_metrics(round_trips: pd.DataFrame) -> dict:
    if round_trips.empty:
        return {"completed_round_trips": 0}
    wins = round_trips[round_trips.net_pnl > 0]
    losses = round_trips[round_trips.net_pnl < 0]
    avg_win = wins.net_return.mean() if len(wins) else np.nan
    avg_loss = abs(losses.net_return.mean()) if len(losses) else np.nan
    entry = pd.to_datetime(round_trips.entry_date)
    exit_ = pd.to_datetime(round_trips.exit_date)
    return {
        "completed_round_trips": len(round_trips),
        "trade_win_rate": float((round_trips.net_pnl > 0).mean()),
        "profit_loss_ratio": float(avg_win / avg_loss) if pd.notna(avg_win) and pd.notna(avg_loss) and avg_loss else np.nan,
        "max_consecutive_losses": consecutive_losses(round_trips.sort_values("exit_date").net_pnl),
        "avg_holding_calendar_days": float((exit_ - entry).dt.days.mean()),
        "gross_pnl": float(round_trips.gross_pnl.sum()),
        "net_pnl": float(round_trips.net_pnl.sum()),
        "round_trip_cost": float(round_trips.cost.sum()),
    }


def audit_samples(round_trips: pd.DataFrame) -> pd.DataFrame:
    """Select up to five high-information closed trades per strategy."""
    rows = []
    if round_trips.empty:
        return pd.DataFrame()
    for strategy, group in round_trips.groupby("strategy"):
        seed = pd.concat(
            [group.nlargest(2, "net_pnl"), group.nsmallest(2, "net_pnl"), group.sort_values("exit_date").tail(1)],
            ignore_index=True,
        ).drop_duplicates(["symbol", "entry_date", "exit_date", "shares"])
        remainder = group.merge(
            seed[["symbol", "entry_date", "exit_date", "shares"]],
            on=["symbol", "entry_date", "exit_date", "shares"], how="left", indicator=True,
        ).query("_merge == 'left_only'").drop(columns="_merge")
        ordered = pd.concat([seed, remainder], ignore_index=True).head(5)
        rows.append(ordered)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def recommendation_detail(candidates: pd.DataFrame, trades: pd.DataFrame, round_trips: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()
    out = candidates.copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    buy = trades[trades.side.astype(str).str.upper().eq("BUY")].copy()
    if not buy.empty:
        buy["symbol"] = buy.symbol.astype(str).str.zfill(6)
        if "reject_reason" not in buy:
            buy["reject_reason"] = ""
        buy = buy[["strategy", "trade_date", "symbol", "price", "shares", "cost", "reject_reason"]].rename(
            columns={"trade_date": "execution_date", "price": "filled_price", "shares": "filled_shares", "cost": "entry_cost", "reject_reason": "trade_reject_reason"}
        )
        out = out.merge(buy, on=["strategy", "execution_date", "symbol"], how="left")
    if not round_trips.empty:
        rt = round_trips.sort_values("exit_date").drop_duplicates(["strategy", "symbol", "entry_date"], keep="last")
        rt = rt[["strategy", "symbol", "entry_date", "exit_date", "exit_price", "gross_return", "net_return", "net_pnl", "cost", "exit_reason"]].rename(columns={"entry_date": "execution_date", "cost": "round_trip_cost"})
        out = out.merge(rt, on=["strategy", "symbol", "execution_date"], how="left")
    keep = [
        "strategy", "signal_date", "execution_date", "rank", "symbol", "name", "industry",
        "rank_score", "planned_shares", "planned_price", "filled_shares", "filled_price",
        "exit_date", "exit_price", "gross_return", "net_return", "net_pnl", "entry_cost",
        "round_trip_cost", "plan_reject_reason", "trade_reject_reason", "execution_tradable",
        "locked", "skipped_by_position_cap", "risk_decision", "risk_governor_reasons",
        "large_slippage_proxy", "limit_up_buy_ratio", "unfilled_ratio_proxy",
        "limit_down_sell_ratio", "open_gap_proxy", "estimated_turnover_impact",
        "execution_mode", "causality_pass", "exit_reason",
    ]
    for column in keep:
        if column not in out:
            out[column] = np.nan
    return out[keep]


def stock_summary(candidates: pd.DataFrame, round_trips: pd.DataFrame) -> pd.DataFrame:
    base = pd.DataFrame()
    if not candidates.empty:
        base = candidates.groupby(["strategy", "symbol"], as_index=False).agg(
            name=("name", "last"), industry=("industry", "last"),
            recommendation_count=("signal_date", "count"), first_recommendation=("signal_date", "min"),
            last_recommendation=("signal_date", "max"), average_rank=("rank", "mean"),
        )
    if round_trips.empty:
        return base
    perf = round_trips.groupby(["strategy", "symbol"], as_index=False).agg(
        completed_trade_count=("net_return", "count"), cumulative_net_pnl=("net_pnl", "sum"),
        average_net_return=("net_return", "mean"), win_rate=("net_pnl", lambda x: float((x > 0).mean())),
        best_trade_return=("net_return", "max"), worst_trade_return=("net_return", "min"),
        max_loss_amount=("net_pnl", "min"), total_cost=("cost", "sum"),
    )
    if base.empty:
        return perf
    return base.merge(perf, on=["strategy", "symbol"], how="outer")


def risk_exposure(candidates: pd.DataFrame, round_trips: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy, group in candidates.groupby("strategy") if not candidates.empty else []:
        counts = group.groupby("industry").size().sort_values(ascending=False)
        symbols = group.groupby("symbol").size().sort_values(ascending=False)
        rt = round_trips[round_trips.strategy.eq(strategy)] if not round_trips.empty else pd.DataFrame()
        total_positive = rt.loc[rt.net_pnl > 0, "net_pnl"].sum() if not rt.empty else np.nan
        top_symbol_pnl = rt.groupby("symbol").net_pnl.sum().max() if not rt.empty else np.nan
        rows.append({
            "strategy": strategy, "recommendations": len(group),
            "unique_stocks": group.symbol.nunique(), "unique_industries": group.industry.nunique(),
            "top_industry": counts.index[0] if len(counts) else "",
            "top_industry_share": float(counts.iloc[0] / len(group)) if len(counts) else np.nan,
            "top_stock": symbols.index[0] if len(symbols) else "",
            "top_stock_share": float(symbols.iloc[0] / len(group)) if len(symbols) else np.nan,
            "largest_winner_share_of_positive_pnl": float(top_symbol_pnl / total_positive) if pd.notna(top_symbol_pnl) and total_positive > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def fmt_pct(value) -> str:
    return "—" if pd.isna(value) else f"{float(value):+.2%}"


def int0(value) -> int:
    return 0 if pd.isna(value) else int(value)


def num0(value) -> float:
    return 0.0 if pd.isna(value) else float(value)


def risk_level(row: pd.Series) -> str:
    if row.get("evaluation_status") != "evaluated":
        return "不可评估"
    if int0(row.get("completed_round_trips")) < 5:
        return "样本不足"
    mdd = row.get("max_drawdown", np.nan)
    if pd.isna(mdd) or mdd <= -0.45:
        return "极高"
    if mdd <= -0.25:
        return "高"
    if mdd <= -0.15:
        return "中高"
    return "中"


def recommendation(row: pd.Series) -> str:
    status = row.get("status")
    if row.get("evaluation_status") != "evaluated":
        return "淘汰/遗留" if status == "LEGACY" else "仅研究"
    if status == "PRODUCTION":
        return "保留生产" if row.get("max_drawdown", -1) > -0.45 else "生产降级复核"
    if status == "SHADOW":
        return "继续影子" if row.get("total_return", -1) > 0 else "仅研究"
    return "仅研究"


def write_report(path: Path, cards: pd.DataFrame, summary: pd.DataFrame, windows: pd.DataFrame,
                 stocks: pd.DataFrame, quality: pd.DataFrame, risk: pd.DataFrame, output_dir: Path,
                 commit: str, title: str = "系统内置策略全面评估与逐股复盘",
                 benchmark_available: bool = False) -> None:
    evaluated = summary[summary.evaluation_status.eq("evaluated")].sort_values("total_return", ascending=False)
    unavailable = summary[~summary.evaluation_status.eq("evaluated")]
    top = evaluated.iloc[0] if len(evaluated) else None
    lines = [
        f"# {title}",
        "",
        "## Executive Summary",
        "",
    ]
    if top is not None:
        lines += [
            f"- **全历史收益最高的是 `{top.strategy}`：{fmt_pct(top.total_return)}，最大回撤 {fmt_pct(top.max_drawdown)}。** 各策略证据窗口并不完全一致，排名用于识别风险收益特征，不等同于同窗冠军赛。",
            f"- **共审查 {len(cards)} 张策略卡，其中 {len(evaluated)} 个有可复核账户账本，{len(unavailable)} 个不可验证。** 不可验证项不会获得模拟收益或交易建议。",
            "- **长期攻击策略存在明显尾部风险。** 三年窗口的 `tiered_liquidity_then_bs_v2` 回撤极深；短期外部影子策略样本只有约三个月，不能年化外推。",
            "- **当前证据晚于部分代码、早于 2026-06-23 策略卡版本。** 本报告是已保存账本复盘，不是当前版本截至今日的重新验收。",
        ]
    lines += [
        "",
        "## 风险收益总览",
        "",
        "| 策略 | 状态 | 证据期 | 总收益 | 年化 | 最大回撤 | 夏普 | 成交闭环 | 风险 | 建议 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for _, row in summary.sort_values(["evaluation_status", "total_return"], ascending=[True, False]).iterrows():
        period = "—" if row.evaluation_status != "evaluated" else f"{row.first_date}～{row.last_date}"
        lines.append(
            f"| `{row.strategy}` | {row.status} | {period} | {fmt_pct(row.total_return)} | {fmt_pct(row.annualized_return)} | {fmt_pct(row.max_drawdown)} | "
            f"{'—' if pd.isna(row.sharpe) else f'{row.sharpe:.2f}'} | {int0(row.completed_round_trips)} | {row.risk_level} | {row.recommendation} |"
        )
    lines += [
        "",
        "**含义：** 生产策略只在现有证据仍可接受时保留；影子策略即使短期盈利，也必须继续积累同版本、同窗口的前向证据。不可验证策略保持研究或遗留状态。",
        "",
        "## 分窗口表现揭示明显的行情依赖",
        "",
        "| 策略 | 窗口 | 覆盖 | 起止 | 收益 | 最大回撤 | 波动率 | 夏普 |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for _, row in windows.iterrows():
        lines.append(
            f"| `{row.strategy}` | {row.window} | {row.coverage_status} ({row.coverage_ratio:.1%}) | {row.window_start}～{row.window_end} | {fmt_pct(row.total_return)} | {fmt_pct(row.max_drawdown)} | {fmt_pct(row.annualized_volatility)} | "
            f"{'—' if pd.isna(row.sharpe) else f'{row.sharpe:.2f}'} |"
        )
    lines += [
        "",
        "**含义：** 近三个月只有在策略拥有足够交易日时才展示。外部影子策略的“全历史”本身只有约三个月，不能与三年核心策略直接比较年化值。",
        "",
        "## 每个策略具体赚在什么股票、亏在什么股票",
        "",
    ]
    for _, srow in summary.iterrows():
        lines += [f"### `{srow.strategy}` — {srow.recommendation}", ""]
        if srow.evaluation_status != "evaluated":
            lines += [f"不可验证：{srow.evaluation_note}", ""]
            continue
        ss = stocks[stocks.strategy.eq(srow.strategy)].copy()
        winners = ss.sort_values("cumulative_net_pnl", ascending=False).head(5)
        losers = ss.sort_values("cumulative_net_pnl", ascending=True).head(5)
        strategy_risk = risk[risk.strategy.eq(srow.strategy)]
        if len(strategy_risk):
            rr = strategy_risk.iloc[0]
            lines += [
                f"推荐 {int(rr.recommendations)} 次，覆盖 {int(rr.unique_stocks)} 只股票、{int(rr.unique_industries)} 个行业；最高频行业为 {rr.top_industry}（{rr.top_industry_share:.1%}），最高频股票为 `{rr.top_stock}`（{rr.top_stock_share:.1%}）。",
                "",
            ]
        lines += ["| 贡献最高股票 | 名称 | 推荐次数 | 完成交易 | 累计净损益 | 胜率 | 平均净收益 |", "|---|---|---:|---:|---:|---:|---:|"]
        for _, row in winners.iterrows():
            lines.append(f"| `{row.symbol}` | {row.get('name','')} | {int0(row.get('recommendation_count'))} | {int0(row.get('completed_trade_count'))} | {num0(row.get('cumulative_net_pnl')):,.0f} | {fmt_pct(row.get('win_rate'))} | {fmt_pct(row.get('average_net_return'))} |")
        lines += ["", "| 拖累最大股票 | 名称 | 推荐次数 | 完成交易 | 累计净损益 | 胜率 | 最差单笔 |", "|---|---|---:|---:|---:|---:|---:|"]
        for _, row in losers.iterrows():
            lines.append(f"| `{row.symbol}` | {row.get('name','')} | {int0(row.get('recommendation_count'))} | {int0(row.get('completed_trade_count'))} | {num0(row.get('cumulative_net_pnl')):,.0f} | {fmt_pct(row.get('win_rate'))} | {fmt_pct(row.get('worst_trade_return'))} |")
        lines.append("")
    lines += [
        "## 建议动作",
        "",
        "1. 保留生产策略但重新跑 2026-06-23 策略卡版本的同窗验收；在重跑完成前，不把本报告视为当前版本放行证据。",
        "2. AShare 与双系统策略继续影子观察，至少补足 6～12 个月及完整卖出闭环后再讨论晋级。",
        "3. `tiered_liquidity_then_bs_v2` 仅允许强势市场攻击预算，禁止用近一年强收益覆盖三年尾部回撤证据。",
        "4. `repair_reversal_shadow` 保持研究占位；`chenyiyun_selected` 保持遗留只读，不恢复订单能力。",
        "",
        "## 仍需回答的问题",
        "",
        "- 2026-06-23 策略卡版本在统一 2023 年起始窗口下是否仍保持相同风险排序？",
        "- 外部影子策略在完整卖出周期、不同市场状态下是否仍有正收益？",
        "- 本地指数数据覆盖不足以形成统一基准时，内部防守基线的超额收益是否稳定？",
        "",
        "## 口径、限制与审计结论",
        "",
        f"- 主回测初始资金 {num0(evaluated.initial_cash.iloc[0]) / 10000:.0f} 万元，Top 5，持有 10 日，单边成本 0.075%，信号 T 日、执行 T+1；实际参数以每个源账本为准并保存在 `strategy_summary.csv`。" if len(evaluated) else "- 账户参数不可验证。",
        "- 各源运行日期不同；全历史是各策略自身可得全历史，不是严格共同交集。跨策略结论因此以风险画像为主，不做显著性声明。",
        "- 动态策略的已保存决策字段使用 `exit_date < signal_date`；交易因果性和禁用模型字段检查结果见 `data_quality_checks.csv`。",
        "- 沪深300基准已由本次冻结市场环境中的逐日收益重建；超额收益、信息比率和上下行捕获见 `benchmark_metrics.csv`。" if benchmark_available else "- 沪深300基准不可用，因此本次不计算指数超额收益。",
        f"- Git commit：`{commit}`；原始复盘目录：`{output_dir.relative_to(ROOT)}`。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review all registered built-in strategy cards from saved trusted ledgers.")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--source", action="append", default=[], help="Override source as strategy=run_directory")
    parser.add_argument("--benchmark-csv", default=None, help="CSI300 daily CSV with trade_date and close")
    parser.add_argument("--benchmark-from-db", action="store_true", help="Load CSI300 from configured read-only database")
    parser.add_argument("--benchmark-market-environment", default=None, help="Use frozen market environment HS300 daily returns")
    parser.add_argument("--min-window-coverage", type=float, default=DEFAULT_MIN_WINDOW_COVERAGE)
    parser.add_argument("--primary-capital", type=float, default=1_000_000)
    parser.add_argument("--expected-data-date", default=None, help="Fail closed when evidence ends before this date")
    parser.add_argument("--production-allocation-review", action="store_true", help="Use production allocation artifact names and fail-closed appendix")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "production_all_strategy_review" if args.production_allocation_review else "builtin_strategy_full_review"
    output_dir = Path(args.output_dir) if args.output_dir else EXPORT_ROOT / f"{stamp}_{suffix}"
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    DOC_ROOT.mkdir(parents=True, exist_ok=True)

    cards = load_cards()
    sources = parse_sources(args.source)
    all_candidates, all_trades, all_positions, all_nav, all_round_trips = [], [], [], [], []
    summary_rows, window_rows, quality_rows = [], [], []

    for _, card in cards.iterrows():
        strategy = card.strategy
        source = sources.get(strategy)
        base = card.to_dict()
        if source is None or not source.exists():
            summary_rows.append({**base, "evaluation_status": "unavailable", "evaluation_note": "无兼容的可信账户级回测实现或已保存账本", "source_run": ""})
            quality_rows.append({"strategy": strategy, "check": "backtest_ledger_available", "status": "FAIL", "details": "No compatible saved account ledger"})
            continue
        summary_file = read_csv(source / "trusted_account_backtest_summary.csv")
        nav = read_csv(source / "trusted_account_backtest_nav.csv")
        candidates = read_csv(source / "trusted_account_backtest_candidates.csv")
        trades = read_csv(source / "trusted_account_backtest_trades.csv")
        positions = read_csv(source / "trusted_account_backtest_positions.csv")
        for frame in (summary_file, nav, candidates, trades, positions):
            if not frame.empty and "strategy" in frame:
                frame.drop(frame.index[~frame.strategy.astype(str).eq(strategy)], inplace=True)
        if summary_file.empty or nav.empty:
            summary_rows.append({**base, "evaluation_status": "unavailable", "evaluation_note": "源目录存在但不含该策略的完整汇总/净值", "source_run": str(source.relative_to(ROOT))})
            quality_rows.append({"strategy": strategy, "check": "backtest_ledger_available", "status": "FAIL", "details": str(source)})
            continue
        for frame in (nav, candidates, trades, positions):
            if not frame.empty:
                if "symbol" in frame:
                    frame["symbol"] = frame.symbol.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
                all_candidates.append(frame.copy()) if frame is candidates else None
                all_trades.append(frame.copy()) if frame is trades else None
                all_positions.append(frame.copy()) if frame is positions else None
                all_nav.append(frame.copy()) if frame is nav else None
        round_trips, open_or_unmatched = pair_round_trips(trades)
        if not round_trips.empty:
            all_round_trips.append(round_trips)
        trade_metrics = completed_trade_metrics(round_trips)
        full_metric = nav_metrics(nav, "full_history", None)
        raw = summary_file.iloc[0].to_dict()
        summary_rows.append({
            **base, **raw, **trade_metrics, "evaluation_status": "evaluated",
            "evaluation_note": "已保存可信账户账本；策略卡版本可能晚于证据运行日期",
            "source_run": str(source.relative_to(ROOT)), "sharpe": full_metric.get("sharpe"),
            "calmar": full_metric.get("calmar"), "annualized_volatility": full_metric.get("annualized_volatility"),
            "open_or_unmatched_lots": open_or_unmatched,
        })
        for label, days in WINDOWS:
            metric = nav_metrics(nav, label, days, args.min_window_coverage)
            if int(trade_metrics.get("completed_round_trips", 0)) < 5:
                metric.update({"comparable": False, "coverage_status": "INSUFFICIENT_TRADES", "annualized_return": np.nan, "sharpe": np.nan, "sortino": np.nan, "calmar": np.nan})
            if label == "full_history":
                metric.update({
                    "window_start": raw.get("first_date"), "window_end": raw.get("last_date"),
                    "trading_days": raw.get("trading_days"), "total_return": raw.get("total_return"),
                    "annualized_return": raw.get("annualized_return"), "max_drawdown": raw.get("max_drawdown"),
                    "daily_win_rate": raw.get("daily_win_rate"),
                })
            window_rows.append({"strategy": strategy, **metric})

        signal_ok = True
        if not candidates.empty and {"signal_date", "execution_date"}.issubset(candidates):
            signal_ok = bool((pd.to_datetime(candidates.signal_date) < pd.to_datetime(candidates.execution_date)).all())
        quality_rows.append({"strategy": strategy, "check": "signal_before_execution", "status": "PASS" if signal_ok else "FAIL", "details": "signal_date < execution_date"})
        causality = candidates.get("causality_pass", pd.Series(dtype=object)).dropna() if not candidates.empty else pd.Series(dtype=object)
        causality_ok = not causality.empty and causality.astype(str).str.lower().isin(["1", "1.0", "true"]).all()
        quality_rows.append({"strategy": strategy, "check": "causality_pass", "status": "PASS" if causality_ok else ("WARN" if causality.empty else "FAIL"), "details": f"audited_rows={len(causality)}"})
        future_cols = [c for c in candidates.columns if c.startswith("bs_model_")] if not candidates.empty else []
        quality_rows.append({"strategy": strategy, "check": "forbidden_backfilled_fields", "status": "PASS" if not future_cols else "FAIL", "details": ",".join(future_cols) or "none"})
        dup = int(candidates.duplicated(["strategy", "signal_date", "symbol"]).sum()) if not candidates.empty else 0
        quality_rows.append({"strategy": strategy, "check": "duplicate_recommendations", "status": "PASS" if dup == 0 else "WARN", "details": str(dup)})
        missing_prices = int(pd.to_numeric(trades.get("price", pd.Series(dtype=float)), errors="coerce").isna().sum()) if not trades.empty else 0
        quality_rows.append({"strategy": strategy, "check": "trade_prices_present", "status": "PASS" if missing_prices == 0 else "FAIL", "details": str(missing_prices)})
        dynamic = read_csv(source / "trusted_account_backtest_adaptive_decisions.csv")
        if not dynamic.empty and "strategy" in dynamic:
            dynamic = dynamic[dynamic.strategy.astype(str).eq(strategy)]
        completed_rule = set(dynamic.get("completed_history_rule", pd.Series(dtype=str)).dropna().astype(str))
        dynamic_ok = not completed_rule or completed_rule == {"exit_date < signal_date"}
        quality_rows.append({"strategy": strategy, "check": "dynamic_history_completed_before_signal", "status": "PASS" if dynamic_ok else "FAIL", "details": ";".join(sorted(completed_rule)) or "not_applicable"})
        final_nav = float(pd.to_numeric(nav.total_equity, errors="coerce").iloc[-1])
        final_summary = float(raw.get("final_equity"))
        equity_diff = abs(final_nav - final_summary)
        quality_rows.append({"strategy": strategy, "check": "final_equity_reconciliation", "status": "PASS" if equity_diff < 0.01 else "FAIL", "details": f"difference={equity_diff:.6f}"})
        trade_cost = float(pd.to_numeric(trades.get("cost", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        summary_cost = float(raw.get("total_cost", 0) or 0)
        cost_diff = abs(trade_cost - summary_cost)
        quality_rows.append({"strategy": strategy, "check": "trade_cost_reconciliation", "status": "PASS" if cost_diff < 0.01 else "FAIL", "details": f"difference={cost_diff:.6f}"})
        quality_rows.append({"strategy": strategy, "check": "card_version_matches_saved_run", "status": "WARN", "details": f"card_version={card.strategy_version}; source={source.name}"})
        report_path = source / "trusted_account_backtest_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        provenance = report.get("provenance") or report.get("params") or {}
        ledger_status = provenance.get("ledger_implementation_status", "MISSING")
        ca_status = provenance.get("corporate_action_coverage_status", "MISSING")
        lifecycle_hash = provenance.get("security_lifecycle_snapshot_sha256")
        reproducibility = provenance.get("reproducibility_status", "MISSING")
        quality_rows.extend([
            {"strategy": strategy, "check": "strict_ledger_verified", "status": "PASS" if ledger_status == "VERIFIED" else "FAIL", "details": str(ledger_status)},
            {"strategy": strategy, "check": "corporate_action_coverage", "status": "PASS" if ca_status in {"VERIFIED", "COMPLETE"} else "FAIL", "details": str(ca_status)},
            {"strategy": strategy, "check": "security_lifecycle_snapshot", "status": "PASS" if lifecycle_hash else "FAIL", "details": str(lifecycle_hash or "missing")},
            {"strategy": strategy, "check": "deterministic_reproducibility", "status": "PASS" if reproducibility == "REPRODUCIBLE" else "FAIL", "details": str(reproducibility)},
        ])
        if args.expected_data_date:
            current = pd.Timestamp(raw.get("last_date")) >= pd.Timestamp(args.expected_data_date)
            quality_rows.append({"strategy": strategy, "check": "data_cutoff_current", "status": "PASS" if current else "FAIL", "details": f"last_date={raw.get('last_date')}; expected>={args.expected_data_date}"})

    candidates = pd.concat(all_candidates, ignore_index=True, sort=False) if all_candidates else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True, sort=False) if all_trades else pd.DataFrame()
    positions = pd.concat(all_positions, ignore_index=True, sort=False) if all_positions else pd.DataFrame()
    nav = pd.concat(all_nav, ignore_index=True, sort=False) if all_nav else pd.DataFrame()
    round_trips = pd.concat(all_round_trips, ignore_index=True, sort=False) if all_round_trips else pd.DataFrame()
    samples = audit_samples(round_trips)
    evaluated_names = [row["strategy"] for row in summary_rows if row.get("evaluation_status") == "evaluated"]
    for strategy in evaluated_names:
        available = int((round_trips.strategy.eq(strategy)).sum()) if not round_trips.empty else 0
        sampled = int((samples.strategy.eq(strategy)).sum()) if not samples.empty else 0
        quality_rows.append({
            "strategy": strategy, "check": "manual_trade_sample_coverage",
            "status": "PASS" if sampled >= 5 else "WARN",
            "details": f"sampled={sampled}; completed_round_trips={available}; target=5",
        })
    quality = pd.DataFrame(quality_rows)
    summary = pd.DataFrame(summary_rows)
    windows = pd.DataFrame(window_rows)
    stock = stock_summary(candidates, round_trips)
    detail = recommendation_detail(candidates, trades, round_trips)
    risk = risk_exposure(candidates, round_trips)
    benchmark_sources = sum(bool(x) for x in (args.benchmark_csv, args.benchmark_from_db, args.benchmark_market_environment))
    if benchmark_sources > 1:
        raise ValueError("choose only one benchmark source")
    if args.benchmark_from_db:
        benchmark = load_csi300_from_db()
    elif args.benchmark_market_environment:
        benchmark = load_csi300_from_market_environment(Path(args.benchmark_market_environment))
    else:
        benchmark = read_csv(Path(args.benchmark_csv)) if args.benchmark_csv else pd.DataFrame()
    benchmark_result = benchmark_metrics(nav, benchmark)
    stress = stress_matrix(summary, candidates)

    for column in ("total_return", "annualized_return", "max_drawdown", "sharpe", "completed_round_trips"):
        if column not in summary:
            summary[column] = np.nan
    summary["risk_level"] = summary.apply(risk_level, axis=1)
    summary["recommendation"] = summary.apply(recommendation, axis=1)
    failed_strategies = set(quality.loc[quality.status.eq("FAIL"), "strategy"].astype(str))
    summary.loc[summary.strategy.astype(str).isin(failed_strategies) & summary.status.eq("PRODUCTION"), "recommendation"] = "不批准生产扩仓"
    baseline = summary.loc[summary.strategy.eq("baseline_full_liquidity_detail_vol_position"), "total_return"]
    summary["excess_vs_internal_defensive_baseline"] = summary.total_return - (baseline.iloc[0] if len(baseline) else np.nan)

    summary.to_csv(output_dir / "strategy_summary.csv", index=False)
    windows.to_csv(output_dir / "window_metrics.csv", index=False)
    stock.to_csv(output_dir / "stock_summary.csv", index=False)
    detail.to_csv(output_dir / "recommendation_trade_detail.csv", index=False)
    trades.to_csv(output_dir / "trades.csv", index=False)
    positions.to_csv(output_dir / "positions.csv", index=False)
    nav.to_csv(output_dir / "nav.csv", index=False)
    round_trips.to_csv(output_dir / "round_trips.csv", index=False)
    samples.to_csv(output_dir / "trade_audit_samples.csv", index=False)
    risk.to_csv(output_dir / "risk_exposure.csv", index=False)
    quality.to_csv(output_dir / "data_quality_checks.csv", index=False)
    benchmark_result.to_csv(output_dir / "benchmark_metrics.csv", index=False)
    stress.to_csv(output_dir / "stress_capacity_matrix.csv", index=False)
    hard_fail = bool((quality.status == "FAIL").any())
    production_names = set(summary.loc[summary.status.eq("PRODUCTION") & summary.evaluation_status.eq("evaluated"), "strategy"])
    primary_capacity = stress[(stress.strategy.isin(production_names)) & (stress.capital == args.primary_capital) & (stress.cost_rate == DEFAULT_COST_RATES[0]) & (stress.slippage_bps == DEFAULT_SLIPPAGE_BPS[0])] if not stress.empty else pd.DataFrame()
    capacity_verified = bool(not primary_capacity.empty and primary_capacity.capacity_status.eq("PASS").all())
    production_decision = {
        "primary_capital": args.primary_capital,
        "approved_for_scale_up": bool(not hard_fail and capacity_verified and not benchmark_result.empty),
        "recommended_initial_exposure": 0.0 if hard_fail or not capacity_verified else 0.10,
        "hard_fail_present": hard_fail,
        "capacity_verified": capacity_verified,
        "benchmark_available": not benchmark_result.empty,
        "decision": "DO_NOT_SCALE" if hard_fail or not capacity_verified or benchmark_result.empty else "CANARY_ONLY",
        "freeze_triggers": ["5日亏损<=-8%", "20日回撤<=-15%", "峰值回撤<=-25%", "数据延迟连续3日", "执行偏离连续2日"],
    }
    (output_dir / "production_allocation_decision.json").write_text(json.dumps(production_decision, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance = {
        "generated_at": datetime.now().isoformat(), "git_commit": git_commit(),
        "cards": cards.to_dict(orient="records"),
        "sources": {k: display_path(v) for k, v in sources.items()},
        "parameters": {"primary_capital": args.primary_capital, "capacity_capitals": DEFAULT_CAPITALS, "top_n": 5, "hold_days": 10, "trade_cost_rates": DEFAULT_COST_RATES, "slippage_bps": DEFAULT_SLIPPAGE_BPS, "execution": "T signal / T+1 open", "min_window_coverage": args.min_window_coverage},
        "data_cutoff_by_strategy": summary.set_index("strategy").get("last_date", pd.Series(dtype=object)).dropna().astype(str).to_dict(),
        "production_allocation_decision": production_decision,
        "limitations": ["Review consumes saved immutable exports; database refresh must be run separately.", "Evidence dates differ by strategy.", "Current strategy-card versions may post-date source runs.", "Capacity remains UNVERIFIED when execution proxy fields are absent."],
    }
    (output_dir / "review_manifest.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")

    report_name = "系统全策略生产投配回测评估" if args.production_allocation_review else "系统内置策略全面评估与逐股复盘"
    report_path = DOC_ROOT / f"{args.report_date}_{report_name}.md"
    write_report(report_path, cards, summary, windows, stock, quality, risk, output_dir, provenance["git_commit"], report_name, not benchmark_result.empty)
    if args.production_allocation_review:
        with report_path.open("a", encoding="utf-8") as handle:
            handle.write("\n## 生产投配结论（Fail-closed）\n\n")
            handle.write(f"- 100万元投配结论：**{production_decision['decision']}**。\n")
            handle.write(f"- 建议初始风险暴露：{production_decision['recommended_initial_exposure']:.0%}。\n")
            handle.write(f"- 严格检查失败：{production_decision['hard_fail_present']}；容量已验证：{production_decision['capacity_verified']}；沪深300基准可用：{production_decision['benchmark_available']}。\n")
            handle.write("- 任一数据时效、严格账本、容量或基准检查失败时，不批准扩仓；不得用短窗口高收益覆盖长期回撤。\n")
            handle.write("- 冻结触发：" + "；".join(production_decision["freeze_triggers"]) + "。\n")
            handle.write("\n完整压力矩阵、基准指标和决策 JSON 位于同一原始输出目录。\n")
    print(json.dumps({"output_dir": str(output_dir), "report": str(report_path), "evaluated": int(summary.evaluation_status.eq('evaluated').sum()), "unavailable": int((~summary.evaluation_status.eq('evaluated')).sum())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
