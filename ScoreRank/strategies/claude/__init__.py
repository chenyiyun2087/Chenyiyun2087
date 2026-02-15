"""
股票每日收盘复盘系统

这是一个完整的量化交易系统,实现了:
- 六因子评分体系
- 入库/出库状态管理
- Trade/Watch分层输出
- 每日自动化复盘流程

主要模块:
- utils: 工具函数和数据结构
- indicators: 技术指标和因子计算
- signals: 买卖点信号生成
- inventory: 库状态机管理
- scoring: 评分引擎
- engine: 主执行引擎
- visualization: 可视化和报表

快速开始:
>>> from src.engine import DailyReviewEngine
>>> engine = DailyReviewEngine('configs/config.yaml')
>>> results = engine.run_daily_review(trade_date, market_data)
"""

__version__ = '1.0.0'
__author__ = 'Stock Review System'

from src.engine import DailyReviewEngine
from src.inventory import InventoryStateMachine, InventoryAnalyzer
from src.scoring import ScoringEngine, TradeWatchClassifier
from src.signals import SignalGenerator, MultiStockSignalGenerator
from src.indicators import FactorCalculator, TechnicalIndicators
from src.visualization import ChartGenerator

__all__ = [
    'DailyReviewEngine',
    'InventoryStateMachine',
    'InventoryAnalyzer',
    'ScoringEngine',
    'TradeWatchClassifier',
    'SignalGenerator',
    'MultiStockSignalGenerator',
    'FactorCalculator',
    'TechnicalIndicators',
    'ChartGenerator',
]
