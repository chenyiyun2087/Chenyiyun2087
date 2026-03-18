#!/usr/bin/env python3
"""Export yesterday's B/S stocks with latest daily bars and MACD/KDJ indicators.

Data sources:
- B/S signal list: chenyiyun.bs_detection_results
- K-line data: tushare_stock.dwd_stock_daily_standard
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql


DEFAULT_MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
    "autocommit": True,
}

DEFAULT_COLUMNS = [
    "batch_date",
    "stock_code",
    "stock_name",
    "signal_type",
    "has_buy_signal",
    "has_sell_signal",
    "buy_points_count",
    "sell_points_count",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "kdj_k",
    "kdj_d",
    "kdj_j",
]


def parse_target_date(date_str: str | None) -> str:
    if not date_str:
        return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    return pd.to_datetime(date_str).strftime("%Y%m%d")


def normalize_symbol(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "." in text:
        text = text.split(".", 1)[0]
    if text.startswith(("sh", "sz", "bj")):
        text = text[2:]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def resolve_signal_type(has_buy_signal: int, has_sell_signal: int) -> str:
    if has_buy_signal and has_sell_signal:
        return "B,S"
    if has_buy_signal:
        return "B"
    if has_sell_signal:
        return "S"
    return ""


def fetch_bs_stocks(conn: pymysql.connections.Connection, batch_date: str) -> pd.DataFrame:
    sql = """
    SELECT
        batch_date,
        stock_code,
        has_buy_signal,
        has_sell_signal,
        buy_points_count,
        sell_points_count
    FROM chenyiyun.bs_detection_results
    WHERE batch_date = %s
      AND (has_buy_signal = 1 OR has_sell_signal = 1)
    ORDER BY stock_code ASC
    """

    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql, (batch_date,))
        rows = cursor.fetchall()

    if not rows:
        return pd.DataFrame(
            columns=[
                "batch_date",
                "stock_code",
                "has_buy_signal",
                "has_sell_signal",
                "buy_points_count",
                "sell_points_count",
                "signal_type",
            ]
        )

    df = pd.DataFrame(rows)
    df["stock_code"] = df["stock_code"].map(normalize_symbol)
    df = df[df["stock_code"].notna()].copy()

    for col in ["has_buy_signal", "has_sell_signal", "buy_points_count", "sell_points_count"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["signal_type"] = df.apply(
        lambda row: resolve_signal_type(int(row["has_buy_signal"]), int(row["has_sell_signal"])),
        axis=1,
    )
    df = df.drop_duplicates(subset=["stock_code"], keep="last")
    return df


def fetch_stock_names(
    conn: pymysql.connections.Connection,
    symbols: list[str],
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["stock_code", "stock_name"])

    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT symbol AS stock_code, name AS stock_name
    FROM tushare_stock.dim_stock
    WHERE symbol IN ({placeholders})
    """

    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql, tuple(symbols))
        rows = cursor.fetchall()

    if not rows:
        return pd.DataFrame(columns=["stock_code", "stock_name"])

    df = pd.DataFrame(rows)
    df["stock_code"] = df["stock_code"].map(normalize_symbol)
    df["stock_name"] = df["stock_name"].fillna("")
    return df


def fetch_kline_with_warmup(
    conn: pymysql.connections.Connection,
    symbols: list[str],
    end_date: str,
    warmup_days: int,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["stock_code", "trade_date", "open", "high", "low", "close"])

    end_dt = datetime.strptime(end_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=warmup_days)
    start_date = int(start_dt.strftime("%Y%m%d"))
    end_date_int = int(end_date)

    column_map = resolve_kline_ohlc_columns(conn)

    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT
        SUBSTR(ts_code, 1, 6) AS stock_code,
        trade_date,
        {column_map['open']} AS `open`,
        {column_map['high']} AS `high`,
        {column_map['low']} AS `low`,
        {column_map['close']} AS `close`
    FROM tushare_stock.dwd_stock_daily_standard
    WHERE SUBSTR(ts_code, 1, 6) IN ({placeholders})
      AND trade_date BETWEEN %s AND %s
    ORDER BY stock_code ASC, trade_date ASC
    """

    params = list(symbols)
    params.extend([start_date, end_date_int])

    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()

    if not rows:
        return pd.DataFrame(columns=["stock_code", "trade_date", "open", "high", "low", "close"])

    df = pd.DataFrame(rows)
    df["stock_code"] = df["stock_code"].map(normalize_symbol)
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d", errors="coerce")

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["stock_code", "trade_date", "high", "low", "close"]).copy()
    df = df.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
    return df


def add_macd_kdj(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    grouped = out.groupby("stock_code", sort=False)

    ema12 = grouped["close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = grouped["close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
    out["macd_dif"] = ema12 - ema26
    out["macd_dea"] = out.groupby("stock_code", sort=False)["macd_dif"].transform(
        lambda s: s.ewm(span=9, adjust=False).mean()
    )
    out["macd_hist"] = (out["macd_dif"] - out["macd_dea"]) * 2

    low_n = grouped["low"].transform(lambda s: s.rolling(9, min_periods=1).min())
    high_n = grouped["high"].transform(lambda s: s.rolling(9, min_periods=1).max())
    denom = (high_n - low_n).replace(0, np.nan)
    rsv = ((out["close"] - low_n) / denom) * 100

    out["kdj_k"] = rsv.groupby(out["stock_code"], sort=False).transform(
        lambda s: s.ewm(alpha=1 / 3, adjust=False).mean()
    )
    out["kdj_d"] = out["kdj_k"].groupby(out["stock_code"], sort=False).transform(
        lambda s: s.ewm(alpha=1 / 3, adjust=False).mean()
    )
    out["kdj_j"] = 3 * out["kdj_k"] - 2 * out["kdj_d"]
    return out


def resolve_kline_ohlc_columns(conn: pymysql.connections.Connection) -> dict[str, str]:
    sql = """
    SELECT COLUMN_NAME
    FROM information_schema.columns
    WHERE table_schema = 'tushare_stock'
      AND table_name = 'dwd_stock_daily_standard'
    """
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()

    cols = {str(row.get("COLUMN_NAME", "")).lower() for row in rows}
    required = ("open", "high", "low", "close")

    column_map: dict[str, str] = {}
    for col in required:
        if col in cols:
            column_map[col] = f"`{col}`"
            continue
        adj_col = f"adj_{col}"
        if adj_col in cols:
            column_map[col] = f"`{adj_col}`"
            continue
        raise RuntimeError(
            "Cannot resolve OHLC columns from tushare_stock.dwd_stock_daily_standard "
            f"for field '{col}'"
        )

    return column_map


def build_export_dataframe(
    bs_df: pd.DataFrame,
    kline_df: pd.DataFrame,
    stock_names_df: pd.DataFrame,
    lookback_days: int,
) -> pd.DataFrame:
    if bs_df.empty or kline_df.empty:
        return pd.DataFrame(columns=DEFAULT_COLUMNS)

    indicators_df = add_macd_kdj(kline_df)
    latest_df = indicators_df.groupby("stock_code", group_keys=False, sort=False).tail(lookback_days).copy()

    merged = latest_df.merge(bs_df, on="stock_code", how="left")
    merged = merged.merge(stock_names_df, on="stock_code", how="left")
    merged["stock_name"] = merged["stock_name"].fillna("")

    merged["batch_date"] = merged["batch_date"].fillna("")
    merged["trade_date"] = merged["trade_date"].dt.strftime("%Y%m%d")

    output = merged[
        [
            "batch_date",
            "stock_code",
            "stock_name",
            "signal_type",
            "has_buy_signal",
            "has_sell_signal",
            "buy_points_count",
            "sell_points_count",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "macd_dif",
            "macd_dea",
            "macd_hist",
            "kdj_k",
            "kdj_d",
            "kdj_j",
        ]
    ].copy()

    output = output.sort_values(["stock_code", "trade_date"], ascending=[True, True]).reset_index(drop=True)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export yesterday (or specified date) B/S stocks with latest daily bars and "
            "MACD/KDJ indicators to CSV"
        )
    )
    parser.add_argument("--date", type=str, default=None, help="Signal date, e.g. 20260304 or 2026-03-04")
    parser.add_argument("--lookback-days", type=int, default=10, help="Number of latest trade days to export")
    parser.add_argument("--warmup-days", type=int, default=180, help="History window used for indicator calculation")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")

    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_CONFIG["host"], help="MySQL host")
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_CONFIG["port"], help="MySQL port")
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_CONFIG["user"], help="MySQL user")
    parser.add_argument("--mysql-password", default=DEFAULT_MYSQL_CONFIG["password"], help="MySQL password")
    parser.add_argument("--mysql-db", default=DEFAULT_MYSQL_CONFIG["database"], help="Default MySQL database")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = parse_target_date(args.date)

    if args.lookback_days <= 0:
        raise ValueError("--lookback-days must be > 0")
    if args.warmup_days <= 0:
        raise ValueError("--warmup-days must be > 0")

    output_path = Path(args.output) if args.output else Path("result") / f"bs_kline_indicators_{target_date}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mysql_config = {
        "host": args.mysql_host,
        "port": args.mysql_port,
        "user": args.mysql_user,
        "password": args.mysql_password or "",
        "database": args.mysql_db,
        "charset": "utf8mb4",
        "autocommit": True,
    }

    with pymysql.connect(**mysql_config) as conn:
        bs_df = fetch_bs_stocks(conn, target_date)

        if bs_df.empty:
            pd.DataFrame(columns=DEFAULT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
            print(f"No B/S signals found on {target_date}. Empty CSV exported: {output_path}")
            return

        symbols = sorted(bs_df["stock_code"].dropna().astype(str).unique().tolist())
        kline_df = fetch_kline_with_warmup(conn, symbols, target_date, args.warmup_days)
        stock_names_df = fetch_stock_names(conn, symbols)

    export_df = build_export_dataframe(bs_df, kline_df, stock_names_df, args.lookback_days)

    if export_df.empty:
        pd.DataFrame(columns=DEFAULT_COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"No K-line data found for B/S stocks on {target_date}. Empty CSV exported: {output_path}")
        return

    export_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(
        f"Exported {len(export_df)} rows, {export_df['stock_code'].nunique()} stocks to {output_path} "
        f"(signal date: {target_date})"
    )


if __name__ == "__main__":
    main()
