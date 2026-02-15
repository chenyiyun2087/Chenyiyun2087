"""
示例运行脚本 - 演示如何使用DailyReviewEngine

这个脚本演示了:
1. 如何准备数据
2. 如何运行每日复盘
3. 如何查看结果
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# 添加src目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.engine import DailyReviewEngine


def generate_mock_data(symbols: list, days: int = 150) -> dict:
    """
    生成模拟数据(仅用于演示)
    
    实际使用时,应该从真实数据源获取数据,例如:
    - AkShare: import akshare as ak
    - Tushare: import tushare as ts
    - 本地数据库
    
    Args:
        symbols: 股票代码列表
        days: 生成天数
    
    Returns:
        {symbol: DataFrame} 字典
    """
    data_dict = {}
    
    # 生成日期序列
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    dates = pd.date_range(start=start_date, end=end_date, freq='B')
    
    for symbol in symbols:
        # 生成随机价格数据
        np.random.seed(hash(symbol) % 2**32)
        
        base_price = np.random.uniform(10, 100)
        returns = np.random.randn(len(dates)) * 0.02
        prices = base_price * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame({
            'open': prices * (1 + np.random.randn(len(dates)) * 0.01),
            'high': prices * (1 + np.abs(np.random.randn(len(dates))) * 0.02),
            'low': prices * (1 - np.abs(np.random.randn(len(dates))) * 0.02),
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, len(dates)),
            'amount': prices * np.random.randint(1000000, 10000000, len(dates))
        }, index=dates)
        
        # 确保high >= close >= low
        df['high'] = df[['high', 'close']].max(axis=1)
        df['low'] = df[['low', 'close']].min(axis=1)
        
        # 将索引转换为字符串格式
        df.index = df.index.strftime('%Y-%m-%d')
        
        data_dict[symbol] = df
    
    return data_dict


def example_single_day_review():
    """示例1: 单日复盘"""
    print("\n" + "="*60)
    print("示例1: 单日收盘复盘")
    print("="*60 + "\n")
    
    # 1. 准备数据
    print("准备模拟数据...")
    symbols = [f'{i:06d}.SH' for i in range(600000, 600020)]  # 20只股票
    market_data = generate_mock_data(symbols, days=150)
    
    # 2. 初始化引擎
    print("初始化引擎...")
    engine = DailyReviewEngine('configs/config.yaml')
    
    # 3. 运行复盘
    trade_date = datetime.now().strftime('%Y-%m-%d')
    results = engine.run_daily_review(trade_date, market_data)
    
    # 4. 查看结果
    print("\n" + "="*60)
    print("复盘结果:")
    print("="*60 + "\n")
    
    print(f"Trade候选 ({len(results['trade'])} 只):")
    if len(results['trade']) > 0:
        print(results['trade'][['symbol', 'score_adjusted', 'ret_since_in']].head(10))
    else:
        print("无")
    
    print(f"\nWatch观察 ({len(results['watch'])} 只):")
    if len(results['watch']) > 0:
        print(results['watch'][['symbol', 'score_adjusted', 'ret_since_in']].head(10))
    else:
        print("无")
    
    return engine, results


def example_multi_day_review():
    """示例2: 多日连续复盘"""
    print("\n" + "="*60)
    print("示例2: 多日连续复盘")
    print("="*60 + "\n")
    
    # 1. 准备数据
    print("准备模拟数据...")
    symbols = [f'{i:06d}.SH' for i in range(600000, 600050)]  # 50只股票
    market_data = generate_mock_data(symbols, days=150)
    
    # 2. 初始化引擎
    print("初始化引擎...")
    engine = DailyReviewEngine('configs/config.yaml')
    
    # 3. 连续5天复盘
    end_date = datetime.now()
    for i in range(5, 0, -1):
        trade_date = (end_date - timedelta(days=i)).strftime('%Y-%m-%d')
        
        # 过滤非交易日(简化处理)
        weekday = (end_date - timedelta(days=i)).weekday()
        if weekday >= 5:  # 周末跳过
            continue
        
        print(f"\n{'*'*60}")
        print(f"处理日期: {trade_date}")
        print(f"{'*'*60}")
        
        try:
            results = engine.run_daily_review(trade_date, market_data)
            
            print(f"本日新增: {len([s for s in results['inventory']['in_date'] if s == trade_date])} 只")
            print(f"库内总数: {len(results['inventory'])} 只")
            
        except Exception as e:
            print(f"处理失败: {e}")
            continue
    
    return engine


def example_with_real_data():
    """示例3: 使用真实数据(需要安装akshare)"""
    print("\n" + "="*60)
    print("示例3: 使用真实数据")
    print("="*60 + "\n")
    
    try:
        import akshare as ak
    except ImportError:
        print("需要安装akshare: pip install akshare")
        return
    
    # 获取沪深300成分股
    print("获取沪深300成分股...")
    stock_list = ak.index_stock_cons(symbol="000300")
    symbols = stock_list['品种代码'].head(20).tolist()  # 只取前20只
    
    # 获取历史数据
    print("获取历史数据...")
    market_data = {}
    
    for symbol in symbols:
        try:
            # 获取日线数据
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date="20230101",
                end_date=datetime.now().strftime('%Y%m%d'),
                adjust="qfq"
            )
            
            # 重命名列
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount'
            })
            
            df = df.set_index('date')
            market_data[symbol] = df
            
            print(f"获取 {symbol} 成功")
            
        except Exception as e:
            print(f"获取 {symbol} 失败: {e}")
            continue
    
    if len(market_data) == 0:
        print("没有成功获取任何数据")
        return
    
    # 运行复盘
    print("\n运行复盘...")
    engine = DailyReviewEngine('configs/config.yaml')
    
    trade_date = datetime.now().strftime('%Y-%m-%d')
    results = engine.run_daily_review(trade_date, market_data)
    
    return engine, results


if __name__ == '__main__':
    print("\n" + "="*60)
    print("每日收盘复盘系统 - 示例运行")
    print("="*60)
    
    # 运行示例1: 单日复盘
    engine1, results1 = example_single_day_review()
    
    # 运行示例2: 多日复盘
    # engine2 = example_multi_day_review()
    
    # 运行示例3: 真实数据(取消注释以使用)
    # engine3, results3 = example_with_real_data()
    
    print("\n" + "="*60)
    print("所有示例运行完成!")
    print("结果已保存到 ./outputs/daily/ 目录")
    print("="*60 + "\n")
