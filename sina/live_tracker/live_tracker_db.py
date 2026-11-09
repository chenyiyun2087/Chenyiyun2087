"""
实盘跟踪数据库层
Live Trading Tracker Database Layer
"""

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
import logging

import pandas as pd
from sqlalchemy import create_engine

from live_tracker_config import LIVE_CONFIG


logger = logging.getLogger(__name__)
_SQLALCHEMY_ENGINE = None


def get_db_connection():
    """获取复用连接池的 DBAPI 连接（close 时归还到池）"""
    return get_engine().raw_connection()


@contextmanager
def transaction():
    """Yield a connection with transaction commit/rollback semantics."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def get_engine():
    """获取 SQLAlchemy 引擎"""
    global _SQLALCHEMY_ENGINE
    if _SQLALCHEMY_ENGINE is None:
        _SQLALCHEMY_ENGINE = create_engine(
            LIVE_CONFIG["db_url"],
            future=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
            pool_pre_ping=True,
        )
    return _SQLALCHEMY_ENGINE


def _row_to_dict(cursor, row: Any) -> Optional[Dict]:
    """将 DBAPI 返回行统一转换为 dict。"""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "keys"):
        try:
            return {key: row[key] for key in row.keys()}
        except Exception:
            pass
    if isinstance(row, (tuple, list)):
        columns = [col[0] for col in (cursor.description or [])]
        if columns:
            return dict(zip(columns, row))
    return row


def _fetchall_dict(cursor) -> List[Dict]:
    """fetchall 并确保返回 dict 列表。"""
    rows = cursor.fetchall()
    if not rows:
        return []
    return [_row_to_dict(cursor, row) for row in rows]


def _fetchone_dict(cursor) -> Optional[Dict]:
    """fetchone 并确保返回 dict。"""
    return _row_to_dict(cursor, cursor.fetchone())


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
    conn: Any = None,
) -> int:
    """插入交易记录，返回插入的 ID"""
    sql = """
    INSERT INTO live_trades 
    (trade_date, symbol, direction, price, shares, amount, commission, reason, score)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    owns_conn = conn is None
    c = get_db_connection() if owns_conn else conn
    try:
        with c.cursor() as cursor:
            cursor.execute(sql, (
                trade_date, symbol, direction, price, shares,
                amount, commission, reason, score
            ))
            if owns_conn:
                c.commit()
            return cursor.lastrowid
    finally:
        if owns_conn:
            c.close()


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
            return _fetchall_dict(cursor)
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
    highest_since_entry: float = None,
    holding_trade_days: int = None,
    pending_forced_exit: int = 0,
    pending_exit_reason: str = None,
    rebuy_cooldown_until: date = None,
    conn: Any = None,
) -> None:
    """插入或更新持仓"""
    sql = """
    INSERT INTO live_positions (
        symbol, name, shares, avg_cost, entry_date, current_price,
        highest_since_entry, holding_trade_days, pending_forced_exit, pending_exit_reason, rebuy_cooldown_until
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        name = VALUES(name),
        shares = VALUES(shares),
        avg_cost = VALUES(avg_cost),
        current_price = VALUES(current_price),
        highest_since_entry = GREATEST(COALESCE(highest_since_entry, 0), COALESCE(VALUES(highest_since_entry), 0)),
        holding_trade_days = COALESCE(VALUES(holding_trade_days), holding_trade_days),
        pending_forced_exit = COALESCE(VALUES(pending_forced_exit), pending_forced_exit),
        pending_exit_reason = COALESCE(VALUES(pending_exit_reason), pending_exit_reason),
        rebuy_cooldown_until = COALESCE(VALUES(rebuy_cooldown_until), rebuy_cooldown_until)
    """
    owns_conn = conn is None
    c = get_db_connection() if owns_conn else conn
    try:
        with c.cursor() as cursor:
            hs = highest_since_entry if highest_since_entry is not None else current_price
            cursor.execute(
                sql,
                (
                    symbol,
                    name,
                    shares,
                    avg_cost,
                    entry_date,
                    current_price,
                    hs,
                    holding_trade_days,
                    pending_forced_exit,
                    pending_exit_reason,
                    rebuy_cooldown_until,
                ),
            )
            if owns_conn:
                c.commit()
    finally:
        if owns_conn:
            c.close()


def delete_position(symbol: str, conn: Any = None) -> None:
    """删除持仓（清仓时调用）"""
    sql = "DELETE FROM live_positions WHERE symbol = %s"
    owns_conn = conn is None
    c = get_db_connection() if owns_conn else conn
    try:
        with c.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            if owns_conn:
                c.commit()
    finally:
        if owns_conn:
            c.close()


def get_all_positions() -> List[Dict]:
    """获取所有持仓"""
    sql = "SELECT * FROM live_positions ORDER BY entry_date"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return _fetchall_dict(cursor)
    finally:
        conn.close()


def get_position(symbol: str) -> Optional[Dict]:
    """获取单只股票持仓"""
    sql = "SELECT * FROM live_positions WHERE symbol = %s"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            return _fetchone_dict(cursor)
    finally:
        conn.close()


def update_position_price(symbol: str, current_price: float) -> None:
    """更新持仓当前价格"""
    sql = """
    UPDATE live_positions
    SET current_price = %s,
        highest_since_entry = GREATEST(COALESCE(highest_since_entry, 0), COALESCE(%s, 0))
    WHERE symbol = %s
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (current_price, current_price, symbol))
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
                    """
                    UPDATE live_positions
                    SET current_price = %s,
                        highest_since_entry = GREATEST(COALESCE(highest_since_entry, 0), COALESCE(%s, 0))
                    WHERE symbol = %s
                    """,
                    (price, price, symbol)
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
    conn: Any = None,
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
    owns_conn = conn is None
    c = get_db_connection() if owns_conn else conn
    try:
        with c.cursor() as cursor:
            cursor.execute(sql, (
                snapshot_date, cash, positions_value, total_equity,
                daily_pnl, daily_return_pct, csi300_return_pct, excess_return_pct
            ))
            if owns_conn:
                c.commit()
    finally:
        if owns_conn:
            c.close()


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
            return _fetchall_dict(cursor)
    finally:
        conn.close()


def get_latest_snapshot() -> Optional[Dict]:
    """获取最新快照"""
    sql = "SELECT * FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return _fetchone_dict(cursor)
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
    is_executed: int = 0,
) -> None:
    """插入交易信号（忽略重复）"""
    sql = """
    INSERT INTO live_signals 
    (signal_date, symbol, name, signal_type, score, bs_signal_strength, reason, is_executed)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        score = VALUES(score),
        bs_signal_strength = VALUES(bs_signal_strength),
        reason = VALUES(reason),
        is_executed = VALUES(is_executed)
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                signal_date, symbol, name, signal_type,
                score, bs_signal_strength, reason, is_executed
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
            return _fetchall_dict(cursor)
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
    """从行情表获取最新收盘价 (优先走 ADCClient 统一数据契约)。"""
    if not symbols:
        return {}
    
    # 将 date 对象转为 int YYYYMMDD
    if trade_date is not None:
        trade_date_int = int(trade_date.strftime("%Y%m%d")) if isinstance(trade_date, (date, datetime)) else int(trade_date)
    else:
        trade_date_int = None

    try:
        from cross_project.adc_client import ADCClient
        adc = ADCClient()
        if trade_date_int is None:
            trade_date_int = adc.data.get_latest_data_date(symbols)
            if not trade_date_int:
                return {}
        
        df = adc.data.get_daily_bars_batch(
            symbols=symbols,
            start_date=trade_date_int,
            end_date=trade_date_int,
            adj_type="qfq",
        )
        if not df.empty:
            prices = {}
            for _, row in df.iterrows():
                c_val = row.get("close")
                if pd.notna(c_val) and float(c_val) > 0:
                    prices[str(row["symbol"])] = float(c_val)
            return prices
    except Exception as exc:
        logger.warning("ADCClient 取最新价格失败，回退原生查询: %s", exc)

    price_table = LIVE_CONFIG.get("price_table", "tushare_stock.dwd_stock_daily_standard")
    if trade_date is None:
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(symbols))
                date_sql = f"""
                SELECT MAX(t.trade_date) as max_date 
                FROM {price_table} t
                JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
                WHERE s.symbol IN ({placeholders})
                """
                cursor.execute(date_sql, symbols)
                row = _fetchone_dict(cursor)
                if row and row["max_date"]:
                    date_str = str(row["max_date"])
                    trade_date = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:]))
                else:
                    return {}
        finally:
            conn.close()
    
    if isinstance(trade_date, (date, datetime)):
        trade_date_int = int(trade_date.strftime("%Y%m%d"))
    else:
        trade_date_int = int(trade_date)

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
            rows = _fetchall_dict(cursor)
            return {row["symbol"]: float(row["close"]) for row in rows if row.get("close") is not None}
    finally:
        conn.close()


def get_latest_price(symbol: str) -> float:
    """获取最新收盘价。禁止缺失时静默返回 0.0。"""
    prices = get_latest_prices_from_kline([symbol])
    if symbol in prices and prices[symbol] > 0:
        return prices[symbol]

    price_table = LIVE_CONFIG.get("price_table", "tushare_stock.dwd_stock_daily_standard")
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
            result = _fetchone_dict(cursor)
            if result and result.get("close") is not None and float(result["close"]) > 0:
                return float(result["close"])
            raise ValueError(f"标的 {symbol} 行情缺失或价格为 0，拒绝返回伪造的 0.0 价格！")
    finally:
        conn.close()


def get_stock_name(symbol: str) -> str:
    """获取股票名称"""
    sql = "SELECT name FROM tushare_stock.dim_stock WHERE symbol = %s LIMIT 1"
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (symbol,))
            result = _fetchone_dict(cursor)
            if result:
                return result["name"]
            return symbol
    except Exception:
        return symbol
    finally:
        conn.close()
