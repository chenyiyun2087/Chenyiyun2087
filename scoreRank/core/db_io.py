import pandas as pd
import pymysql
from urllib.parse import parse_qs, unquote, urlparse
from typing import List, Optional

from .config import CONFIG


def _parse_db_url(db_url: str) -> dict:
    parsed = urlparse(db_url)
    params = parse_qs(parsed.query or "")
    charset = params.get("charset", ["utf8mb4"])[0]
    db_name = (parsed.path or "").lstrip("/")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": db_name,
        "charset": charset,
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


def get_engine(as_sqlalchemy: bool = False):
    # Kept function name for compatibility; default returns pymysql connection config.
    if as_sqlalchemy:
        from sqlalchemy import create_engine
        return create_engine(CONFIG["db_url"], future=True)
    return _parse_db_url(CONFIG["db_url"])


def _fetch_rows(db_conf: dict, sql: str, params: tuple | list | dict | None = None) -> list[dict]:
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def get_latest_trade_date(engine, symbols: List[str], adj_type: str) -> Optional[str]:
    """
    获取这批symbols的最新交易日（取全体最大值）
    注意：dwd_stock_daily_standard 表包含所有复权数据，不需要筛选 adj_type
    """
    if not symbols:
        return None

    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT MAX(trade_date) AS max_date
    FROM {CONFIG["table"]}
    WHERE SUBSTR(ts_code, 1, 6) IN ({placeholders})
    """
    rows = _fetch_rows(engine, sql, tuple(symbols))
    max_date = rows[0]["max_date"] if rows else None
    if max_date:
        date_str = str(max_date)
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return None


def fetch_bars_batch(
    engine,
    symbols: List[str],
    adj_type: str,
    start_date: str,
    end_date: Optional[str] = None
) -> pd.DataFrame:
    """
    批量取一批股票的日线（长表），返回列：
    symbol, trade_date, open, high, low, close, volume, amount
    """
    if not symbols:
        return pd.DataFrame()

    start_date_int = int(start_date.replace("-", ""))
    end_date_int = int(end_date.replace("-", "")) if end_date else None

    end_clause = "AND trade_date <= %s" if end_date_int else ""
    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT
        test.ts_code,
        trade_date,
        adj_open AS open,
        adj_high AS high,
        adj_low AS low,
        adj_close AS close,
        vol AS volume,
        amount
    FROM {CONFIG["table"]} test
    WHERE trade_date >= %s
      {end_clause}
      AND SUBSTR(ts_code, 1, 6) IN ({placeholders})
    ORDER BY ts_code, trade_date
    """

    params: list = [start_date_int]
    if end_date_int:
        params.append(end_date_int)
    params.extend(symbols)

    rows = _fetch_rows(engine, sql, tuple(params))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    df["symbol"] = df["ts_code"].astype(str).str.slice(0, 6)
    df = df.drop(columns=["ts_code"])

    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["symbol", "trade_date", "close", "high", "low"])
    return df


def get_symbol_names_if_exist(engine, symbols: List[str]) -> pd.DataFrame:
    """
    从 tushare_stock.dim_stock 获取股票名称
    """
    if not symbols:
        return pd.DataFrame(columns=["symbol", "name"])

    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT symbol, name
    FROM tushare_stock.dim_stock
    WHERE symbol IN ({placeholders})
    """

    try:
        rows = _fetch_rows(engine, sql, tuple(symbols))
        if not rows:
            return pd.DataFrame({"symbol": symbols, "name": [""] * len(symbols)})
        df = pd.DataFrame(rows)
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["name"] = df["name"].fillna("")
        return df
    except Exception as e:
        print(f"Error fetching names: {e}")
        return pd.DataFrame({"symbol": symbols, "name": [""] * len(symbols)})
