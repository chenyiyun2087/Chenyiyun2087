"""
实盘跟踪数据库层
Live Trading Tracker Database Layer
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pymysql
from sqlalchemy import create_engine, text

from .live_tracker_config import LIVE_CONFIG


def get_db_connection():
    """获取 PyMySQL 连接"""
    # 从 SQLAlchemy URL 解析连接参数
    db_url = LIVE_CONFIG["db_url"]
    # mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4
    parts = db_url.replace("mysql+pymysql://", "").split("@")
    user_pass = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    db_name = host_db[1].split("?")[0]
    
    return pymysql.connect(
        host=host_port[0],
        port=int(host_port[1]) if len(host_port) > 1 else 3306,
        user=user_pass[0],
        password=user_pass[1],
        database=db_name,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_engine():
    """获取 SQLAlchemy 引擎"""
    return create_engine(LIVE_CONFIG["db_url"], future=True)


# ==================== 交易记录 ====================

def insert_trade(
    trade_date: date,
    symbol: str,
    direction: str,  # 'buy' or 'sell'
    price: float,
    shares: int,
    amount: float,
    commission: float,
    reason: str = "",
    score: float = None,
) -> int:
    """插入交易记录，返回插入的 ID"""
    sql = """
    INSERT INTO live_trades 
    (trade_date, symbol, direction, price, shares, amount, commission, reason, score)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                trade_date, symbol, direction, price, shares,
                amount, commission, reason, score
            ))
            conn.commit()
            return cursor.lastrowid
    finally:
        conn.close()


def get_trades(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    symbol: Optional[str] = None,
) -> List[Dict]:
    """查询交易记录"""
    sql = "SELECT * FROM live_trades WHERE 1=1"
    params = []
    
    if start_date:
        sql += " AND trade_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= %s"
        params.append(end_date)
    if symbol:
        sql += " AND symbol = %s"
        params.append(symbol)
    
    sql += " ORDER BY trade_date DESC, id DESC"
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


# ==================== 持仓管理 ====================

def upsert_position(
    symbol: str,
    shares: int,
    avg_cost: float,
    entry_date: date,
    name: str = "",
    current_price: float = None,
) -> None:
    """插入或更新持仓"""
    sql = """
    INSERT INTO live_positions (symbol, name, shares, avg_cost, entry_date, current_price)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        name = VALUES(name),
        shares = VALUES(shares),
        avg_cost = VALUES(avg_cost),
        current_price = VALUES(current_price)
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol, name, shares, avg_cost, entry_date, current_price))
            conn.commit()
    finally:
        conn.close()


def delete_position(symbol: str) -> None:
    """删除持仓（清仓时调用）"""
    sql = "DELETE FROM live_positions WHERE symbol = %s"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            conn.commit()
    finally:
        conn.close()


def get_all_positions() -> List[Dict]:
    """获取所有持仓"""
    sql = "SELECT * FROM live_positions ORDER BY entry_date"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    finally:
        conn.close()


def get_position(symbol: str) -> Optional[Dict]:
    """获取单只股票持仓"""
    sql = "SELECT * FROM live_positions WHERE symbol = %s"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            return cursor.fetchone()
    finally:
        conn.close()


def update_position_price(symbol: str, current_price: float) -> None:
    """更新持仓当前价格"""
    sql = "UPDATE live_positions SET current_price = %s WHERE symbol = %s"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (current_price, symbol))
            conn.commit()
    finally:
        conn.close()


def batch_update_prices(price_dict: Dict[str, float]) -> None:
    """批量更新持仓价格"""
    if not price_dict:
        return
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for symbol, price in price_dict.items():
                cursor.execute(
                    "UPDATE live_positions SET current_price = %s WHERE symbol = %s",
                    (price, symbol)
                )
            conn.commit()
    finally:
        conn.close()


# ==================== 每日快照 ====================

def upsert_daily_snapshot(
    snapshot_date: date,
    cash: float,
    positions_value: float,
    total_equity: float,
    daily_pnl: float = None,
    daily_return_pct: float = None,
    csi300_return_pct: float = None,
    excess_return_pct: float = None,
) -> None:
    """插入或更新每日快照"""
    sql = """
    INSERT INTO live_daily_snapshots 
    (snapshot_date, cash, positions_value, total_equity, daily_pnl, 
     daily_return_pct, csi300_return_pct, excess_return_pct)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        cash = VALUES(cash),
        positions_value = VALUES(positions_value),
        total_equity = VALUES(total_equity),
        daily_pnl = VALUES(daily_pnl),
        daily_return_pct = VALUES(daily_return_pct),
        csi300_return_pct = VALUES(csi300_return_pct),
        excess_return_pct = VALUES(excess_return_pct)
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                snapshot_date, cash, positions_value, total_equity,
                daily_pnl, daily_return_pct, csi300_return_pct, excess_return_pct
            ))
            conn.commit()
    finally:
        conn.close()


def get_daily_snapshots(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Dict]:
    """获取每日快照"""
    sql = "SELECT * FROM live_daily_snapshots WHERE 1=1"
    params = []
    
    if start_date:
        sql += " AND snapshot_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND snapshot_date <= %s"
        params.append(end_date)
    
    sql += " ORDER BY snapshot_date"
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def get_latest_snapshot() -> Optional[Dict]:
    """获取最新快照"""
    sql = "SELECT * FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchone()
    finally:
        conn.close()


# ==================== 交易信号 ====================

def insert_signal(
    signal_date: date,
    symbol: str,
    signal_type: str,  # 'buy', 'sell', 'watch'
    score: float = None,
    bs_signal_strength: float = None,
    reason: str = "",
    name: str = "",
) -> None:
    """插入交易信号（忽略重复）"""
    sql = """
    INSERT IGNORE INTO live_signals 
    (signal_date, symbol, name, signal_type, score, bs_signal_strength, reason)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                signal_date, symbol, name, signal_type,
                score, bs_signal_strength, reason
            ))
            conn.commit()
    finally:
        conn.close()


def get_signals(
    signal_date: Optional[date] = None,
    signal_type: Optional[str] = None,
    is_executed: Optional[bool] = None,
) -> List[Dict]:
    """获取交易信号"""
    sql = "SELECT * FROM live_signals WHERE 1=1"
    params = []
    
    if signal_date:
        sql += " AND signal_date = %s"
        params.append(signal_date)
    if signal_type:
        sql += " AND signal_type = %s"
        params.append(signal_type)
    if is_executed is not None:
        sql += " AND is_executed = %s"
        params.append(1 if is_executed else 0)
    
    sql += " ORDER BY score DESC"
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def mark_signal_executed(signal_date: date, symbol: str, signal_type: str) -> None:
    """标记信号已执行"""
    sql = """
    UPDATE live_signals 
    SET is_executed = 1 
    WHERE signal_date = %s AND symbol = %s AND signal_type = %s
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (signal_date, symbol, signal_type))
            conn.commit()
    finally:
        conn.close()


# ==================== 辅助查询 ====================

def get_latest_prices_from_kline(symbols: List[str], trade_date: date = None) -> Dict[str, float]:
    """从行情表获取最新收盘价"""
    if not symbols:
        return {}
    
    price_table = LIVE_CONFIG.get("price_table", "tushare_stock.dwd_stock_daily_standard")
    
    # 如果没指定日期，查最新日期
    # 注意：这里简化为查询这些股票最近一天的交易日期
    if trade_date is None:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # 构造 IN 查询
                placeholders = ", ".join(["%s"] * len(symbols))
                date_sql = f"""
                SELECT MAX(t.trade_date) as max_date 
                FROM {price_table} t
                JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
                WHERE s.symbol IN ({placeholders})
                """
                cursor.execute(date_sql, symbols)
                row = cursor.fetchone()
                if row and row["max_date"]:
                    # 转换 int YYYYMMDD -> date
                    date_str = str(row["max_date"])
                    trade_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
                else:
                    return {}
        finally:
            conn.close()
    
    # 将 date 对象转为 int YYYYMMDD
    if isinstance(trade_date, (date, datetime)):
        trade_date_int = int(trade_date.strftime("%Y%m%d"))
    else:
        trade_date_int = int(trade_date)

    # 查询收盘价
    placeholders = ", ".join(["%s"] * len(symbols))
    sql = f"""
    SELECT s.symbol, t.adj_close as close 
    FROM {price_table} t
    JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
    WHERE t.trade_date = %s AND s.symbol IN ({placeholders})
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, [trade_date_int] + list(symbols))
            rows = cursor.fetchall()
            return {row["symbol"]: float(row["close"]) for row in rows}
    finally:
        conn.close()


def get_latest_price(symbol: str) -> float:
    """获取最新收盘价"""
    # 优先使用配置中的行情表，默认为 dwd_stock_daily_standard
    price_table = LIVE_CONFIG.get("price_table", "tushare_stock.dwd_stock_daily_standard")
    
    # 关联 dim_stock 获取 ts_code，再查询行情
    sql = f"""
    SELECT t.adj_close as close
    FROM {price_table} t
    JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
    WHERE s.symbol = %s
    ORDER BY t.trade_date DESC
    LIMIT 1
    """
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            result = cursor.fetchone()
            if result:
                return float(result["close"])
            return 0.0
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")
        return 0.0
    finally:
        conn.close()


def get_stock_name(symbol: str) -> str:
    """获取股票名称"""
    sql = "SELECT name FROM tushare_stock.dim_stock WHERE symbol = %s LIMIT 1"
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            result = cursor.fetchone()
            if result:
                return result["name"]
            return symbol
    except Exception:
        return symbol
    finally:
        conn.close()
