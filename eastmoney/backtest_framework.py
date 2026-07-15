"""
A股超跌反弹策略 - 回测框架
包含数据接口、回测引擎和绩效分析
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from matplotlib import font_manager
import warnings
warnings.filterwarnings('ignore')

# 设置中文显示
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


import pymysql
from sqlalchemy import create_engine
from scoreRank.core.db_config import build_pymysql_config, require_sqlalchemy_url

class DataInterface:
    """数据接口类 - 对接真实数据库 (MySQL)"""
    
    def __init__(self):
        # 安全敏感入口只接受显式 URL；同一账户访问两个逻辑库。
        require_sqlalchemy_url(database="chenyiyun")
        self.db_config = build_pymysql_config(dict_cursor=False)
        self.engine_str = require_sqlalchemy_url(database="chenyiyun")
        self.ts_engine_str = require_sqlalchemy_url(database="tushare_stock")
    
    def get_stock_basic_info(self, stock_code):
        """获取股票基本信息 (从 chenyiyun.a_share_stock_list)"""
        sql = f"SELECT stock_code, stock_name, exchange, list_date, is_active FROM chenyiyun.a_share_stock_list WHERE stock_code = '{stock_code}'"
        try:
            with pymysql.connect(**self.db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    row = cursor.fetchone()
                    if row:
                        return {
                            'code': row[0],
                            'name': row[1],
                            'market': row[2],  # Exchange as market
                            'industry': '',    # Industry not in this table
                            'list_date': str(row[3]) if row[3] else '',
                            'ts_code': f"{row[0]}.{row[2]}" if row[2] else None
                        }
        except Exception as e:
            print(f"获取基本信息失败 {stock_code}: {e}")
        
        return {'code': stock_code, 'name': stock_code, 'market': '', 'industry': '', 'list_date': ''}
    
    def get_sentiment_data(self, stock_code, date=None):
        """获取东方财富多空情绪数据 (从 chenyiyun.em_duokong_sentiment)"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
            
        sql = f"""
        SELECT bulls_percent, bears_percent, bulls_votes, bears_votes 
        FROM chenyiyun.em_duokong_sentiment 
        WHERE stock_code = '{stock_code}' AND trade_date <= '{date}'
        ORDER BY trade_date DESC LIMIT 1
        """
        try:
            with pymysql.connect(**self.db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    row = cursor.fetchone()
                    if row:
                        return {
                            'code': stock_code,
                            'date': date,
                            'bearish_ratio': float(row[1]),
                            'bullish_ratio': float(row[0]), 
                            'total_votes': (row[2] or 0) + (row[3] or 0)
                        }
        except Exception as e:
            pass
            
        return {'code': stock_code, 'date': date, 'bearish_ratio': 50, 'bullish_ratio': 50, 'total_votes': 0}

    def get_price_data(self, stock_code, start_date, end_date):
        """获取股票价格数据 (从 tushare_stock.dwd_stock_daily_standard)"""
        # 1. Get ts_code
        basic = self.get_stock_basic_info(stock_code)
        ts_code = basic.get('ts_code')
        
        if not ts_code:
            # Fallback guessing if look up failed
            ts_code = f"{stock_code}.SZ" if stock_code.startswith(('00', '30')) else f"{stock_code}.SH"
            if stock_code.startswith(('4', '8')): ts_code = f"{stock_code}.BJ"

        # 2. Date conversion
        start_int = int(start_date.replace('-', ''))
        end_int = int(end_date.replace('-', ''))

        sql = f"""
        SELECT trade_date, adj_open, adj_high, adj_low, adj_close, vol, amount
        FROM tushare_stock.dwd_stock_daily_standard 
        WHERE ts_code = '{ts_code}' AND trade_date >= {start_int} AND trade_date <= {end_int}
        ORDER BY trade_date ASC
        """
        try:
            engine = create_engine(self.ts_engine_str)
            df = pd.read_sql(sql, engine)
            if not df.empty:
                df = df.rename(columns={
                    'trade_date': 'date',
                    'adj_open': 'open',
                    'adj_high': 'high',
                    'adj_low': 'low',
                    'adj_close': 'close',
                    'vol': 'volume'
                })
                df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
                return df
        except Exception as e:
            print(f"获取价格数据失败 {stock_code}: {e}")
            
        return pd.DataFrame()

    def get_chip_distribution(self, stock_code, date=None):
        """
        获取筹码分布数据 (简易估算版 - Cost Distribution Model)
        由于缺乏真实筹码数据，暂返回默认值或简单的价格区间统计
        """
        # TODO: Implement a real cost distribution model based on historical volume/price
        return {
            'code': stock_code,
            'date': date or datetime.now().strftime('%Y-%m-%d'),
            'concentration': 15.0,  # 默认值
            'profit_ratio': 10.0,
            'avg_cost': 0,
            'peak_price': 0,
            'upper_peak_exists': False,
            'peak_shift': 0,
            'chip_distribution': {}
        }
    
    def get_financial_data(self, stock_code):
        """获取财务数据 (需补充 tushare_stock 表结构，暂返回默认安全值)"""
        # TODO: Implement real financial data query
        return {
            'code': stock_code,
            'avg_profit_3y': 100_000_000, 
            'total_dividend_3y': 50_000_000,
            'roe': 10,
            'debt_ratio': 50,
            'is_st': 'ST' in stock_code.upper(), # Simple check
            'has_fraud_concern': False,
            'buyback_amount': 0
        }
    
    def get_market_data(self, index_code='000001', start_date=None, end_date=None):
        return self.get_price_data(index_code, start_date, end_date)


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, strategy, initial_capital=1000000):
        """
        初始化回测引擎
        
        参数:
            strategy: 策略实例
            initial_capital: 初始资金
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.positions = {}  # 持仓
        self.trades = []  # 交易记录
        self.daily_values = []  # 每日账户价值
        
    def run_backtest(self, stock_pool, start_date, end_date, max_positions=5):
        """
        运行回测
        
        参数:
            stock_pool: list, 股票代码列表
            start_date: str, 开始日期
            end_date: str, 结束日期
            max_positions: int, 最大持仓数量
        """
        print(f"\n开始回测: {start_date} 至 {end_date}")
        print(f"初始资金: {self.initial_capital:,.0f} 元")
        print(f"股票池数量: {len(stock_pool)}")
        print(f"最大持仓数: {max_positions}")
        print("=" * 60)
        
        data_interface = DataInterface()
        
        # 按日期遍历
        current_date = pd.to_datetime(start_date)
        end_date = pd.to_datetime(end_date)
        
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            
            # 更新持仓
            self._update_positions(data_interface, date_str)
            
            # 检查出场信号
            self._check_exit_signals(data_interface, date_str)
            
            # 如果持仓未满，寻找入场机会
            if len(self.positions) < max_positions:
                self._find_entry_opportunities(
                    data_interface, stock_pool, date_str, max_positions
                )
            
            # 记录每日账户价值
            total_value = self._calculate_total_value(data_interface, date_str)
            self.daily_values.append({
                'date': date_str,
                'cash': self.current_capital,
                'market_value': total_value - self.current_capital,
                'total_value': total_value
            })
            
            current_date += timedelta(days=1)
        
        print("\n回测完成!")
        return self._generate_backtest_report()
    
    def _update_positions(self, data_interface, date):
        """更新持仓市值"""
        for code in list(self.positions.keys()):
            df = data_interface.get_price_data(
                code, 
                (pd.to_datetime(date) - timedelta(days=30)).strftime('%Y-%m-%d'),
                date
            )
            if len(df) > 0:
                current_price = df.iloc[-1]['close']
                self.positions[code]['current_price'] = current_price
                self.positions[code]['market_value'] = (
                    current_price * self.positions[code]['shares']
                )
                self.positions[code]['return'] = (
                    current_price / self.positions[code]['entry_price'] - 1
                ) * 100
    
    def _check_exit_signals(self, data_interface, date):
        """检查出场信号"""
        for code in list(self.positions.keys()):
            position = self.positions[code]
            
            # 获取最近数据
            df = data_interface.get_price_data(
                code,
                (pd.to_datetime(date) - timedelta(days=60)).strftime('%Y-%m-%d'),
                date
            )
            
            chip_data = data_interface.get_chip_distribution(code, date)
            
            # 生成出场信号
            exit_signal = self.strategy.generate_exit_signals(
                df, chip_data, position['entry_price']
            )
            
            # 执行出场
            if exit_signal['action'] in ['TAKE_PROFIT', 'STOP_LOSS']:
                self._close_position(code, date, exit_signal)
    
    def _find_entry_opportunities(self, data_interface, stock_pool, date, max_positions):
        """寻找入场机会"""
        candidates = []
        
        for code in stock_pool:
            # 如果已持仓，跳过
            if code in self.positions:
                continue
            
            # 获取数据
            df = data_interface.get_price_data(
                code,
                (pd.to_datetime(date) - timedelta(days=60)).strftime('%Y-%m-%d'),
                date
            )
            
            if len(df) < 30:
                continue
            
            # 情绪筛选
            sentiment = data_interface.get_sentiment_data(code, date)
            if sentiment['bearish_ratio'] < self.strategy.bearish_threshold:
                continue
            
            # 技术分析
            oversold = self.strategy.identify_oversold(df)
            if not oversold['IS_OVERSOLD']:
                continue
            
            # 筹码分析
            chip_data = data_interface.get_chip_distribution(code, date)
            chip_analysis = self.strategy.analyze_chip_distribution(chip_data)
            
            # 风险过滤
            financial_data = data_interface.get_financial_data(code)
            st_filter = self.strategy.filter_st_risk(financial_data)
            fraud_filter = self.strategy.filter_financial_fraud(financial_data)
            
            if not (st_filter['PASS_ST_FILTER'] and fraud_filter['PASS_FRAUD_FILTER']):
                continue
            
            # 入场信号
            entry_signals = self.strategy.generate_entry_signals(df, chip_data)
            
            if len(entry_signals) > 0:
                # 计算综合得分
                stock_data = {
                    'code': code,
                    'CHIP_QUALITY_SCORE': chip_analysis['CHIP_QUALITY_SCORE'],
                    'PROFIT_RATIO': chip_data['profit_ratio'],
                    'OVERSOLD_SCORE': oversold['OVERSOLD_SCORE'],
                    'VOLUME_SCORE': 70,  # 简化处理
                }
                
                score = self.strategy.calculate_bounce_probability(stock_data)
                
                candidates.append({
                    'code': code,
                    'score': score,
                    'price': df.iloc[-1]['close'],
                    'signal': entry_signals[0]
                })
        
        # 按得分排序，选择前N个
        candidates.sort(key=lambda x: x['score'], reverse=True)
        available_slots = max_positions - len(self.positions)
        
        for candidate in candidates[:available_slots]:
            self._open_position(candidate, date)
    
    def _open_position(self, candidate, date):
        """开仓"""
        # 计算仓位（等权重）
        position_size = self.current_capital * 0.2  # 单仓位20%资金
        shares = int(position_size / candidate['price'] / 100) * 100  # 整百股
        cost = shares * candidate['price']
        
        if cost > self.current_capital:
            return
        
        self.positions[candidate['code']] = {
            'entry_date': date,
            'entry_price': candidate['price'],
            'shares': shares,
            'cost': cost,
            'current_price': candidate['price'],
            'market_value': cost,
            'return': 0,
            'signal': candidate['signal']['type']
        }
        
        self.current_capital -= cost
        
        self.trades.append({
            'date': date,
            'code': candidate['code'],
            'action': 'BUY',
            'price': candidate['price'],
            'shares': shares,
            'amount': cost,
            'score': candidate['score']
        })
        
        print(f"[{date}] 买入 {candidate['code']} "
              f"价格:{candidate['price']:.2f} "
              f"数量:{shares} "
              f"得分:{candidate['score']:.2f}")
    
    def _close_position(self, code, date, exit_signal):
        """平仓"""
        position = self.positions[code]
        
        sell_amount = position['market_value']
        self.current_capital += sell_amount
        
        profit = sell_amount - position['cost']
        profit_pct = position['return']
        
        self.trades.append({
            'date': date,
            'code': code,
            'action': 'SELL',
            'price': position['current_price'],
            'shares': position['shares'],
            'amount': sell_amount,
            'profit': profit,
            'profit_pct': profit_pct,
            'reason': exit_signal['reason']
        })
        
        print(f"[{date}] 卖出 {code} "
              f"价格:{position['current_price']:.2f} "
              f"收益率:{profit_pct:.2f}% "
              f"原因:{exit_signal['reason']}")
        
        del self.positions[code]
    
    def _calculate_total_value(self, data_interface, date):
        """计算总资产"""
        total = self.current_capital
        for position in self.positions.values():
            total += position['market_value']
        return total
    
    def _generate_backtest_report(self):
        """生成回测报告"""
        df_values = pd.DataFrame(self.daily_values)
        df_trades = pd.DataFrame(self.trades)
        
        # 计算绩效指标
        final_value = df_values.iloc[-1]['total_value']
        total_return = (final_value / self.initial_capital - 1) * 100
        
        # 计算日收益率
        df_values['daily_return'] = df_values['total_value'].pct_change()
        
        # 年化收益率
        days = len(df_values)
        annual_return = ((final_value / self.initial_capital) ** (252 / days) - 1) * 100
        
        # 最大回撤
        df_values['cummax'] = df_values['total_value'].cummax()
        df_values['drawdown'] = (df_values['total_value'] / df_values['cummax'] - 1) * 100
        max_drawdown = df_values['drawdown'].min()
        
        # 夏普比率
        risk_free_rate = 0.03 / 252  # 年化3%的无风险利率
        sharpe_ratio = (df_values['daily_return'].mean() - risk_free_rate) / df_values['daily_return'].std() * np.sqrt(252)
        
        # 胜率
        winning_trades = df_trades[df_trades['action'] == 'SELL']
        if len(winning_trades) > 0:
            win_rate = (winning_trades['profit'] > 0).sum() / len(winning_trades) * 100
            avg_profit = winning_trades[winning_trades['profit'] > 0]['profit_pct'].mean()
            avg_loss = winning_trades[winning_trades['profit'] <= 0]['profit_pct'].mean()
        else:
            win_rate = 0
            avg_profit = 0
            avg_loss = 0
        
        report = {
            'initial_capital': self.initial_capital,
            'final_value': final_value,
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'total_trades': len(df_trades[df_trades['action'] == 'BUY']),
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'daily_values': df_values,
            'trades': df_trades
        }
        
        return report


class PerformanceAnalyzer:
    """绩效分析器"""
    
    @staticmethod
    def print_report(report):
        """打印回测报告"""
        print("\n" + "=" * 60)
        print("回测绩效报告")
        print("=" * 60)
        
        print(f"\n【资金情况】")
        print(f"初始资金: {report['initial_capital']:,.0f} 元")
        print(f"最终资金: {report['final_value']:,.0f} 元")
        print(f"总收益率: {report['total_return']:.2f}%")
        print(f"年化收益率: {report['annual_return']:.2f}%")
        
        print(f"\n【风险指标】")
        print(f"最大回撤: {report['max_drawdown']:.2f}%")
        print(f"夏普比率: {report['sharpe_ratio']:.2f}")
        
        print(f"\n【交易统计】")
        print(f"总交易次数: {report['total_trades']}")
        print(f"胜率: {report['win_rate']:.2f}%")
        print(f"平均盈利: {report['avg_profit']:.2f}%")
        print(f"平均亏损: {report['avg_loss']:.2f}%")
        
        print("\n" + "=" * 60)
    
    @staticmethod
    def plot_equity_curve(report, save_path=None):
        """绘制资金曲线"""
        df = report['daily_values']
        
        fig, axes = plt.subplots(2, 1, figsize=(12, 8))
        
        # 资金曲线
        axes[0].plot(pd.to_datetime(df['date']), df['total_value'], label='账户总值', linewidth=2)
        axes[0].axhline(y=report['initial_capital'], color='r', linestyle='--', label='初始资金', alpha=0.7)
        axes[0].set_title('账户资金曲线', fontsize=14, fontweight='bold')
        axes[0].set_ylabel('资金(元)', fontsize=12)
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)
        
        # 回撤曲线
        axes[1].fill_between(pd.to_datetime(df['date']), df['drawdown'], 0, 
                             color='red', alpha=0.3, label='回撤')
        axes[1].set_title('回撤曲线', fontsize=14, fontweight='bold')
        axes[1].set_xlabel('日期', fontsize=12)
        axes[1].set_ylabel('回撤(%)', fontsize=12)
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"\n图表已保存至: {save_path}")
        else:
            plt.show()
    
    @staticmethod
    def plot_trade_analysis(report, save_path=None):
        """绘制交易分析图"""
        df = report['trades']
        sells = df[df['action'] == 'SELL'].copy()
        
        if len(sells) == 0:
            print("没有完成的交易，无法绘制交易分析图")
            return
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        # 收益分布
        axes[0, 0].hist(sells['profit_pct'], bins=20, color='steelblue', alpha=0.7, edgecolor='black')
        axes[0, 0].axvline(x=0, color='red', linestyle='--', linewidth=2)
        axes[0, 0].set_title('单笔交易收益率分布', fontsize=12, fontweight='bold')
        axes[0, 0].set_xlabel('收益率(%)', fontsize=10)
        axes[0, 0].set_ylabel('次数', fontsize=10)
        axes[0, 0].grid(True, alpha=0.3)
        
        # 盈亏金额
        colors = ['green' if x > 0 else 'red' for x in sells['profit']]
        axes[0, 1].bar(range(len(sells)), sells['profit'], color=colors, alpha=0.7)
        axes[0, 1].axhline(y=0, color='black', linestyle='-', linewidth=1)
        axes[0, 1].set_title('每笔交易盈亏金额', fontsize=12, fontweight='bold')
        axes[0, 1].set_xlabel('交易序号', fontsize=10)
        axes[0, 1].set_ylabel('盈亏(元)', fontsize=10)
        axes[0, 1].grid(True, alpha=0.3)
        
        # 累计收益
        sells['cumulative_profit'] = sells['profit'].cumsum()
        axes[1, 0].plot(range(len(sells)), sells['cumulative_profit'], 
                       marker='o', linewidth=2, markersize=4, color='darkgreen')
        axes[1, 0].fill_between(range(len(sells)), sells['cumulative_profit'], 0, alpha=0.3, color='lightgreen')
        axes[1, 0].set_title('累计盈亏曲线', fontsize=12, fontweight='bold')
        axes[1, 0].set_xlabel('交易序号', fontsize=10)
        axes[1, 0].set_ylabel('累计盈亏(元)', fontsize=10)
        axes[1, 0].grid(True, alpha=0.3)
        
        # 出场原因分析
        if 'reason' in sells.columns:
            reason_counts = sells['reason'].value_counts()
            axes[1, 1].pie(reason_counts.values, labels=reason_counts.index, autopct='%1.1f%%',
                          startangle=90, colors=plt.cm.Set3.colors)
            axes[1, 1].set_title('出场原因分布', fontsize=12, fontweight='bold')
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"交易分析图已保存至: {save_path}")
        else:
            plt.show()


# ==================== 使用示例 ====================

def run_full_backtest():
    """运行完整回测示例"""
    from oversold_bounce_strategy import OversoldBounceStrategy
    
    # 初始化策略
    strategy = OversoldBounceStrategy()
    
    # 初始化回测引擎
    engine = BacktestEngine(strategy, initial_capital=1_000_000)
    
    # 定义股票池（示例）
    stock_pool = [
        '000001', '000002', '000003', '000004', '000005',
        '000006', '000007', '000008', '000009', '000010'
    ]
    
    # 运行回测
    report = engine.run_backtest(
        stock_pool=stock_pool,
        start_date='2024-01-01',
        end_date='2024-12-31',
        max_positions=5
    )
    
    # 打印报告
    PerformanceAnalyzer.print_report(report)
    
    # 绘制图表
    PerformanceAnalyzer.plot_equity_curve(report, save_path='/home/claude/equity_curve.png')
    PerformanceAnalyzer.plot_trade_analysis(report, save_path='/home/claude/trade_analysis.png')
    
    return report


if __name__ == "__main__":
    print("A股超跌反弹策略 - 回测系统")
    print("=" * 60)
    
    report = run_full_backtest()
    
    print("\n回测系统运行完成!")
