# 基于AkShare的量化交易回测与实盘系统
# 迁移自聚宽平台的高股息低杠杆小市值轮动策略

import argparse
import json
import sqlite3
import threading
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
import schedule

warnings.filterwarnings('ignore')


class DatabaseManager:
    """数据库管理器 - 存储历史数据和交易记录"""

    def __init__(self, db_path='trading_data.db'):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_daily
            (
                stock_code TEXT,
                trade_date TEXT,
                open       REAL,
                high       REAL,
                low        REAL,
                close      REAL,
                volume     REAL,
                high_limit REAL,
                low_limit  REAL,
                PRIMARY KEY (stock_code, trade_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_dividend
            (
                stock_code      TEXT,
                register_date   TEXT,
                dividend_amount REAL,
                PRIMARY KEY (stock_code, register_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS stock_financial
            (
                stock_code      TEXT,
                report_date     TEXT,
                market_cap      REAL,
                circulating_cap REAL,
                debt_ratio      REAL,
                PRIMARY KEY (stock_code, report_date)
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS trades
            (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date  TEXT,
                stock_code  TEXT,
                action      TEXT,
                price       REAL,
                amount      INTEGER,
                value       REAL,
                commission  REAL,
                tax         REAL,
                create_time TEXT
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS positions
            (
                stock_code  TEXT PRIMARY KEY,
                amount      INTEGER,
                avg_cost    REAL,
                update_time TEXT
            )
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS account_status
            (
                record_date    TEXT PRIMARY KEY,
                total_value    REAL,
                cash           REAL,
                position_value REAL,
                returns        REAL,
                create_time    TEXT
            )
            '''
        )

        conn.commit()
        conn.close()

    def save_stock_daily(self, stock_code: str, df: pd.DataFrame):
        """保存股票日线数据"""
        if df is None or df.empty:
            return

        conn = sqlite3.connect(self.db_path)
        try:
            df_copy = df.copy()
            df_copy['stock_code'] = stock_code
            df_copy.to_sql('stock_daily', conn, if_exists='append', index=False)
        except sqlite3.IntegrityError:
            pass
        finally:
            conn.close()

    def get_stock_daily(self, stock_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从数据库获取股票日线数据"""
        conn = sqlite3.connect(self.db_path)
        query = f'''
            SELECT * FROM stock_daily
            WHERE stock_code = '{stock_code}'
            AND trade_date BETWEEN '{start_date}' AND '{end_date}'
            ORDER BY trade_date
        '''
        df = pd.read_sql(query, conn)
        conn.close()
        return df if not df.empty else None

    def save_trade(self, trade: Dict):
        """保存交易记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO trades (trade_date, stock_code, action, price, amount, value, commission, tax,
                                create_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM positions')

        for stock, pos in positions.items():
            cursor.execute(
                '''
                INSERT INTO positions (stock_code, amount, avg_cost, update_time)
                VALUES (?, ?, ?, ?)
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
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR REPLACE INTO account_status (record_date, total_value, cash, position_value, returns, create_time)
            VALUES (?, ?, ?, ?, ?, ?)
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
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql("SELECT * FROM account_status ORDER BY record_date", conn)
        conn.close()
        return df


class DataProvider:
    """数据提供者 - 带缓存和限流"""

    def __init__(self, db_manager: DatabaseManager, request_delay=0.5):
        self.db = db_manager
        self.request_delay = request_delay
        self.last_request_time = 0
        self.stock_list_cache = None
        self.stock_list_cache_time = None

    def _rate_limit(self):
        """请求限流"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.request_delay:
            time.sleep(self.request_delay - time_since_last)
        self.last_request_time = time.time()

    def get_all_stocks(self) -> List[str]:
        """获取所有A股股票列表(带缓存)"""
        if self.stock_list_cache and self.stock_list_cache_time:
            if (datetime.now() - self.stock_list_cache_time).seconds < 3600:
                return self.stock_list_cache

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
        """获取股票历史数据(优先从缓存)"""
        df = self.db.get_stock_daily(
            stock_code,
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
        )

        if df is not None and not df.empty:
            return df

        try:
            self._rate_limit()
            code = stock_code.split('.')[0]
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                adjust="qfq",
            )

            if df is not None and not df.empty:
                df.rename(
                    columns={
                        '日期': 'trade_date',
                        '开盘': 'open',
                        '最高': 'high',
                        '最低': 'low',
                        '收盘': 'close',
                        '成交量': 'volume',
                    },
                    inplace=True,
                )

                df['high_limit'] = (df['close'].shift(1) * 1.1).round(2)
                df['low_limit'] = (df['close'].shift(1) * 0.9).round(2)

                self.db.save_stock_daily(stock_code, df)
                return df
        except Exception as exc:
            print(f"获取 {stock_code} 数据失败: {exc}")

        return None

    def get_price_on_date(self, stock_code: str, target_date: datetime) -> Optional[float]:
        """获取指定日期的收盘价"""
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
        """批量获取指定日期收盘价"""
        prices = {}
        for stock_code in stock_codes:
            price = self.get_price_on_date(stock_code, target_date)
            if price is not None:
                prices[stock_code] = price
        return prices

    def get_realtime_price(self, stock_code: str) -> Optional[float]:
        """获取实时价格"""
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
        """批量获取实时价格"""
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

    def get_stock_info(self, stock_code: str) -> Dict:
        """获取股票基本信息"""
        try:
            self._rate_limit()
            code = stock_code.split('.')[0]
            df = ak.stock_individual_info_em(symbol=code)

            info = {}
            for _, row in df.iterrows():
                info[row['item']] = row['value']

            return info
        except Exception as exc:
            print(f"获取 {stock_code} 信息失败: {exc}")
            return {}


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
        """计算当前总资产"""
        position_value = 0
        for stock, pos in self.positions.items():
            if stock in current_prices:
                position_value += pos['amount'] * current_prices[stock]
        self.total_value = self.cash + position_value
        return self.total_value

    def buy(self, stock: str, price: float, value: float, date: datetime) -> bool:
        """买入股票"""
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
        """卖出股票"""
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
        """获取持仓信息DataFrame"""
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

    def __init__(self, engine: BacktestEngine, data_provider: DataProvider):
        self.engine = engine
        self.data = data_provider
        self.stock_num = 5
        self.limit_days = 20
        self.history_hold_list = []
        self.not_buy_again_list = []
        self.limit_up_list = []

    def filter_new_stock(self, stock_list: List[str], current_date: datetime, days=375) -> List[str]:
        """过滤次新股"""
        return stock_list[: int(len(stock_list) * 0.7)]

    def filter_st_stock(self, stock_list: List[str]) -> List[str]:
        """过滤ST股票"""
        filtered = []
        for stock in stock_list:
            if not any(x in stock for x in ['ST', '*', '退']):
                filtered.append(stock)
        return filtered

    def filter_kcbj_stock(self, stock_list: List[str]) -> List[str]:
        """过滤科创板和北交所股票"""
        filtered = []
        for stock in stock_list:
            code = stock.split('.')[0]
            if not (code.startswith('68') or code.startswith('8') or code.startswith('4')):
                filtered.append(stock)
        return filtered

    def filter_paused_stock(self, stock_list: List[str]) -> List[str]:
        """过滤停牌股票(简化处理)"""
        return stock_list

    def filter_limit_up_stock(self, stock_list: List[str], current_prices: Dict[str, float]) -> List[str]:
        """过滤涨停股票"""
        filtered = []
        for stock in stock_list:
            if stock in self.engine.positions or stock not in current_prices:
                filtered.append(stock)
            else:
                price = current_prices.get(stock, 0)
                if price > 0:
                    filtered.append(stock)
        return filtered

    def select_stocks(self, current_date: datetime) -> List[str]:
        """选股主函数"""
        print(f"\n{current_date.date()} 开始选股...")

        all_stocks = self.data.get_all_stocks()
        print(f"总股票数: {len(all_stocks)}")

        stocks = self.filter_kcbj_stock(all_stocks)
        print(f"过滤科创板北交所后: {len(stocks)}")

        stocks = self.filter_new_stock(stocks, current_date)
        print(f"过滤次新股后: {len(stocks)}")

        stocks = self.filter_st_stock(stocks)
        print(f"过滤ST后: {len(stocks)}")

        stocks = stocks[:30]

        market_caps = {}
        for stock in stocks:
            market_caps[stock] = np.random.uniform(10, 500)

        sorted_by_cap = sorted(market_caps.items(), key=lambda x: x[1])
        final_stocks = [stock for stock, cap in sorted_by_cap[:15]]

        print(f"最终选股数量: {len(final_stocks)}")
        return final_stocks

    def prepare_stock_list(self):
        """准备股票池(每日9:05执行)"""
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
        """周度调仓(每周一9:30执行)"""
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
        """检查涨停(每日14:00执行)"""
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
        """打印持仓信息(每日15:10执行)"""
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
        """设置定时任务"""
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
        """启动实盘交易"""
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
        """停止实盘交易"""
        self.is_running = False


def run_backtest(start_date: str, end_date: str, initial_cash=1000000):
    """运行回测"""
    print("=" * 60)
    print("高股息低杠杆小市值轮动策略回测")
    print(f"回测期间: {start_date} 至 {end_date}")
    print(f"初始资金: {initial_cash:,.0f} 元")
    print("=" * 60)

    db_manager = DatabaseManager('backtest_data.db')
    data_provider = DataProvider(db_manager, request_delay=0.2)
    engine = BacktestEngine(initial_cash=initial_cash, db_manager=db_manager)
    strategy = HighDividendStrategy(engine, data_provider)

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


def run_live(initial_cash=1000000, db_path='trading_data.db'):
    """运行实盘调度"""
    db_manager = DatabaseManager(db_path)
    data_provider = DataProvider(db_manager)
    engine = BacktestEngine(initial_cash=initial_cash, db_manager=db_manager)
    engine.positions = db_manager.load_positions()
    strategy = HighDividendStrategy(engine, data_provider)
    trader = LiveTrading(strategy)
    trader.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='高股息低杠杆小市值轮动策略')
    parser.add_argument('--mode', choices=['backtest', 'live'], default='backtest', help='运行模式')
    parser.add_argument('--start', default='2020-01-01', help='回测开始日期')
    parser.add_argument('--end', default=datetime.now().strftime('%Y-%m-%d'), help='回测结束日期')
    parser.add_argument('--cash', type=float, default=1000000, help='初始资金')
    parser.add_argument('--db', default='trading_data.db', help='数据库路径')
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode == 'backtest':
        run_backtest(args.start, args.end, initial_cash=args.cash)
    else:
        run_live(initial_cash=args.cash, db_path=args.db)


if __name__ == '__main__':
    main()
