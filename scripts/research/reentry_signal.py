#!/usr/bin/env python3
"""
复推信号评估 — 对再次出现的订单信号进行 V 型反转检测。

核心逻辑:
  1. 查询该股票过去 30 天在 ads_local_strategy_orders 中的历史信号
  2. 如果上一次推荐后 5 日收益 < -5%，但随后 10 日出现 > 5% 的反弹 → V 型反转
  3. 对 V 型反转复推股：权重 × 1.3
  4. 对持续下跌复推股（无反弹）：权重 × 0.5

用法:
  PYTHONPATH=. python scripts/research/reentry_signal.py --symbol 600507 --date 2026-06-18
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("CHENYIYUN_DB_PASSWORD", "19871019")
from scoreRank.core.db_config import build_sqlalchemy_url


def _to_int_date(d) -> int:
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        return int(d.replace("-", ""))
    return int(d.strftime("%Y%m%d"))


def load_trade_calendar(engine) -> list[int]:
    cal = pd.read_sql(
        text(
            "SELECT cal_date FROM chenyiyun.dim_trade_cal "
            "WHERE exchange='SSE' AND is_open=1 ORDER BY cal_date"
        ),
        engine,
    )
    return sorted(cal["cal_date"].astype(int).tolist())


def load_symbol_history(engine, symbol: str, lookback_days: int = 30) -> pd.DataFrame:
    """查询某只股票在 ads_local_strategy_orders 中的近期信号历史。"""
    since = int((date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d"))
    orders = pd.read_sql(
        text(
            """
        SELECT trade_date, execution_date, ts_code, strategy, side, target_weight
        FROM chenyiyun.ads_local_strategy_orders
        WHERE ts_code IN (:sym_bare, :sym_sz, :sym_sh, :sym_bj)
          AND trade_date >= :since
          AND side = 'BUY'
        ORDER BY trade_date
        """
        ),
        engine,
        params={
            "sym_bare": symbol,
            "sym_sz": f"{symbol}.SZ",
            "sym_sh": f"{symbol}.SH",
            "sym_bj": f"{symbol}.BJ",
            "since": since,
        },
    )
    # Normalize dates
    for col in ["trade_date", "execution_date"]:
        if col in orders.columns:
            orders[col] = orders[col].apply(lambda x: _to_int_date(x) if pd.notna(x) else 0)
    return orders


def compute_forward_performance(engine, symbol: str, entry_date: int, calendar: list[int]) -> dict:
    """计算某只股票在给定入场日期后的前向表现。"""
    # Build next-trade-date map
    cal_set = set(calendar)
    next_date_map = {}
    for i, d in enumerate(calendar[:-1]):
        next_date_map[d] = calendar[i + 1]
    next_date_map[calendar[-1]] = None

    if entry_date not in cal_set:
        return {"ret_5d": np.nan, "ret_10d": np.nan, "max_ret_10d": np.nan, "max_dd_10d": np.nan}

    # Get exit dates: +5d and +10d
    exit_dates = []
    cursor = entry_date
    for _ in range(10):
        nxt = next_date_map.get(cursor)
        if nxt is None:
            break
        exit_dates.append(nxt)
        cursor = nxt

    # Load prices
    prices = pd.read_sql(
        text(
            """
        SELECT trade_date, adj_open, adj_close
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE ts_code IN (:sym_sz, :sym_sh, :sym_bj)
          AND trade_date >= :start AND trade_date <= :end
        ORDER BY trade_date
        """
        ),
        engine,
        params={
            "sym_sz": f"{symbol}.SZ",
            "sym_sh": f"{symbol}.SH",
            "sym_bj": f"{symbol}.BJ",
            "start": entry_date,
            "end": max(exit_dates) if exit_dates else entry_date,
        },
    )

    if prices.empty:
        return {"ret_5d": np.nan, "ret_10d": np.nan, "max_ret_10d": np.nan, "max_dd_10d": np.nan}

    try:
        entry_open = float(prices[prices["trade_date"] == entry_date]["adj_open"].iloc[0])
    except (IndexError, KeyError):
        return {"ret_5d": np.nan, "ret_10d": np.nan, "max_ret_10d": np.nan, "max_dd_10d": np.nan}

    ret_5d = np.nan
    ret_10d = np.nan
    max_ret_10d = -np.inf
    max_dd_10d = np.inf

    for i, ex_d in enumerate(exit_dates):
        close_rows = prices[prices["trade_date"] == ex_d]
        if close_rows.empty:
            continue
        close_px = float(close_rows["adj_close"].iloc[0])
        if not np.isfinite(entry_open) or entry_open <= 0:
            continue
        ret = close_px / entry_open - 1.0
        max_ret_10d = max(max_ret_10d, ret)
        max_dd_10d = min(max_dd_10d, ret)
        if i == 4:
            ret_5d = ret
        if i == 9:
            ret_10d = ret

    return {
        "ret_5d": ret_5d,
        "ret_10d": ret_10d,
        "max_ret_10d": max_ret_10d if np.isfinite(max_ret_10d) else np.nan,
        "max_dd_10d": max_dd_10d if np.isfinite(max_dd_10d) else np.nan,
    }


def classify_reentry(symbol: str, as_of_date: str, engine=None) -> dict:
    """
    分类复推信号：

    Returns:
        {"pattern": "v_reversal"|"continued_weakness"|"first_signal"|"normal_reentry",
         "multiplier": 1.3|0.5|1.0|1.0,
         "history": [...]}
    """
    if engine is None:
        engine = create_engine(build_sqlalchemy_url())

    calendar = load_trade_calendar(engine)
    symbol_clean = str(symbol).zfill(6)
    orders = load_symbol_history(engine, symbol_clean, lookback_days=30)
    trade_date_int = _to_int_date(as_of_date)

    # Filter to only past signals (before current date)
    past_orders = orders[orders["trade_date"] < trade_date_int]
    if past_orders.empty:
        return {"pattern": "first_signal", "multiplier": 1.0, "history": []}

    # Get the most recent past signal
    last_order = past_orders.iloc[-1]
    last_trade_date = int(last_order["trade_date"])
    exec_date = int(last_order["execution_date"]) if pd.notna(last_order.get("execution_date")) else last_trade_date

    perf = compute_forward_performance(engine, symbol_clean, exec_date, calendar)

    ret_5d = perf.get("ret_5d", np.nan)
    max_ret_10d = perf.get("max_ret_10d", np.nan)
    max_dd_10d = perf.get("max_dd_10d", np.nan)

    history = [
        {
            "trade_date": last_trade_date,
            "ret_5d": ret_5d,
            "max_ret_10d": max_ret_10d,
            "max_dd_10d": max_dd_10d,
        }
    ]

    # V-reversal: last time lost >5% in 5 days, but then recovered >5% in 10 days
    if np.isfinite(ret_5d) and ret_5d < -0.05 and np.isfinite(max_ret_10d) and max_ret_10d > 0.05:
        return {"pattern": "v_reversal", "multiplier": 1.30, "history": history}

    # Continued weakness: last time lost >5%, no recovery
    if np.isfinite(ret_5d) and ret_5d < -0.05:
        return {"pattern": "continued_weakness", "multiplier": 0.50, "history": history}

    # Normal reentry: appeared before but not a clear pattern
    return {"pattern": "normal_reentry", "multiplier": 1.0, "history": history}


def main():
    parser = argparse.ArgumentParser(description="复推信号评估")
    parser.add_argument("--symbol", required=True, help="股票代码")
    parser.add_argument("--date", required=True, help="信号日期")
    args = parser.parse_args()

    result = classify_reentry(args.symbol, args.date)
    print(f"\n{args.symbol}: pattern={result['pattern']}, multiplier={result['multiplier']:.2f}")
    if result["history"]:
        for h in result["history"]:
            ret_5d_str = f"{h['ret_5d']*100:.1f}%" if np.isfinite(h['ret_5d']) else "N/A"
            print(f"  上次信号: {h['trade_date']}, 5日收益={ret_5d_str}")


if __name__ == "__main__":
    main()
