"""Review strategy orders forward 1-week performance.

For each order generated in the past ~30 days, compute the stock's forward return
over the next trading week (5 trading days after the execution date), then aggregate
by week/signal-date to assess how well the strategy picks performed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TODAY = date.today()
LOOKBACK_DAYS = 35  # roughly one month
FORWARD_TRADING_DAYS = 5  # one trading week
CANDIDATE_TABLE = "chenyiyun.ads_trusted_strategy_candidates"
PRODUCTION_STRATEGY = "production_governed_vol_position"
PRICE_TABLE = "tushare_stock.dwd_stock_daily_standard"
INDEX_TABLE = "tushare_stock.dwd_index_daily"
BENCHMARK_CODE = "000300.SH"  # CSI 300
CALENDAR_TABLE = "chenyiyun.dim_trade_cal"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_int_date(d: date | str | int) -> int:
    """Convert any date representation to YYYYMMDD int."""
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        return int(d.replace("-", ""))
    return int(d.strftime("%Y%m%d"))


def _from_int_date(d: int) -> date:
    """Convert YYYYMMDD int to date."""
    s = str(d)
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _pct(v, digits=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(x):
        return "-"
    return f"{x * 100:.{digits}f}%"


def _num(v, digits=2):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(x):
        return "-"
    return f"{x:.{digits}f}"


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def load_trade_calendar(engine) -> pd.DataFrame:
    """Load trading days, return sorted ascending."""
    cal = pd.read_sql(
        text(
            f"SELECT cal_date FROM {CALENDAR_TABLE} "
            "WHERE exchange='SSE' AND is_open=1 "
            "ORDER BY cal_date"
        ),
        engine,
    )
    cal["cal_date"] = cal["cal_date"].astype(int)
    return cal


def load_orders(engine, start_date: int, end_date: int, strategy: str = PRODUCTION_STRATEGY) -> pd.DataFrame:
    """Load the latest persisted trusted candidate set for each signal date."""
    orders = pd.read_sql(
        text(
            f"""
        SELECT c.id, c.trade_date, c.strategy, c.symbol AS ts_code,
               c.stock_name, c.industry, c.rank_no, c.rank_score,
               c.effective_weight AS target_weight, c.bs_score_v2,
               c.is_bs_candidate, c.index_bucket, c.market_liquidity_bucket
        FROM {CANDIDATE_TABLE} c
        JOIN (
            SELECT trade_date, strategy, MAX(signal_time) AS latest_signal_time
            FROM {CANDIDATE_TABLE}
            WHERE trade_date >= :start_date AND trade_date <= :end_date
              AND strategy = :strategy
            GROUP BY trade_date, strategy
        ) latest
          ON latest.trade_date=c.trade_date AND latest.strategy=c.strategy
         AND latest.latest_signal_time=c.signal_time
        WHERE c.strategy = :strategy
        ORDER BY c.trade_date, c.rank_no, c.symbol
        """
        ),
        engine,
        params={"start_date": int(start_date), "end_date": int(end_date), "strategy": strategy},
    )
    # Normalize date columns to YYYYMMDD int
    for col in ["trade_date"]:
        if col in orders.columns:
            orders[col] = orders[col].apply(
                lambda x: _to_int_date(x) if pd.notna(x) else 0
            )
    return orders


def load_prices_batch(engine, ts_codes: list[str], start_date: int, end_date: int) -> pd.DataFrame:
    """Load price data for a batch of stocks."""
    if not ts_codes:
        return pd.DataFrame()
    placeholders = ",".join([f":code_{i}" for i in range(len(ts_codes))])
    params = {f"code_{i}": c for i, c in enumerate(ts_codes)}
    params["start_date"] = int(start_date)
    params["end_date"] = int(end_date)

    prices = pd.read_sql(
        text(
            f"""
        SELECT trade_date, ts_code, adj_open, adj_close
        FROM {PRICE_TABLE}
        WHERE ts_code IN ({placeholders})
          AND trade_date >= :start_date AND trade_date <= :end_date
        ORDER BY ts_code, trade_date
        """
        ),
        engine,
        params=params,
    )
    return prices


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def build_forward_schedule(
    orders: pd.DataFrame, calendar: list[int]
) -> tuple[dict, dict]:
    """Map each signal date to its execution date and forward exit dates.

    Returns:
        signal_map: {trade_date_int: {"exec_date": int, "exit_dates": [int, ...]}}
        next_date_map: {date_int: next_trade_date_int}
    """
    cal_set = set(calendar)
    cal_sorted = sorted(cal_set)

    # Build next-trade-date lookup
    next_date_map = {}
    for i, d in enumerate(cal_sorted[:-1]):
        next_date_map[d] = cal_sorted[i + 1]
    next_date_map[cal_sorted[-1]] = None  # last date has no next

    signal_map = {}
    unique_signals = sorted(orders["trade_date"].dropna().unique())

    for sd in unique_signals:
        sd_int = int(sd)
        if sd_int not in cal_set:
            continue

        # CRITICAL: orders are generated after market close on T (trade_date).
        # Execution happens at T+1 open (next trading day).
        # Forward returns are measured from T+1 open to each of the next N
        # trading day closes.
        exec_date = next_date_map.get(sd_int)
        if exec_date is None:
            continue

        # Forward exit dates: exec_date itself + next N-1 trading days.
        # D1 = exec_date close/open - 1 (same-day return)
        # D2 = next day close/open - 1, ... D5 = 5th day close/open - 1
        exit_dates = [exec_date]
        cursor = exec_date
        for _ in range(FORWARD_TRADING_DAYS - 1):
            nxt = next_date_map.get(cursor)
            if nxt is None:
                break
            exit_dates.append(nxt)
            cursor = nxt

        if exit_dates:
            signal_map[sd_int] = {"exec_date": exec_date, "exit_dates": exit_dates}

    return signal_map, next_date_map


def compute_forward_returns(
    orders: pd.DataFrame,
    prices: pd.DataFrame,
    signal_map: dict,
) -> pd.DataFrame:
    """Attach forward returns to each order row.

    Returns a DataFrame with columns:
        trade_date, ts_code, strategy, target_weight,
        forward_ret_d1..d5 (day-level returns),
        forward_ret_1w (close of day 5 vs open of exec day),
        forward_max_ret (max close during the 5 days vs open)
    """
    if orders.empty or prices.empty:
        return orders.assign(
            **{f"forward_ret_d{i+1}": np.nan for i in range(FORWARD_TRADING_DAYS)},
            forward_ret_1w=np.nan,
            forward_max_ret=np.nan,
        )

    # Index prices by (ts_code, trade_date)
    price_idx = prices.set_index(["ts_code", "trade_date"])

    rows = []
    for _, order in orders.iterrows():
        sd = int(order["trade_date"])
        ts_code = str(order["ts_code"])
        sched = signal_map.get(sd)
        if sched is None:
            continue

        exec_date = sched["exec_date"]
        exit_dates = sched["exit_dates"]

        # Get execution day open price
        try:
            entry_open = float(price_idx.loc[(ts_code, exec_date), "adj_open"])
        except (KeyError, TypeError):
            entry_open = np.nan

        rets = {}
        closes = []
        for i, exit_d in enumerate(exit_dates):
            try:
                close_px = float(price_idx.loc[(ts_code, exit_d), "adj_close"])
            except (KeyError, TypeError):
                close_px = np.nan
            closes.append(close_px)
            if np.isfinite(entry_open) and np.isfinite(close_px) and entry_open > 0:
                rets[f"forward_ret_d{i+1}"] = close_px / entry_open - 1.0
            else:
                rets[f"forward_ret_d{i+1}"] = np.nan

        complete_week = len(exit_dates) == FORWARD_TRADING_DAYS and len(closes) == FORWARD_TRADING_DAYS
        if complete_week and all(np.isfinite(c) for c in closes) and np.isfinite(entry_open) and entry_open > 0:
            rets["forward_ret_1w"] = closes[FORWARD_TRADING_DAYS - 1] / entry_open - 1.0
        else:
            rets["forward_ret_1w"] = np.nan
        rets["observation_status"] = "complete" if np.isfinite(rets["forward_ret_1w"]) else "observing"

        # Max return during the 5 days
        finite_closes = [c for c in closes if np.isfinite(c)]
        if finite_closes and np.isfinite(entry_open) and entry_open > 0:
            rets["forward_max_ret"] = max(finite_closes) / entry_open - 1.0
        else:
            rets["forward_max_ret"] = np.nan

        rows.append(
            {
                "trade_date": sd,
                "ts_code": ts_code,
                "strategy": order.get("strategy", ""),
                "target_weight": order.get("target_weight", np.nan),
                "stock_name": order.get("stock_name", ""),
                "industry": order.get("industry", ""),
                "rank_no": order.get("rank_no", np.nan),
                "rank_score": order.get("rank_score", np.nan),
                "bs_score_v2": order.get("bs_score_v2", np.nan),
                "is_bs_candidate": order.get("is_bs_candidate", np.nan),
                "exec_date": exec_date,
                **rets,
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Summary / report
# ---------------------------------------------------------------------------


def summarize_by_signal_date(results: pd.DataFrame) -> pd.DataFrame:
    """Aggregate forward returns by signal date."""
    if results.empty:
        return pd.DataFrame()

    agg = results.groupby("trade_date").agg(
        stock_count=("ts_code", "count"),
        avg_ret_d1=("forward_ret_d1", "mean"),
        avg_ret_d2=("forward_ret_d2", "mean"),
        avg_ret_d3=("forward_ret_d3", "mean"),
        avg_ret_d4=("forward_ret_d4", "mean"),
        avg_ret_d5=("forward_ret_d5", "mean"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        avg_max_ret=("forward_max_ret", "mean"),
        win_rate_1w=("forward_ret_1w", lambda x: (x.dropna() > 0).mean()),
        stocks=("ts_code", lambda x: ",".join(sorted(x))),
    ).reset_index()
    return agg.sort_values("trade_date")


def summarize_by_week(results: pd.DataFrame) -> pd.DataFrame:
    """Aggregate by ISO week."""
    if results.empty:
        return pd.DataFrame()
    df = results.copy()
    df["signal_date_obj"] = df["trade_date"].apply(_from_int_date)
    df["week_start"] = df["signal_date_obj"].apply(
        lambda d: d - timedelta(days=d.weekday())
    )
    agg = df.groupby("week_start").agg(
        trading_days=("trade_date", "nunique"),
        stock_count=("ts_code", "count"),
        unique_stocks=("ts_code", "nunique"),
        avg_ret_1w=("forward_ret_1w", "mean"),
        avg_max_ret=("forward_max_ret", "mean"),
        win_rate_1w=("forward_ret_1w", lambda x: (x.dropna() > 0).mean()),
        hit_3pct=("forward_ret_1w", lambda x: (x.dropna() >= 0.03).mean()),
        hit_5pct=("forward_ret_1w", lambda x: (x.dropna() >= 0.05).mean()),
        hit_10pct=("forward_ret_1w", lambda x: (x.dropna() >= 0.10).mean()),
    ).reset_index()
    if not agg.empty:
        agg["week_start"] = agg["week_start"].apply(
            lambda d: d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        )
    return agg.sort_values("week_start")


def top_bottom_stocks(results: pd.DataFrame, n: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return best and worst performing stocks by 1-week return."""
    valid = results.dropna(subset=["forward_ret_1w"]).copy()
    if valid.empty:
        return pd.DataFrame(), pd.DataFrame()
    valid = valid.sort_values("forward_ret_1w", ascending=False)
    top = valid.head(n)
    bottom = valid.tail(n)
    return top, bottom


def load_benchmark_prices(engine, start_date: int, end_date: int) -> pd.DataFrame:
    """Load CSI 300 index prices for benchmark comparison."""
    bm = pd.read_sql(
        text(
            f"""
        SELECT trade_date, ts_code, `open` AS adj_open, `close` AS adj_close
        FROM {INDEX_TABLE}
        WHERE ts_code = :code
          AND trade_date >= :start_date AND trade_date <= :end_date
        ORDER BY trade_date
        """
        ),
        engine,
        params={"code": BENCHMARK_CODE, "start_date": int(start_date), "end_date": int(end_date)},
    )
    return bm


def compute_benchmark_returns(
    benchmark_prices: pd.DataFrame,
    signal_map: dict,
) -> pd.Series:
    """For each signal date, compute the CSI 300 forward 1-week return."""
    if benchmark_prices.empty:
        return pd.Series(dtype=float)
    price_idx = benchmark_prices.set_index("trade_date")
    records = {}
    for sd_int, sched in signal_map.items():
        exec_date = sched["exec_date"]
        exit_dates = sched["exit_dates"]
        try:
            entry_open = float(price_idx.loc[exec_date, "adj_open"])
        except (KeyError, TypeError):
            records[sd_int] = np.nan
            continue
        if len(exit_dates) < FORWARD_TRADING_DAYS:
            records[sd_int] = np.nan
            continue
        last_close_date = exit_dates[-1]
        try:
            last_close = float(price_idx.loc[last_close_date, "adj_close"])
        except (KeyError, TypeError):
            records[sd_int] = np.nan
            continue
        if np.isfinite(entry_open) and np.isfinite(last_close) and entry_open > 0:
            records[sd_int] = last_close / entry_open - 1.0
        else:
            records[sd_int] = np.nan
    return pd.Series(records, name="benchmark_ret_1w")


def print_report(
    results: pd.DataFrame,
    by_date: pd.DataFrame,
    by_week: pd.DataFrame,
    top_stocks: pd.DataFrame,
    bottom_stocks: pd.DataFrame,
    benchmark_returns: pd.Series | None = None,
) -> str:
    """Generate a human-readable markdown report."""
    total_orders = len(results)
    complete = results["forward_ret_1w"].notna().sum()
    incomplete = total_orders - complete
    overall_avg_1w = results["forward_ret_1w"].mean()
    valid_results = results.dropna(subset=["forward_ret_1w"])
    overall_win_rate = (valid_results["forward_ret_1w"] > 0).mean()
    overall_avg_max = results["forward_max_ret"].mean()
    hit_3 = (valid_results["forward_ret_1w"] >= 0.03).mean()
    hit_5 = (valid_results["forward_ret_1w"] >= 0.05).mean()
    hit_10 = (valid_results["forward_ret_1w"] >= 0.10).mean()

    # Distribution stats
    valid_rets = results["forward_ret_1w"].dropna()
    median_1w = valid_rets.median()
    std_1w = valid_rets.std()
    q25 = valid_rets.quantile(0.25)
    q75 = valid_rets.quantile(0.75)

    lines = []
    lines.append("# 策略订单前向一周表现回顾")
    lines.append(f"\n**分析日期**: {TODAY}  \n**数据范围**: 过去约一个月  \n**持有期**: T+1开盘买入，持有5个交易日至T+5收盘")
    lines.append(f"\n## 总览")
    lines.append(f"- 总订单数: {total_orders}（其中 {complete} 笔已有完整一周数据，{incomplete} 笔尚不足一周）")
    lines.append(f"- 平均一周收益: {_pct(overall_avg_1w)}")
    lines.append(f"- 中位数一周收益: {_pct(median_1w)}")
    lines.append(f"- 标准差: {_pct(std_1w, 1)}")
    lines.append(f"- 收益分布: 25分位 {_pct(q25)} / 75分位 {_pct(q75)}")
    lines.append(f"- 胜率(>0): {_pct(overall_win_rate, 1)}")
    lines.append(f"- 平均最大收益: {_pct(overall_avg_max)}")
    lines.append(f"- 一周涨幅≥3%: {_pct(hit_3, 1)}")
    lines.append(f"- 一周涨幅≥5%: {_pct(hit_5, 1)}")
    lines.append(f"- 一周涨幅≥10%: {_pct(hit_10, 1)}")

    # Benchmark comparison
    if benchmark_returns is not None and not benchmark_returns.empty:
        bm_avg = benchmark_returns.mean()
        bm_win = (benchmark_returns > 0).mean()
        # Align benchmark to signal dates that have results
        matched_dates = set(results["trade_date"].unique()) & set(benchmark_returns.index)
        matched_rets = results[results["trade_date"].isin(matched_dates)]
        strategy_avg = matched_rets["forward_ret_1w"].dropna().mean()
        excess_avg = strategy_avg - bm_avg

        lines.append(f"\n## 基准对比 (沪深300)")
        lines.append(f"- 沪深300同期平均一周收益: {_pct(bm_avg)}")
        lines.append(f"- 策略超额收益: {_pct(excess_avg)}")
        lines.append(f"- 沪深300胜率: {_pct(bm_win, 1)}")
        lines.append(f"- 策略相对胜率: {_pct(overall_win_rate - bm_win, 1)}")

        # By-signal-date comparison
        if not by_date.empty:
            bm_map = benchmark_returns.to_dict()
            lines.append(f"\n## 按信号日收益 vs 沪深300")
            hdr = "| 信号日 | 股票数 | 策略一周收益 | 沪深300 | 超额 | 策略胜率 |"
            lines.append(hdr)
            lines.append("|" + "|".join(["---"] * 6) + "|")
            for _, row in by_date.iterrows():
                sd = int(row["trade_date"])
                bm_ret = bm_map.get(sd, np.nan)
                strategy_ret = row["avg_ret_1w"]
                excess = strategy_ret - bm_ret if np.isfinite(strategy_ret) and np.isfinite(bm_ret) else np.nan
                lines.append(
                    f"| {_from_int_date(sd)} | {int(row['stock_count'])} | "
                    f"{_pct(strategy_ret)} | {_pct(bm_ret)} | "
                    f"{_pct(excess)} | {_pct(row['win_rate_1w'], 1)} |"
                )

    # Per-signal-date table
    if not by_date.empty:
        lines.append(f"\n## 按信号日汇总")
        header = (
            "| 信号日 | 股票数 | D1平均 | D2平均 | D3平均 | D4平均 | D5平均 | 一周收益 | 最大收益 | 胜率 |"
        )
        lines.append(header)
        lines.append("|" + "|".join(["---"] * 10) + "|")
        for _, row in by_date.iterrows():
            sd = _from_int_date(int(row["trade_date"]))
            lines.append(
                f"| {sd} | {int(row['stock_count'])} | "
                f"{_pct(row['avg_ret_d1'])} | {_pct(row['avg_ret_d2'])} | "
                f"{_pct(row['avg_ret_d3'])} | {_pct(row['avg_ret_d4'])} | "
                f"{_pct(row['avg_ret_d5'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['avg_max_ret'])} | {_pct(row['win_rate_1w'], 1)} |"
            )

    # Per-week table
    if not by_week.empty and len(by_week) > 1:
        lines.append(f"\n## 按周汇总")
        header = (
            "| 周起始 | 交易日数 | 订单数 | 去重股票 | 平均一周收益 | 平均最大收益 | 胜率 | ≥3% | ≥5% | ≥10% |"
        )
        lines.append(header)
        lines.append("|" + "|".join(["---"] * 11) + "|")
        for _, row in by_week.iterrows():
            lines.append(
                f"| {row['week_start']} | {int(row['trading_days'])} | {int(row['stock_count'])} | "
                f"{int(row['unique_stocks'])} | {_pct(row['avg_ret_1w'])} | "
                f"{_pct(row['avg_max_ret'])} | {_pct(row['win_rate_1w'], 1)} | "
                f"{_pct(row['hit_3pct'], 1)} | {_pct(row['hit_5pct'], 1)} | "
                f"{_pct(row['hit_10pct'], 1)} |"
            )

    # Top stocks
    if not top_stocks.empty:
        lines.append(f"\n## 表现最佳股票（按一周收益）")
        lines.append("| 信号日 | 代码 | 一周收益 | 最大收益 | D1 | D5 | 权重 |")
        lines.append("|" + "|".join(["---"] * 7) + "|")
        for _, row in top_stocks.iterrows():
            sd = _from_int_date(int(row["trade_date"]))
            lines.append(
                f"| {sd} | {row['ts_code']} | {_pct(row['forward_ret_1w'])} | "
                f"{_pct(row['forward_max_ret'])} | {_pct(row['forward_ret_d1'])} | "
                f"{_pct(row['forward_ret_d5'])} | {_pct(row.get('target_weight', 0))} |"
            )

    # Bottom stocks
    if not bottom_stocks.empty:
        lines.append(f"\n## 表现最差股票（按一周收益）")
        lines.append("| 信号日 | 代码 | 一周收益 | 最大收益 | D1 | D5 | 权重 |")
        lines.append("|" + "|".join(["---"] * 7) + "|")
        for _, row in bottom_stocks.iterrows():
            sd = _from_int_date(int(row["trade_date"]))
            lines.append(
                f"| {sd} | {row['ts_code']} | {_pct(row['forward_ret_1w'])} | "
                f"{_pct(row['forward_max_ret'])} | {_pct(row['forward_ret_d1'])} | "
                f"{_pct(row['forward_ret_d5'])} | {_pct(row.get('target_weight', 0))} |"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Review production recommendations from T+1 open to T+5 close.")
    parser.add_argument("--as-of-date", default=date.today().isoformat(), help="YYYY-MM-DD report cutoff")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--strategy", default=PRODUCTION_STRATEGY)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "exports" / "order_forward_reviews"))
    args = parser.parse_args()
    global TODAY
    TODAY = date.fromisoformat(args.as_of_date)
    engine = create_engine(build_sqlalchemy_url())

    start_date = _to_int_date(TODAY - timedelta(days=args.lookback_days))
    end_date = _to_int_date(TODAY)

    print(f"Loading data: {start_date} ~ {end_date}")

    # 1. Load trade calendar
    cal_df = load_trade_calendar(engine)
    calendar = sorted(cal_df["cal_date"].tolist())
    print(f"  Calendar: {len(calendar)} trading days")

    # 2. Load orders
    orders = load_orders(engine, start_date, end_date, strategy=args.strategy)
    print(f"  Orders: {len(orders)} BUY orders, {orders['trade_date'].nunique()} signal dates")

    if orders.empty:
        print("No orders found in the date range.")
        return

    # 3. Load price data for all stocks in the orders
    all_codes = orders["ts_code"].dropna().unique().tolist()
    # Orders store ts_code as bare code (e.g. "300285"), but price table uses
    # full ts_code with exchange suffix (e.g. "300285.SZ"). Add both variants.
    code_variants = set()
    for c in all_codes:
        c_str = str(c).strip()
        code_variants.add(c_str)
        # Add common exchange suffixes
        for suffix in [".SZ", ".SH", ".BJ"]:
            code_variants.add(c_str + suffix)
    price_codes = list(code_variants)
    price_start = int(str(orders["trade_date"].min()))
    price_end = max(calendar)  # go to the end of available calendar
    prices = load_prices_batch(engine, price_codes, price_start, price_end)
    print(f"  Prices: {len(prices)} rows, {prices['ts_code'].nunique()} stocks")

    # Normalize ts_code in prices to bare code for matching
    # (strip exchange suffix so "300285.SZ" -> "300285")
    prices["ts_code_bare"] = prices["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True)
    # Also normalize orders ts_code to string
    orders["ts_code"] = orders["ts_code"].astype(str).str.strip()
    # Map prices lookup to bare code
    prices["ts_code"] = prices["ts_code_bare"]
    prices = prices.drop(columns=["ts_code_bare"])

    # 4. Build forward schedule
    signal_map, _ = build_forward_schedule(orders, calendar)
    print(f"  Signal maps: {len(signal_map)} signal dates mapped")

    # 5. Compute forward returns
    results = compute_forward_returns(orders, prices, signal_map)
    print(f"  Results: {len(results)} rows")
    print(f"  Complete (have full 1w data): {results['forward_ret_1w'].notna().sum()}")

    # 6. Load benchmark (CSI 300)
    bm_prices = load_benchmark_prices(engine, price_start, price_end)
    bm_rets = compute_benchmark_returns(bm_prices, signal_map)
    print(f"  Benchmark: {bm_rets.notna().sum()} signal dates with CSI 300 data")

    # 7. Summarize
    by_date = summarize_by_signal_date(results)
    by_week = summarize_by_week(results)
    top_stocks, bottom_stocks = top_bottom_stocks(results, n=8)

    # 8. Print report
    report = print_report(results, by_date, by_week, top_stocks, bottom_stocks, bm_rets)
    print("\n" + "=" * 80)
    print(report)

    # 8. Save to file
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = TODAY.strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"orders_forward_1w_review_{ts}.md"
    report_path.write_text(report, encoding="utf-8")

    # Also save raw CSV
    results_path = output_dir / f"orders_forward_returns_{ts}.csv"
    results.to_csv(results_path, index=False, encoding="utf-8-sig")

    print(f"\nReport saved to: {report_path}")
    print(f"Data saved to: {results_path}")


if __name__ == "__main__":
    main()
