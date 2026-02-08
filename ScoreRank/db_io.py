import pandas as pd
from sqlalchemy import create_engine, text
from typing import List, Optional, Tuple
from config import CONFIG


def get_engine():
    return create_engine(CONFIG["db_url"], future=True)


def get_latest_trade_date(engine, symbols: List[str], adj_type: str) -> Optional[str]:
    """
    获取这批symbols的最新交易日（取全体最大值）
    注意：dwd_stock_daily_standard 表包含所有复权数据，不需要筛选 adj_type
    """
    # SUBSTR(ts_code, 1, 6) IN (...)
    symbol_placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
    
    sql = f"""
    SELECT MAX(trade_date) AS max_date
    FROM {CONFIG["table"]}
    WHERE SUBSTR(ts_code, 1, 6) IN ({symbol_placeholders})
    """
    
    params = {f"s{i}": symbols[i] for i in range(len(symbols))}

    with engine.begin() as conn:
        res = conn.execute(text(sql), params).mappings().first()
    
    if res and res["max_date"]:
        # 转换 int YYYYMMDD -> str YYYY-MM-DD
        date_str = str(res["max_date"])
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
    # 转换日期格式 YYYY-MM-DD -> YYYYMMDD
    start_date_int = int(start_date.replace("-", ""))
    end_date_int = int(end_date.replace("-", "")) if end_date else None
    
    # 转换 symbols 为 tushare 格式 (需要后缀，暂时模糊匹配或假设后缀)
    # 由于 symbol 只是 6 位数字，我们需要匹配 ts_code
    # tushare_stock 中 ts_code 格式为 000001.SZ
    
    end_clause = "AND trade_date <= :end_date" if end_date_int else ""
    
    # 构造 ts_code 列表 (假设 symbols 是 6 位代码)
    # 注意：这里简单的加上 % 通配符可能效率低，最好是应用层处理后缀
    # 但为了兼容现有逻辑，我们可以在 SQL 中处理 substr(ts_code, 1, 6)
    
    # 使用 SUBSTR(ts_code, 1, 6) IN (...)
    symbol_placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
    
    sql = f"""
    SELECT 
        test.ts_code, 
        trade_date, 
        adj_open as open, 
        adj_high as high, 
        adj_low as low, 
        adj_close as close, 
        vol as volume, 
        amount
    FROM {CONFIG["table"]} test
    WHERE trade_date >= :start_date
      {end_clause}
      AND SUBSTR(ts_code, 1, 6) IN ({symbol_placeholders})
    ORDER BY ts_code, trade_date
    """
    
    params = {"start_date": start_date_int}
    if end_date_int:
        params["end_date"] = end_date_int
    params.update({f"s{i}": symbols[i] for i in range(len(symbols))})

    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)

    if df.empty:
        return pd.DataFrame()

    # 标准化
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))
    # ts_code (000001.SZ) -> symbol (000001)
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
    # 构造 symbol 列表 (dim_stock 中 symbol 不带后缀)
    symbol_placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
    
    sql = f"""
    SELECT symbol, name
    FROM tushare_stock.dim_stock
    WHERE symbol IN ({symbol_placeholders})
    """
    
    params = {f"s{i}": symbols[i] for i in range(len(symbols))}
    
    try:
        with engine.begin() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
        
        if df.empty:
             return pd.DataFrame({"symbol": symbols, "name": [""] * len(symbols)})
             
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["name"] = df["name"].fillna("")
        return df
    except Exception as e:
        print(f"Error fetching names: {e}")
        return pd.DataFrame({"symbol": symbols, "name": [""] * len(symbols)})
