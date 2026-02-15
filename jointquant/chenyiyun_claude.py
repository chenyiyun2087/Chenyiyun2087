# 基于AkShare的量化交易回测与实盘系统
# 迁移自聚宽平台的高股息低杠杆小市值轮动策略

import argparse
import os
import threading
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
import pymysql
import schedule

warnings.filterwarnings('ignore')


@dataclass
class MySQLConfig:
    host: str = os.getenv('MYSQL_HOST', '127.0.0.1')
    port: int = int(os.getenv('MYSQL_PORT', '3306'))
    user: str = os.getenv('MYSQL_USER', 'root')
    password: str = os.getenv('MYSQL_PASSWORD', '')
    database: str = os.getenv('MYSQL_DATABASE', 'quant_trading')


class DatabaseManager:
    """数据库管理器 - 存储历史数据和交易记录"""

    def __init__(self, config: MySQLConfig):
        self.config = config
        self.init_database()

    def _get_conn(self):
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset='utf8mb4',
            autocommit=False,
        )

    def init_database(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_basic
            (
                stock_code   VARCHAR(16) PRIMARY KEY,
                name         VARCHAR(64),
                listing_date DATE,
                exchange     VARCHAR(16)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_daily
            (
                stock_code    VARCHAR(16),
                trade_date    DATE,
                open          DOUBLE,
                high          DOUBLE,
                low           DOUBLE,
                close         DOUBLE,
                volume        DOUBLE,
                turnover_rate DOUBLE,
                high_limit    DOUBLE,
                low_limit     DOUBLE,
                PRIMARY KEY (stock_code, trade_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_dividend
            (
                stock_code      VARCHAR(16),
                register_date   DATE,
                dividend_amount DOUBLE,
                PRIMARY KEY (stock_code, register_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_financial
            (
                stock_code      VARCHAR(16),
                report_date     DATE,
                market_cap      DOUBLE,
                circulating_cap DOUBLE,
                debt_ratio      DOUBLE,
                PRIMARY KEY (stock_code, report_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS trades
            (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                trade_date  DATE,
                stock_code  VARCHAR(16),
                action      VARCHAR(16),
                price       DOUBLE,
                amount      INT,
                value       DOUBLE,
                commission  DOUBLE,
                tax         DOUBLE,
                create_time DATETIME
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS positions
            (
                stock_code  VARCHAR(16) PRIMARY KEY,
                amount      INT,
                avg_cost    DOUBLE,
                update_time DATETIME
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS account_status
            (
                record_date    DATE PRIMARY KEY,
                total_value    DOUBLE,
                cash           DOUBLE,
                position_value DOUBLE,
                returns        DOUBLE,
                create_time    DATETIME
            )
            '''
        )

        conn.commit()
        conn.close()

    def _insert_rows(self, table: str, columns: List[str], rows: List[tuple]):
        if not rows:
            return
        placeholders = ','.join(['%s'] * len(columns))
        updates = ','.join([f"{col}=VALUES({col})" for col in columns if col not in ('stock_code', 'trade_date', 'register_date', 'report_date')])
        sql = (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
            f" ON DUPLICATE KEY UPDATE {updates}"
        )
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
            conn.commit()
        finally:
            conn.close()

    def save_stock_basic(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        rows = [
            (
                row['stock_code'],
                row.get('name'),
                row.get('listing_date'),
                row.get('exchange'),
            )
            for _, row in df.iterrows()
        ]
        self._insert_rows('stock_basic', ['stock_code', 'name', 'listing_date', 'exchange'], rows)

    def save_stock_daily(self, stock_code: str, df: pd.DataFrame):
        if df is None or df.empty:
            return

        rows = [
            (
                stock_code,
                row['trade_date'],
                row['open'],
                row['high'],
                row['low'],
                row['close'],
                row['volume'],
                row.get('turnover_rate'),
                row.get('high_limit'),
                row.get('low_limit'),
            )
            for _, row in df.iterrows()
        ]

        self._insert_rows(
            'stock_daily',
            ['stock_code', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'turnover_rate', 'high_limit', 'low_limit'],
            rows,
        )

    def save_stock_dividend(self, stock_code: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        rows = [
            (
                stock_code,
                row['register_date'],
                row['dividend_amount'],
            )
            for _, row in df.iterrows()
        ]
        self._insert_rows('stock_dividend', ['stock_code', 'register_date', 'dividend_amount'], rows)

    def save_stock_financial(self, stock_code: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        rows = [
            (
                stock_code,
                row['report_date'],
                row['market_cap'],
                row['circulating_cap'],
                row['debt_ratio'],
            )
            for _, row in df.iterrows()
        ]
        self._insert_rows(
            'stock_financial',
            ['stock_code', 'report_date', 'market_cap', 'circulating_cap', 'debt_ratio'],
            rows,
        )

    def get_stock_daily(self, stock_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从数据库获取股票日线数据"""
        conn = self._get_conn()
        query = (
            "SELECT trade_date, open, high, low, close, volume, turnover_rate, high_limit, low_limit "
            "FROM stock_daily WHERE stock_code=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date"
        )
        df = pd.read_sql(query, conn, params=[stock_code, start_date, end_date])
        conn.close()
        return df if not df.empty else None

    def get_latest_trade_date(self, stock_code: str) -> Optional[datetime]:
        conn = self._get_conn()
        query = "SELECT MAX(trade_date) AS latest_date FROM stock_daily WHERE stock_code=%s"
        df = pd.read_sql(query, conn, params=[stock_code])
        conn.close()
        if df.empty or df.iloc[0]['latest_date'] is None:
            return None
        return pd.to_datetime(df.iloc[0]['latest_date'])

    def get_stock_basic_list(self) -> List[str]:
        conn = self._get_conn()
        df = pd.read_sql("SELECT stock_code FROM stock_basic", conn)
        conn.close()
        return df['stock_code'].tolist() if not df.empty else []

    def get_stock_listing_dates(self, stock_list: List[str]) -> Dict[str, Optional[datetime]]:
        if not stock_list:
            return {}
        placeholders = ','.join(['%s'] * len(stock_list))
        conn = self._get_conn()
        df = pd.read_sql(
            f"SELECT stock_code, listing_date FROM stock_basic WHERE stock_code IN ({placeholders})",
            conn,
            params=stock_list,
        )
        conn.close()
        listing_map = {}
        for _, row in df.iterrows():
            listing_map[row['stock_code']] = pd.to_datetime(row['listing_date']) if row['listing_date'] else None
        return listing_map

    def get_stock_names(self, stock_list: List[str]) -> Dict[str, Optional[str]]:
        if not stock_list:
            return {}
        placeholders = ','.join(['%s'] * len(stock_list))
        conn = self._get_conn()
        df = pd.read_sql(
            f"SELECT stock_code, name FROM stock_basic WHERE stock_code IN ({placeholders})",
            conn,
            params=stock_list,
        )
        conn.close()
        name_map = {}
        for _, row in df.iterrows():
            name_map[row['stock_code']] = row['name']
        return name_map

    def get_dividend_sum(self, stock_code: str, start_date: str, end_date: str) -> float:
        conn = self._get_conn()
        query = (
            "SELECT SUM(dividend_amount) AS total_dividend FROM stock_dividend "
            "WHERE stock_code=%s AND register_date BETWEEN %s AND %s"
        )
        df = pd.read_sql(query, conn, params=[stock_code, start_date, end_date])
        conn.close()
        if df.empty or df.iloc[0]['total_dividend'] is None:
            return 0.0
        return float(df.iloc[0]['total_dividend'])

    def get_latest_financial(self, stock_code: str, target_date: str) -> Optional[pd.Series]:
        conn = self._get_conn()
        query = (
            "SELECT report_date, market_cap, circulating_cap, debt_ratio FROM stock_financial "
            "WHERE stock_code=%s AND report_date <= %s ORDER BY report_date DESC LIMIT 1"
        )
        df = pd.read_sql(query, conn, params=[stock_code, target_date])
        conn.close()
        if df.empty:
            return None
        return df.iloc[0]

    def get_turnover_volatility(self, stock_code: str, start_date: str, end_date: str) -> Optional[float]:
        conn = self._get_conn()
        query = (
            "SELECT turnover_rate FROM stock_daily "
            "WHERE stock_code=%s AND trade_date BETWEEN %s AND %s AND turnover_rate IS NOT NULL"
        )
        df = pd.read_sql(query, conn, params=[stock_code, start_date, end_date])
        conn.close()
        if df.empty:
            return None
        return float(df['turnover_rate'].std())

    def save_trade(self, trade: Dict):
        """保存交易记录"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO trades (trade_date, stock_code, action, price, amount, value, commission, tax, create_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (
                trade['date'].strftime('%Y-%m-%d'),
                trade['stock'],
                trade['action'],
                trade['price'],
                trade['amount'],
                trade['value'],
                trade.get('commission', 0),
                trade.get('tax', 0),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        conn.close()

    def save_positions(self, positions: Dict):
        """保存持仓信息"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM positions')

        for stock, pos in positions.items():
            cursor.execute(
                '''
                INSERT INTO positions (stock_code, amount, avg_cost, update_time)
                VALUES (%s, %s, %s, %s)
                ''',
                (
                    stock,
                    pos['amount'],
                    pos['avg_cost'],
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                ),
            )

        conn.commit()
        conn.close()

    def load_positions(self) -> Dict:
        """加载持仓信息"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT stock_code, amount, avg_cost FROM positions')
        rows = cursor.fetchall()
        conn.close()

        positions = {}
        for row in rows:
            positions[row[0]] = {
                'amount': row[1],
                'avg_cost': row[2],
            }
        return positions

    def save_account_status(self, date: datetime, total_value: float, cash: float, position_value: float, returns: float):
        """保存账户状态"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO account_status (record_date, total_value, cash, position_value, returns, create_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                total_value=VALUES(total_value),
                cash=VALUES(cash),
                position_value=VALUES(position_value),
                returns=VALUES(returns),
                create_time=VALUES(create_time)
            ''',
            (
                date.strftime('%Y-%m-%d'),
                total_value,
                cash,
                position_value,
                returns,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            ),
        )
        conn.commit()
        conn.close()

    def get_trade_history(self, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取交易历史"""
        conn = self._get_conn()
        query = "SELECT * FROM trades"
        conditions = []

        if start_date:
            conditions.append(f"trade_date >= '{start_date}'")
        if end_date:
            conditions.append(f"trade_date <= '{end_date}'")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY trade_date, create_time"

        df = pd.read_sql(query, conn)
        conn.close()
        return df

    def get_account_history(self) -> pd.DataFrame:
        """获取账户历史"""
        conn = self._get_conn()
        df = pd.read_sql("SELECT * FROM account_status ORDER BY record_date", conn)
        conn.close()
        return df


class DataSyncer:
    """使用 AkShare 获取并同步数据到 MySQL"""

    def __init__(self, db_manager: DatabaseManager, request_delay=0.5):
        self.db = db_manager
        self.request_delay = request_delay
        self.last_request_time = 0

    def _rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.request_delay:
            time.sleep(self.request_delay - time_since_last)
        self.last_request_time = time.time()

    def get_all_stocks(self) -> List[str]:
        self._rate_limit()
        stock_info = ak.stock_info_a_code_name()
        stock_list = stock_info['code'].tolist()

        formatted_list = []
        for code in stock_list:
            if code.startswith('6'):
                formatted_list.append(f"{code}.XSHG")
            elif code.startswith(('0', '3')):
                formatted_list.append(f"{code}.XSHE")

        return formatted_list

    def sync_stock_basic(self):
        self._rate_limit()
        stock_info = ak.stock_info_a_code_name()
        records = []

        for _, row in stock_info.iterrows():
            code = row['code']
            name = row['name']
            exchange = 'XSHG' if code.startswith('6') else 'XSHE'
            listing_date = None
            try:
                self._rate_limit()
                info_df = ak.stock_individual_info_em(symbol=code)
                info_map = {item['item']: item['value'] for _, item in info_df.iterrows()}
                if '上市日期' in info_map:
                    listing_date = pd.to_datetime(info_map['上市日期']).date()
            except Exception:
                listing_date = None

            stock_code = f"{code}.{exchange}"
            records.append(
                {
                    'stock_code': stock_code,
                    'name': name,
                    'listing_date': listing_date,
                    'exchange': exchange,
                }
            )

        self.db.save_stock_basic(pd.DataFrame(records))

    def sync_stock_daily_history(self, stock_codes: List[str], start_date: datetime, end_date: datetime):
        for stock_code in stock_codes:
            self._rate_limit()
            code = stock_code.split('.')[0]
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_date.strftime('%Y%m%d'),
                    end_date=end_date.strftime('%Y%m%d'),
                    adjust="qfq",
                )
            except Exception as exc:
                print(f"获取 {stock_code} 历史行情失败: {exc}")
                continue

            if df is None or df.empty:
                continue

            df = df.rename(
                columns={
                    '日期': 'trade_date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '换手率': 'turnover_rate',
                }
            )
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
            df['high_limit'] = (df['close'].shift(1) * 1.1).round(2)
            df['low_limit'] = (df['close'].shift(1) * 0.9).round(2)
            self.db.save_stock_daily(stock_code, df)

    def sync_stock_daily_incremental(self, stock_codes: List[str], end_date: datetime):
        for stock_code in stock_codes:
            latest = self.db.get_latest_trade_date(stock_code)
            start_date = latest + timedelta(days=1) if latest else end_date - timedelta(days=365)
            if start_date > end_date:
                continue
            self.sync_stock_daily_history(stock_codes=[stock_code], start_date=start_date, end_date=end_date)

    def _fetch_dividend_df(self, code: str) -> Optional[pd.DataFrame]:
        for func_name in ['stock_dividend_cninfo', 'stock_dividend_em', 'stock_dividend_summary_sina']:
            if hasattr(ak, func_name):
                func = getattr(ak, func_name)
                try:
                    self._rate_limit()
                    df = func(symbol=code)
                    if df is not None and not df.empty:
                        return df
                except Exception:
                    continue
        return None

    def sync_dividend_history(self, stock_codes: List[str]):
        for stock_code in stock_codes:
            code = stock_code.split('.')[0]
            df = self._fetch_dividend_df(code)
            if df is None or df.empty:
                continue

            date_col = None
            amount_col = None
            for candidate in ['权益登记日', '股权登记日', '登记日', '实施公告日']:
                if candidate in df.columns:
                    date_col = candidate
                    break
            for candidate in ['派息', '每10股派现金', '现金分红', '分红金额']:
                if candidate in df.columns:
                    amount_col = candidate
                    break

            if date_col is None or amount_col is None:
                continue

            dividend_df = df[[date_col, amount_col]].rename(
                columns={date_col: 'register_date', amount_col: 'dividend_amount'}
            )
            dividend_df = dividend_df.dropna()
            dividend_df['register_date'] = pd.to_datetime(dividend_df['register_date']).dt.date
            dividend_df['dividend_amount'] = pd.to_numeric(dividend_df['dividend_amount'], errors='coerce').fillna(0)
            self.db.save_stock_dividend(stock_code, dividend_df)

    def sync_financial_snapshot(self, stock_codes: List[str], snapshot_date: datetime):
        self._rate_limit()
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return

        for stock_code in stock_codes:
            code = stock_code.split('.')[0]
            row = df[df['代码'] == code]
            if row.empty:
                continue
            market_cap = row.iloc[0].get('总市值')
            circulating_cap = row.iloc[0].get('流通市值')
            debt_ratio = None
            if hasattr(ak, 'stock_financial_analysis_indicator'):
                try:
                    self._rate_limit()
                    fin_df = ak.stock_financial_analysis_indicator(symbol=code)
                    if fin_df is not None and not fin_df.empty:
                        if '资产负债率(%)' in fin_df.columns:
                            debt_ratio = float(fin_df.iloc[0]['资产负债率(%)']) / 100
                except Exception:
                    debt_ratio = None

            record = pd.DataFrame(
                [
                    {
                        'report_date': snapshot_date.date(),
                        'market_cap': market_cap,
                        'circulating_cap': circulating_cap,
                        'debt_ratio': debt_ratio,
                    }
                ]
            )
            self.db.save_stock_financial(stock_code, record)


class DataProvider:
    """数据提供者 - 从 MySQL 获取数据"""

    def __init__(self, db_manager: DatabaseManager, request_delay=0.5):
        self.db = db_manager
        self.request_delay = request_delay
        self.last_request_time = 0
        self.stock_list_cache = None
        self.stock_list_cache_time = None

    def _rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.request_delay:
            time.sleep(self.request_delay - time_since_last)
        self.last_request_time = time.time()

    def get_all_stocks(self) -> List[str]:
        if self.stock_list_cache and self.stock_list_cache_time:
            if (datetime.now() - self.stock_list_cache_time).seconds < 3600:
                return self.stock_list_cache

        stock_list = self.db.get_stock_basic_list()
        if stock_list:
            self.stock_list_cache = stock_list
            self.stock_list_cache_time = datetime.now()
            return stock_list

        try:
            self._rate_limit()
            stock_info = ak.stock_info_a_code_name()
            stock_list = stock_info['code'].tolist()

            formatted_list = []
            for code in stock_list:
                if code.startswith('6'):
                    formatted_list.append(f"{code}.XSHG")
                elif code.startswith('0') or code.startswith('3'):
                    formatted_list.append(f"{code}.XSHE")

            self.stock_list_cache = formatted_list
            self.stock_list_cache_time = datetime.now()

            return formatted_list
        except Exception as exc:
            print(f"获取股票列表失败: {exc}")
            return self.stock_list_cache if self.stock_list_cache else []

    def get_stock_data(self, stock_code: str, start_date: datetime, end_date: datetime) -> Optional[pd.DataFrame]:
        df = self.db.get_stock_daily(
            stock_code,
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
        )
        return df if df is not None and not df.empty else None

    def get_price_on_date(self, stock_code: str, target_date: datetime) -> Optional[float]:
        df = self.get_stock_data(stock_code, target_date - timedelta(days=10), target_date)
        if df is None or df.empty:
            return None

        date_str = target_date.strftime('%Y-%m-%d')
        if 'trade_date' in df.columns:
            row = df[df['trade_date'] == date_str]
            if not row.empty:
                return float(row.iloc[0]['close'])
        return None

    def batch_get_prices_on_date(self, stock_codes: List[str], target_date: datetime) -> Dict[str, float]:
        prices = {}
        for stock_code in stock_codes:
            price = self.get_price_on_date(stock_code, target_date)
            if price is not None:
                prices[stock_code] = price
        return prices

    def get_realtime_price(self, stock_code: str) -> Optional[float]:
        try:
            self._rate_limit()
            code = stock_code.split('.')[0]
            df = ak.stock_zh_a_spot_em()
            stock_data = df[df['代码'] == code]
            if not stock_data.empty:
                return float(stock_data.iloc[0]['最新价'])
        except Exception as exc:
            print(f"获取 {stock_code} 实时价格失败: {exc}")
        return None

    def batch_get_realtime_prices(self, stock_codes: List[str]) -> Dict[str, float]:
        prices = {}
        try:
            self._rate_limit()
            df = ak.stock_zh_a_spot_em()

            for stock_code in stock_codes:
                code = stock_code.split('.')[0]
                stock_data = df[df['代码'] == code]
                if not stock_data.empty:
                    prices[stock_code] = float(stock_data.iloc[0]['最新价'])
        except Exception as exc:
            print(f"批量获取实时价格失败: {exc}")

        return prices


class BacktestEngine:
    """回测引擎"""

    def __init__(self, initial_cash=1000000, commission=0.0003, tax=0.001, db_manager=None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.commission = commission
        self.tax = tax
        self.positions = {}
        self.total_value = initial_cash
        self.trades = []
        self.daily_records = []
        self.db = db_manager

    def get_portfolio_value(self, current_prices: Dict[str, float]) -> float:
        position_value = 0
        for stock, pos in self.positions.items():
            if stock in current_prices:
                position_value += pos['amount'] * current_prices[stock]
        self.total_value = self.cash + position_value
        return self.total_value

    def buy(self, stock: str, price: float, value: float, date: datetime) -> bool:
        if value <= 0 or price <= 0:
            return False

        commission_cost = value * self.commission
        max_cost = value + commission_cost

        if max_cost > self.cash:
            value = self.cash / (1 + self.commission)

        amount = int(value / price / 100) * 100

        if amount <= 0:
            return False

        actual_value = amount * price
        commission_fee = max(actual_value * self.commission, 5)
        total_cost = actual_value + commission_fee

        if total_cost > self.cash:
            return False

        if stock in self.positions:
            old_amount = self.positions[stock]['amount']
            old_cost = self.positions[stock]['avg_cost']
            new_amount = old_amount + amount
            new_cost = (old_amount * old_cost + actual_value) / new_amount
            self.positions[stock] = {'amount': new_amount, 'avg_cost': new_cost}
        else:
            self.positions[stock] = {'amount': amount, 'avg_cost': price}

        self.cash -= total_cost

        trade = {
            'date': date,
            'stock': stock,
            'action': 'buy',
            'price': price,
            'amount': amount,
            'value': actual_value,
            'commission': commission_fee,
        }
        self.trades.append(trade)

        if self.db:
            self.db.save_trade(trade)

        return True

    def sell(self, stock: str, price: float, date: datetime) -> bool:
        if stock not in self.positions or price <= 0:
            return False

        amount = self.positions[stock]['amount']
        value = amount * price
        commission_fee = max(value * self.commission, 5)
        tax_fee = value * self.tax
        total_income = value - commission_fee - tax_fee

        self.cash += total_income
        del self.positions[stock]

        trade = {
            'date': date,
            'stock': stock,
            'action': 'sell',
            'price': price,
            'amount': amount,
            'value': value,
            'commission': commission_fee,
            'tax': tax_fee,
        }
        self.trades.append(trade)

        if self.db:
            self.db.save_trade(trade)

        return True

    def get_position_info(self) -> pd.DataFrame:
        if not self.positions:
            return pd.DataFrame()

        data = []
        for stock, pos in self.positions.items():
            data.append(
                {
                    'stock_code': stock,
                    'amount': pos['amount'],
                    'avg_cost': pos['avg_cost'],
                    'current_value': pos['amount'] * pos['avg_cost'],
                }
            )

        return pd.DataFrame(data)


class HighDividendStrategy:
    """高股息低杠杆小市值轮动策略"""

    def __init__(self, engine: BacktestEngine, data_provider: DataProvider, db_manager: DatabaseManager):
        self.engine = engine
        self.data = data_provider
        self.db = db_manager
        self.stock_num = 5
        self.limit_days = 20
        self.history_hold_list = []
        self.not_buy_again_list = []
        self.limit_up_list = []

    def filter_new_stock(self, stock_list: List[str], current_date: datetime, days=375) -> List[str]:
        listing_dates = self.db.get_stock_listing_dates(stock_list)
        filtered = []
        for stock in stock_list:
            listing_date = listing_dates.get(stock)
            if listing_date is None:
                filtered.append(stock)
                continue
            if (current_date.date() - listing_date.date()).days >= days:
                filtered.append(stock)
        return filtered

    def filter_st_stock(self, stock_list: List[str]) -> List[str]:
        names = self.db.get_stock_names(stock_list)
        filtered = []
        for stock in stock_list:
            name = names.get(stock, '') or ''
            if not any(x in name for x in ['ST', '*', '退']):
                filtered.append(stock)
        return filtered

    def filter_kcbj_stock(self, stock_list: List[str]) -> List[str]:
        filtered = []
        for stock in stock_list:
            code = stock.split('.')[0]
            if not (code.startswith('68') or code.startswith('8') or code.startswith('4')):
                filtered.append(stock)
        return filtered

    def filter_paused_stock(self, stock_list: List[str]) -> List[str]:
        return stock_list

    def filter_limit_up_stock(self, stock_list: List[str], current_prices: Dict[str, float]) -> List[str]:
        filtered = []
        for stock in stock_list:
            if stock in self.engine.positions or stock not in current_prices:
                filtered.append(stock)
            else:
                price = current_prices.get(stock, 0)
                if price > 0:
                    filtered.append(stock)
        return filtered

    def get_dividend_ratio_filter_list(self, current_date: datetime, stock_list: List[str], sort: bool, p1: float, p2: float) -> List[str]:
        time1 = current_date.date()
        time0 = time1 - timedelta(days=365)
        records = []

        for stock in stock_list:
            dividend_sum = self.db.get_dividend_sum(stock, time0.strftime('%Y-%m-%d'), time1.strftime('%Y-%m-%d'))
            financial = self.db.get_latest_financial(stock, time1.strftime('%Y-%m-%d'))
            if financial is None or financial['market_cap'] is None:
                continue
            dividend_ratio = (dividend_sum / 10000) / float(financial['market_cap']) if financial['market_cap'] else 0
            records.append({'stock': stock, 'dividend_ratio': dividend_ratio})

        if not records:
            return []
        df = pd.DataFrame(records)
        df = df.sort_values(by='dividend_ratio', ascending=sort)
        return list(df['stock'])[int(p1 * len(df)) : int(p2 * len(df))]

    def get_factor_filter_list(self, current_date: datetime, stock_list: List[str], factor: str, sort: bool, p1: float, p2: float) -> List[str]:
        scores = []
        end_date = current_date.strftime('%Y-%m-%d')
        start_date = (current_date - timedelta(days=60)).strftime('%Y-%m-%d')

        for stock in stock_list:
            if factor == 'turnover_volatility':
                volatility = self.db.get_turnover_volatility(stock, start_date, end_date)
                if volatility is None:
                    continue
                scores.append({'stock': stock, 'score': volatility})
            elif factor == 'MLEV':
                financial = self.db.get_latest_financial(stock, end_date)
                if financial is None or financial['debt_ratio'] is None:
                    continue
                scores.append({'stock': stock, 'score': float(financial['debt_ratio'])})

        if not scores:
            return []

        df = pd.DataFrame(scores)
        df = df.sort_values(by='score', ascending=sort)
        return list(df['stock'])[int(p1 * len(df)) : int(p2 * len(df))]

    def select_stocks(self, current_date: datetime) -> List[str]:
        print(f"\n{current_date.date()} 开始选股...")

        all_stocks = self.data.get_all_stocks()
        print(f"总股票数: {len(all_stocks)}")

        stocks = self.filter_kcbj_stock(all_stocks)
        print(f"过滤科创板北交所后: {len(stocks)}")

        stocks = self.filter_new_stock(stocks, current_date)
        print(f"过滤次新股后: {len(stocks)}")

        stocks = self.filter_st_stock(stocks)
        print(f"过滤ST后: {len(stocks)}")

        dr_list = self.get_dividend_ratio_filter_list(current_date, stocks, False, 0, 0.5)
        print(f"高股息筛选后: {len(dr_list)}")

        tv_list = self.get_factor_filter_list(current_date, dr_list, 'turnover_volatility', False, 0, 0.8)
        print(f"高波动筛选后: {len(tv_list)}")

        lev_list = self.get_factor_filter_list(current_date, tv_list, 'MLEV', True, 0, 0.5)
        print(f"低负债筛选后: {len(lev_list)}")

        caps = []
        for stock in lev_list:
            financial = self.db.get_latest_financial(stock, current_date.strftime('%Y-%m-%d'))
            if financial is None or financial['circulating_cap'] is None:
                continue
            caps.append({'stock': stock, 'cap': float(financial['circulating_cap'])})

        if not caps:
            return []

        cap_df = pd.DataFrame(caps)
        cap_df = cap_df.sort_values(by='cap')
        final_stocks = list(cap_df['stock'])[:15]

        print(f"最终选股数量: {len(final_stocks)}")
        return final_stocks

    def prepare_stock_list(self):
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 准备股票池...")

        current_hold = list(self.engine.positions.keys())
        self.history_hold_list.append(current_hold)

        if len(self.history_hold_list) > self.limit_days:
            self.history_hold_list = self.history_hold_list[-self.limit_days :]

        temp_set = set()
        for hold_list in self.history_hold_list:
            for stock in hold_list:
                temp_set.add(stock)
        self.not_buy_again_list = list(temp_set)

        print(f"当前持仓: {len(current_hold)} 只")
        print(f"历史持仓池: {len(self.not_buy_again_list)} 只")

    def weekly_adjustment(self, current_date: Optional[datetime] = None, current_prices: Optional[Dict[str, float]] = None):
        now = current_date or datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 开始周度调仓...")

        target_stocks = self.select_stocks(now)

        all_stocks = list(set(list(self.engine.positions.keys()) + target_stocks))
        if current_prices is None:
            current_prices = self.data.batch_get_realtime_prices(all_stocks)

        target_stocks = self.filter_paused_stock(target_stocks)
        target_stocks = self.filter_limit_up_stock(target_stocks, current_prices)
        target_stocks = target_stocks[: self.stock_num]

        for stock in list(self.engine.positions.keys()):
            if stock not in target_stocks and stock not in self.limit_up_list:
                if stock in current_prices:
                    self.engine.sell(stock, current_prices[stock], now)
                    print(f"卖出 {stock} @ {current_prices[stock]:.2f}")

        position_count = len(self.engine.positions)
        target_count = min(self.stock_num, len(target_stocks))

        if target_count > position_count:
            buy_count = target_count - position_count
            buy_value = self.engine.cash / buy_count if buy_count > 0 else 0

            for stock in target_stocks:
                if stock not in self.engine.positions and stock in current_prices:
                    if self.engine.buy(stock, current_prices[stock], buy_value, now):
                        print(f"买入 {stock} @ {current_prices[stock]:.2f}")
                        if len(self.engine.positions) >= target_count:
                            break

        if self.engine.db:
            self.engine.db.save_positions(self.engine.positions)

    def check_limit_up(self, current_date: Optional[datetime] = None, current_prices: Optional[Dict[str, float]] = None):
        now = current_date or datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 检查涨停股票...")

        if not self.limit_up_list:
            return

        if current_prices is None:
            current_prices = self.data.batch_get_realtime_prices(self.limit_up_list)

        for stock in self.limit_up_list[:]:
            if stock in current_prices:
                if stock in self.engine.positions:
                    cost = self.engine.positions[stock]['avg_cost']
                    if current_prices[stock] < cost * 1.05:
                        self.engine.sell(stock, current_prices[stock], now)
                        print(f"涨停打开,卖出 {stock} @ {current_prices[stock]:.2f}")
                        self.limit_up_list.remove(stock)

    def print_position_info(self, current_date: Optional[datetime] = None, current_prices: Optional[Dict[str, float]] = None):
        now = current_date or datetime.now()
        print(f"\n{'=' * 60}")
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 持仓信息")
        print(f"{'=' * 60}")

        if not self.engine.positions:
            print("当前无持仓")
            return

        if current_prices is None:
            current_prices = self.data.batch_get_realtime_prices(list(self.engine.positions.keys()))

        total_profit = 0
        for stock, pos in self.engine.positions.items():
            if stock in current_prices:
                current_price = current_prices[stock]
                cost = pos['avg_cost']
                amount = pos['amount']
                profit_rate = (current_price / cost - 1) * 100
                profit = (current_price - cost) * amount
                total_profit += profit

                print(f"\n股票: {stock}")
                print(f"  持仓: {amount} 股")
                print(f"  成本: {cost:.2f} 元")
                print(f"  现价: {current_price:.2f} 元")
                print(f"  盈亏: {profit:.2f} 元 ({profit_rate:+.2f}%)")

        position_value = sum(current_prices.get(s, 0) * p['amount'] for s, p in self.engine.positions.items())
        total_value = self.engine.cash + position_value
        total_return = (total_value / self.engine.initial_cash - 1) * 100

        print(f"\n{'=' * 60}")
        print(f"账户总值: {total_value:,.2f} 元")
        print(f"可用资金: {self.engine.cash:,.2f} 元")
        print(f"持仓市值: {position_value:,.2f} 元")
        print(f"累计收益: {total_return:+.2f}%")
        print(f"{'=' * 60}")

        if self.engine.db:
            self.engine.db.save_account_status(now, total_value, self.engine.cash, position_value, total_return)


class LiveTrading:
    """实盘交易调度器"""

    def __init__(self, strategy: HighDividendStrategy):
        self.strategy = strategy
        self.is_running = False

    def setup_schedule(self):
        schedule.every().day.at("09:05").do(self.strategy.prepare_stock_list)
        schedule.every().monday.at("09:30").do(self.strategy.weekly_adjustment)
        schedule.every().day.at("14:00").do(self.strategy.check_limit_up)
        schedule.every().day.at("15:10").do(self.strategy.print_position_info)

        print("\n定时任务已设置:")
        print("  09:05 - 准备股票池")
        print("  09:30(周一) - 周度调仓")
        print("  14:00 - 检查涨停")
        print("  15:10 - 打印持仓信息")

    def start(self):
        self.is_running = True
        self.setup_schedule()

        print("\n实盘交易系统已启动...")
        print("按 Ctrl+C 停止运行\n")

        def run_scheduler():
            while self.is_running:
                schedule.run_pending()
                time.sleep(1)

        thread = threading.Thread(target=run_scheduler, daemon=True)
        thread.start()

        try:
            while self.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在停止实盘交易系统...")
            self.is_running = False

    def stop(self):
        self.is_running = False


def run_backtest(start_date: str, end_date: str, initial_cash=1000000, db_config: Optional[MySQLConfig] = None):
    print("=" * 60)
    print("高股息低杠杆小市值轮动策略回测")
    print(f"回测期间: {start_date} 至 {end_date}")
    print(f"初始资金: {initial_cash:,.0f} 元")
    print("=" * 60)

    db_manager = DatabaseManager(db_config or MySQLConfig())
    data_provider = DataProvider(db_manager, request_delay=0.2)
    engine = BacktestEngine(initial_cash=initial_cash, db_manager=db_manager)
    strategy = HighDividendStrategy(engine, data_provider, db_manager)

    current = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')

    while current <= end:
        if current.weekday() < 5:
            strategy.prepare_stock_list()

            if current.weekday() == 0:
                target_stocks = strategy.select_stocks(current)
                all_stocks = list(set(list(engine.positions.keys()) + target_stocks))
                prices = data_provider.batch_get_prices_on_date(all_stocks, current)
                strategy.weekly_adjustment(current_date=current, current_prices=prices)

            if engine.positions:
                prices = data_provider.batch_get_prices_on_date(list(engine.positions.keys()), current)
            else:
                prices = {}

            portfolio_value = engine.get_portfolio_value(prices)
            position_value = portfolio_value - engine.cash
            total_return = (portfolio_value / engine.initial_cash - 1) * 100

            if engine.db:
                engine.db.save_account_status(current, portfolio_value, engine.cash, position_value, total_return)

            if current.day % 20 == 0:
                print(
                    f"{current.date()} 资产: {portfolio_value:,.2f} 元, "
                    f"收益: {total_return:+.2f}%"
                )

        current += timedelta(days=1)

    print("\n回测完成!")
    account_history = db_manager.get_account_history()
    if not account_history.empty:
        final = account_history.iloc[-1]
        print(
            f"最终资产: {final['total_value']:,.2f} 元, "
            f"累计收益: {final['returns']:+.2f}%"
        )


def run_live(initial_cash=1000000, db_config: Optional[MySQLConfig] = None):
    db_manager = DatabaseManager(db_config or MySQLConfig())
    data_provider = DataProvider(db_manager)
    engine = BacktestEngine(initial_cash=initial_cash, db_manager=db_manager)
    engine.positions = db_manager.load_positions()
    strategy = HighDividendStrategy(engine, data_provider, db_manager)
    trader = LiveTrading(strategy)
    trader.start()


def run_sync_history(start_date: str, end_date: str, db_config: Optional[MySQLConfig] = None):
    db_manager = DatabaseManager(db_config or MySQLConfig())
    syncer = DataSyncer(db_manager)
    syncer.sync_stock_basic()
    stock_codes = syncer.get_all_stocks()
    syncer.sync_stock_daily_history(stock_codes, datetime.strptime(start_date, '%Y-%m-%d'), datetime.strptime(end_date, '%Y-%m-%d'))
    syncer.sync_dividend_history(stock_codes)
    syncer.sync_financial_snapshot(stock_codes, datetime.strptime(end_date, '%Y-%m-%d'))


def run_sync_daily(db_config: Optional[MySQLConfig] = None):
    db_manager = DatabaseManager(db_config or MySQLConfig())
    syncer = DataSyncer(db_manager)
    syncer.sync_stock_basic()
    stock_codes = syncer.get_all_stocks()
    today = datetime.now()
    syncer.sync_stock_daily_incremental(stock_codes, today)
    syncer.sync_dividend_history(stock_codes)
    syncer.sync_financial_snapshot(stock_codes, today)


def parse_args() -> argparse.Namespace:
    defaults = MySQLConfig()
    parser = argparse.ArgumentParser(description='高股息低杠杆小市值轮动策略')
    parser.add_argument('--mode', choices=['backtest', 'live', 'sync-history', 'sync-daily'], default='backtest', help='运行模式')
    parser.add_argument('--start', default='2020-01-01', help='回测/历史同步开始日期')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'), help='回测/历史同步结束日期')
    parser.add_argument('--cash', type=float, default=1000000, help='初始资金')
    parser.add_argument('--db-host', default=defaults.host, help='MySQL host')
    parser.add_argument('--db-port', type=int, default=defaults.port, help='MySQL port')
    parser.add_argument('--db-user', default=defaults.user, help='MySQL user')
    parser.add_argument('--db-password', default=defaults.password, help='MySQL password')
    parser.add_argument('--db-name', default=defaults.database, help='MySQL database')
    return parser.parse_args()


def main():
    args = parse_args()
    db_config = MySQLConfig(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        database=args.db_name,
    )

    if args.mode == 'backtest':
        run_backtest(args.start, args.end, initial_cash=args.cash, db_config=db_config)
    elif args.mode == 'live':
        run_live(initial_cash=args.cash, db_config=db_config)
    elif args.mode == 'sync-history':
        run_sync_history(args.start, args.end, db_config=db_config)
    else:
        run_sync_daily(db_config=db_config)


if __name__ == '__main__':
    main()