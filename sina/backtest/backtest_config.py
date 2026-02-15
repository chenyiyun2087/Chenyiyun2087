"""
回测框架配置文件
backtest configuration for sina B/S strategy
"""

from typing import TypedDict, Dict

class BacktestConfig(TypedDict, total=False):
    # 数据库配置
    db_url: str
    kline_table: str
    bs_table: str
    
    # 交易配置
    initial_capital: float
    slippage: float
    commission: float
    
    # 选股配置
    top_n: int
    trade_threshold: float
    watch_threshold: float
    
    # 评分权重
    weights: Dict[str, float]
    
    # 风险扣分
    penalty: Dict[str, float]


CONFIG: BacktestConfig = {
    # 数据库配置
    "db_url": "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4",
    "kline_table": "tushare_stock.dwd_stock_daily_standard",
    "bs_table": "bs_detection_results",
    
    # 交易配置
    "initial_capital": 1_000_000.0,  # 初始资金100万
    "slippage": 0.001,               # 滑点0.1%
    "commission": 0.0015,            # 手续费0.15%（含印花税）
    
    # 选股配置
    "top_n": 10,                     # TOP N 默认10，可选5/10/15/20
    "trade_threshold": 75.0,         # 进入交易池最低分
    "watch_threshold": 60.0,         # 进入观察池最低分
    
    # 回测配置
    "lookback_days": 160,            # 计算指标的回溯天数
    "breakout_n": 20,                # 突破窗口
    
    # 评分权重（B/S用作筛选而非评分，所以移除B/S因子）
    # 权重总和应为1.0
    "weights": {
        "trend": 0.12,
        "bull_align": 0.08,
        "breakout": 0.22,
        "volume": 0.12,
        "vol_mild": 0.04,
        "rs": 0.12,
        "contraction": 0.10,
        "bias": 0.07,
        "chip": 0.03,
        "liquidity": 0.10,
    },
    
    # 风险扣分
    "penalty": {
        "suspended": 40,       # 近20天出现停牌
        "limit_up_lock": 20,   # 涨停锁死
        "st_name": 25,         # ST股票
        "negative_news": 15,   # 重大利空
    },
    
    # 因子计算参数
    "min_avg_amount20": 50_000_000,    # 最小成交额门槛（5000万）
    "bias_abs_max": 0.05,              # 乖离率上限
    "vol_mild_center": 1.5,            # 温和放量中心
    "vol_mild_half_range": 0.8,        # 温和放量半区间
    
    # 基准指数配置（用于超额收益计算）
    "benchmarks": {
        "csi300": {
            "name": "沪深300",
            "symbol": "000300",     # 沪深300指数代码
            "index_table": "index_daily",  # 指数日线表（如有）
        },
        "csi500": {
            "name": "中证500",
            "symbol": "000905",     # 中证500指数代码
            "index_table": "index_daily",
        },
    },
    
    # 无风险利率（年化，用于夏普比率计算）
    "risk_free_rate": 0.02,
}


def get_top_n_options() -> list:
    """获取TOP N可选值"""
    return [5, 10, 15, 20]


def update_top_n(n: int) -> None:
    """更新TOP N配置"""
    if n not in get_top_n_options():
        raise ValueError(f"top_n 必须是 {get_top_n_options()} 之一")
    CONFIG["top_n"] = n
