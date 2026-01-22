import pandas as pd
from sqlalchemy import create_engine, text
from typing import List, Optional, Tuple
from config import CONFIG


def get_engine():
    return create_engine(CONFIG["db_url"], future=True)


def get_latest_trade_date(engine, symbols: List[str], adj_type: str) -> Optional[str]:
    """
    获取某个adj_type下，这批symbols的最新交易日（取全体最大值）
    """
    sql = f"""
    SELECT MAX(trade_date) AS max_date
    FROM {CONFIG["table"]}
    WHERE adj_type = :adj
      AND symbol IN ({",".join([f":s{i}" for i in range(len(symbols))])})
    """
    params = {"adj": adj_type}
    params.update({f"s{i}": symbols[i] for i in range(len(symbols))})

    with engine.begin() as conn:
        res = conn.execute(text(sql), params).mappings().first()
    return res["max_date"] if res and res["max_date"] else None


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
    end_clause = "AND trade_date <= :end_date" if end_date else ""
    sql = f"""
    SELECT symbol, trade_date, open, high, low, close, volume, amount
    FROM {CONFIG["table"]}
    WHERE adj_type = :adj
      AND trade_date >= :start_date
      {end_clause}
      AND symbol IN ({",".join([f":s{i}" for i in range(len(symbols))])})
    ORDER BY symbol, trade_date
    """
    params = {"adj": adj_type, "start_date": start_date}
    if end_date:
        params["end_date"] = end_date
    params.update({f"s{i}": symbols[i] for i in range(len(symbols))})

    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)

    # 标准化
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["symbol", "trade_date", "close", "high", "low"])
    return df


def get_symbol_names_if_exist(engine, symbols: List[str]) -> pd.DataFrame:
    """
    可选：如果你的表里有name字段，取回来用于ST识别/展示。
    没有name字段的话，会自动返回空df，不影响流程。
    """
    # 先试探字段是否存在
    try:
        sql_test = f"SELECT name FROM {CONFIG['table']} LIMIT 1"
        with engine.begin() as conn:
            conn.execute(text(sql_test))
    except Exception:
        return pd.DataFrame({"symbol": symbols, "name": [""] * len(symbols)})

    sql = f"""
    SELECT symbol, MAX(name) AS name
    FROM {CONFIG["table"]}
    WHERE symbol IN ({",".join([f":s{i}" for i in range(len(symbols))])})
    GROUP BY symbol
    """
    params = {f"s{i}": symbols[i] for i in range(len(symbols))}
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["name"] = df["name"].fillna("")
    return df
