"""
实盘跟踪配置文件
Live Trading Tracker Configuration
"""

from typing import Dict, TypedDict

from scoreRank.core.db_config import require_sqlalchemy_url


class LiveTrackerConfig(TypedDict, total=False):
    # 数据库配置
    db_url: str
    
    # 账户配置
    initial_capital: float
    commission: float
    slippage: float
    
    # 持仓限制
    max_positions: int
    single_position_limit: float  # 单只股票最大仓位比例
    
    # 评分阈值（与评分系统联动）
    buy_threshold: float     # 买入信号阈值
    sell_threshold: float    # 卖出信号阈值
    watch_threshold: float   # 观察池阈值
    
    # 报告配置
    report_output_dir: str


LIVE_CONFIG: LiveTrackerConfig = {
    # 账户配置
    "initial_capital": 700_000.0,  # 初始资金70万
    "commission": 0.0015,           # 手续费0.15%（含印花税）
    "slippage": 0.001,              # 滑点0.1%
    
    # 持仓限制
    "max_positions": 10,            # 最多持仓10只股票
    
    # 行情表配置
    "price_table": "tushare_stock.dwd_stock_daily_standard",
    "single_position_limit": 0.15,  # 单只股票最大15%仓位
    
    # 评分阈值（与 backtest_config 保持一致）
    "buy_threshold": 75.0,          # 进入交易池最低分
    "sell_threshold": 60.0,         # 低于此分数考虑卖出
    "watch_threshold": 60.0,        # 进入观察池最低分
    
    # 报告配置
    "report_output_dir": "live_result",
}


def get_db_url() -> str:
    """获取显式环境配置的数据库连接 URL。"""
    return require_sqlalchemy_url(database="chenyiyun")


def get_initial_capital() -> float:
    """获取初始资金"""
    return LIVE_CONFIG["initial_capital"]


def get_position_limits() -> Dict[str, float]:
    """获取持仓限制配置"""
    return {
        "max_positions": LIVE_CONFIG["max_positions"],
        "single_position_limit": LIVE_CONFIG["single_position_limit"],
    }
