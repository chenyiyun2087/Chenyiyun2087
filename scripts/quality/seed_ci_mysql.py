"""Seed a loopback-only MySQL instance with deterministic CI integration data.

This fixture is for code-path verification only.  It is not formal backtest,
PIT, Shadow or economic evidence.  The command refuses non-loopback targets
and requires the URL database to be ``order_test_db``.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


SYMBOLS = (
    ("000001", "000001.SZ", "平安银行", "银行"),
    ("000002", "000002.SZ", "万科A", "房地产"),
    ("600000", "600000.SH", "浦发银行", "银行"),
    ("600519", "600519.SH", "贵州茅台", "白酒"),
    ("300750", "300750.SZ", "宁德时代", "电气设备"),
)


def _business_dates(start: date, end: date) -> list[date]:
    result: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def _validate_url(db_url: str) -> None:
    parsed = make_url(db_url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ci_fixture_requires_loopback_mysql")
    if parsed.database != "order_test_db":
        raise ValueError("ci_fixture_requires_order_test_db")


DDL = (
    "CREATE DATABASE IF NOT EXISTS chenyiyun CHARACTER SET utf8mb4",
    "CREATE DATABASE IF NOT EXISTS tushare_stock CHARACTER SET utf8mb4",
    """
    CREATE TABLE IF NOT EXISTS chenyiyun.dim_trade_cal (
      cal_date DATE PRIMARY KEY, exchange VARCHAR(8), is_open TINYINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chenyiyun.score_rank_daily (
      trade_date DATE NOT NULL, symbol VARCHAR(8) NOT NULL, name VARCHAR(64),
      industry VARCHAR(64), is_bs_candidate TINYINT, score DOUBLE,
      opt_score DOUBLE, claude_score DOUBLE, s_breakout DOUBLE,
      s_liquidity DOUBLE, s_rs DOUBLE, bs_score_v2 DOUBLE,
      bs_consensus_score DOUBLE, bs_model_rank_score DOUBLE,
      bs_model_prob DOUBLE, bs_model_expected_mdd DOUBLE,
      pattern_score DOUBLE, pattern_sentiment VARCHAR(24),
      pattern_risk_level VARCHAR(24), pattern_pass_count INT,
      bullish_pattern_count INT, bearish_pattern_count INT,
      top_pattern_ids TEXT, ashare_signal_keys TEXT,
      market_hs300_pct_chg DOUBLE, market_hs300_ret_20 DOUBLE,
      market_bs_ratio DOUBLE, pool_type VARCHAR(24), bs_gate_label VARCHAR(24),
      PRIMARY KEY (trade_date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dwd_stock_daily_standard (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL,
      adj_open DOUBLE, adj_high DOUBLE, adj_low DOUBLE, adj_close DOUBLE,
      amount DOUBLE, PRIMARY KEY (trade_date, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dwd_daily (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL,
      open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, pre_close DOUBLE,
      vol DOUBLE, amount DOUBLE, PRIMARY KEY (trade_date, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dwd_stock_label_daily (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL, is_st TINYINT,
      PRIMARY KEY (trade_date, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dwd_daily_basic (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL, circ_mv DOUBLE,
      PRIMARY KEY (trade_date, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dim_stock (
      ts_code VARCHAR(16) PRIMARY KEY, industry VARCHAR(64),
      list_date INT, delist_date INT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.dwd_adj_factor (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL, adj_factor DOUBLE,
      PRIMARY KEY (trade_date, ts_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tushare_stock.ads_universe_daily (
      trade_date INT NOT NULL, ts_code VARCHAR(16) NOT NULL,
      is_tradable TINYINT, is_suspended TINYINT, is_listed TINYINT,
      PRIMARY KEY (trade_date, ts_code)
    )
    """,
)


def seed(db_url: str) -> dict[str, int | str]:
    _validate_url(db_url)
    engine = create_engine(db_url, future=True)
    dates = _business_dates(date(2024, 11, 1), date(2025, 1, 10))
    score_dates = {date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)}
    price_rows = 0
    score_rows = 0
    with engine.begin() as connection:
        for statement in DDL:
            connection.execute(text(statement))
        connection.execute(
            text(
                """
                INSERT INTO chenyiyun.dim_trade_cal (cal_date, exchange, is_open)
                VALUES (:cal_date, 'SSE', 1)
                ON DUPLICATE KEY UPDATE is_open=VALUES(is_open)
                """
            ),
            [{"cal_date": item} for item in dates],
        )
        for symbol_index, (symbol, ts_code, name, industry) in enumerate(SYMBOLS):
            connection.execute(
                text(
                    """
                    INSERT INTO tushare_stock.dim_stock
                      (ts_code, industry, list_date, delist_date)
                    VALUES (:ts_code, :industry, 20100101, NULL)
                    ON DUPLICATE KEY UPDATE industry=VALUES(industry)
                    """
                ),
                {"ts_code": ts_code, "industry": industry},
            )
            previous_close = 10.0 + symbol_index * 5.0
            for day_index, trade_day in enumerate(dates):
                trade_key = int(trade_day.strftime("%Y%m%d"))
                close = previous_close * (1.0 + ((day_index + symbol_index) % 5 - 2) * 0.001)
                open_price = previous_close * 1.0005
                amount = 100_000_000.0 + symbol_index * 10_000_000.0
                common = {
                    "trade_date": trade_key,
                    "ts_code": ts_code,
                    "open": open_price,
                    "high": max(open_price, close) * 1.01,
                    "low": min(open_price, close) * 0.99,
                    "close": close,
                    "pre_close": previous_close,
                    "amount": amount,
                }
                connection.execute(
                    text(
                        """
                        INSERT INTO tushare_stock.dwd_stock_daily_standard
                          (trade_date, ts_code, adj_open, adj_high, adj_low, adj_close, amount)
                        VALUES
                          (:trade_date, :ts_code, :open, :high, :low, :close, :amount)
                        ON DUPLICATE KEY UPDATE adj_close=VALUES(adj_close)
                        """
                    ),
                    common,
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO tushare_stock.dwd_daily
                          (trade_date, ts_code, open, high, low, close, pre_close, vol, amount)
                        VALUES
                          (:trade_date, :ts_code, :open, :high, :low, :close, :pre_close, 1000000, :amount)
                        ON DUPLICATE KEY UPDATE close=VALUES(close)
                        """
                    ),
                    common,
                )
                for table, columns, values in (
                    (
                        "dwd_stock_label_daily",
                        "trade_date, ts_code, is_st",
                        ":trade_date, :ts_code, 0",
                    ),
                    (
                        "dwd_daily_basic",
                        "trade_date, ts_code, circ_mv",
                        ":trade_date, :ts_code, 1000000",
                    ),
                    (
                        "dwd_adj_factor",
                        "trade_date, ts_code, adj_factor",
                        ":trade_date, :ts_code, 1",
                    ),
                    (
                        "ads_universe_daily",
                        "trade_date, ts_code, is_tradable, is_suspended, is_listed",
                        ":trade_date, :ts_code, 1, 0, 1",
                    ),
                ):
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO tushare_stock.{table} ({columns})
                            VALUES ({values})
                            ON DUPLICATE KEY UPDATE ts_code=VALUES(ts_code)
                            """
                        ),
                        common,
                    )
                price_rows += 1
                previous_close = close
                if trade_day in score_dates:
                    connection.execute(
                        text(
                            """
                            INSERT INTO chenyiyun.score_rank_daily (
                              trade_date, symbol, name, industry, is_bs_candidate,
                              score, opt_score, claude_score, s_breakout, s_liquidity,
                              s_rs, bs_score_v2, bs_consensus_score, bs_model_rank_score,
                              bs_model_prob, bs_model_expected_mdd, pattern_score,
                              pattern_sentiment, pattern_risk_level, pattern_pass_count,
                              bullish_pattern_count, bearish_pattern_count, top_pattern_ids,
                              ashare_signal_keys, market_hs300_pct_chg, market_hs300_ret_20,
                              market_bs_ratio, pool_type, bs_gate_label
                            ) VALUES (
                              :trade_date, :symbol, :name, :industry, 1,
                              :score, :score, :score, 60, 70, 65, :score, :score,
                              :score, 0.6, -0.1, 60, 'neutral', 'LOW', 1, 1, 0,
                              '[]', '[]', 0.1, 0.01, 0.5, 'full', 'TRADE'
                            )
                            ON DUPLICATE KEY UPDATE score=VALUES(score)
                            """
                        ),
                        {
                            "trade_date": trade_day,
                            "symbol": symbol,
                            "name": name,
                            "industry": industry,
                            "score": 90.0 - symbol_index,
                        },
                    )
                    score_rows += 1
    engine.dispose()
    return {
        "status": "PASS",
        "scope": "SYNTHETIC_CI_INTEGRATION_ONLY",
        "price_rows": price_rows,
        "score_rows": score_rows,
        "calendar_rows": len(dates),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--allow-ci-fixture", action="store_true")
    args = parser.parse_args()
    if not args.allow_ci_fixture:
        raise SystemExit("--allow-ci-fixture is required")
    print(seed(args.db_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
