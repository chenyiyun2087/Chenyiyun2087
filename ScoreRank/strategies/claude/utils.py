"""
核心数据结构和工具函数
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


@dataclass
class InventoryRecord:
    """库存记录"""
    symbol: str
    in_date: str
    in_price: float
    pivot_price: float
    last_date: str
    last_close: float
    ret_since_in: float
    max_ret_since_in: float
    max_dd_since_in: float
    status: str  # 'in' or 'out'
    out_date: Optional[str] = None
    out_reason: Optional[str] = None


@dataclass
class SignalRecord:
    """信号记录"""
    trade_date: str
    symbol: str
    buy_signal: bool
    sell_signal: bool
    signal_reason: str
    pivot_price: Optional[float] = None


@dataclass
class ScoreRecord:
    """评分记录"""
    trade_date: str
    symbol: str
    # 原始因子值
    raw_breakout: float
    raw_trend: float
    raw_volume: float
    raw_rs: float
    raw_liquidity: float
    raw_contraction: float
    # 标准化后的分数(0-100)
    s_breakout: float
    s_trend: float
    s_volume: float
    s_rs: float
    s_liquidity: float
    s_contraction: float
    # 综合评分
    score_total: float
    trade_watch_label: str
    rank_in_inventory: int


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """
    权重归一化
    
    Args:
        weights: 原始权重字典
        
    Returns:
        归一化后的权重字典
    """
    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def rank_to_percentile(series: pd.Series, higher_better: bool = True) -> pd.Series:
    """
    将Series转换为百分位数(0-100)
    
    Args:
        series: 输入序列
        higher_better: True表示值越大越好，False表示值越小越好
        
    Returns:
        百分位数序列(0-100)
    """
    s = series.copy()
    
    # 方向统一
    if not higher_better:
        s = -s
    
    # 处理缺失值(用中位数填充)
    s = s.fillna(s.median())
    
    # 计算百分位数
    n = len(s)
    if n == 0:
        return pd.Series([])
    
    # rank -> percentile
    percentile = (s.rank(method='average') - 0.5) / n
    
    return 100.0 * percentile


def sigmoid_transform(x: pd.Series, k: float = 0.1) -> pd.Series:
    """
    sigmoid变换(可选的非线性映射)
    
    Args:
        x: 输入序列(假设已在0-100范围)
        k: 陡峭度参数
        
    Returns:
        变换后的序列
    """
    # 将0-100映射到-5到5的范围
    x_scaled = (x - 50) / 10
    return 100 / (1 + np.exp(-k * x_scaled))


def calculate_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """
    计算收益率
    
    Args:
        prices: 价格序列
        periods: 周期数
        
    Returns:
        收益率序列
    """
    return prices.pct_change(periods)


def calculate_max_drawdown(returns: pd.Series) -> Tuple[float, int]:
    """
    计算最大回撤
    
    Args:
        returns: 收益率序列
        
    Returns:
        (最大回撤, 回撤持续期)
    """
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.expanding().max()
    drawdown = (cum_returns - running_max) / running_max
    
    max_dd = drawdown.min()
    
    # 计算回撤持续期
    dd_duration = 0
    current_duration = 0
    for dd in drawdown:
        if dd < 0:
            current_duration += 1
            dd_duration = max(dd_duration, current_duration)
        else:
            current_duration = 0
            
    return max_dd, dd_duration


def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.03) -> float:
    """
    计算Sharpe比率
    
    Args:
        returns: 日收益率序列
        risk_free_rate: 无风险利率(年化)
        
    Returns:
        Sharpe比率(年化)
    """
    if len(returns) == 0:
        return 0.0
    
    # 年化收益率
    annual_return = returns.mean() * 252
    
    # 年化波动率
    annual_vol = returns.std() * np.sqrt(252)
    
    if annual_vol == 0:
        return 0.0
    
    return (annual_return - risk_free_rate) / annual_vol


def calculate_information_coefficient(predictions: pd.Series, 
                                     actual: pd.Series,
                                     method: str = 'pearson') -> float:
    """
    计算IC(Information Coefficient)
    
    Args:
        predictions: 预测值(因子分数)
        actual: 实际值(未来收益)
        method: 'pearson' 或 'spearman'
        
    Returns:
        IC值
    """
    # 对齐索引
    aligned = pd.DataFrame({
        'pred': predictions,
        'actual': actual
    }).dropna()
    
    if len(aligned) < 2:
        return 0.0
    
    if method == 'pearson':
        return aligned['pred'].corr(aligned['actual'])
    else:  # spearman
        return aligned['pred'].corr(aligned['actual'], method='spearman')


def check_limit_price(price: float, prev_close: float, 
                     limit_pct: float = 0.10) -> Tuple[bool, bool]:
    """
    检查涨跌停
    
    Args:
        price: 当前价格
        prev_close: 前收盘价
        limit_pct: 涨跌幅限制(0.10表示10%)
        
    Returns:
        (是否涨停, 是否跌停)
    """
    limit_up = prev_close * (1 + limit_pct)
    limit_down = prev_close * (1 - limit_pct)
    
    # 考虑价格最小变动单位的舍入误差
    is_limit_up = abs(price - limit_up) < 0.01
    is_limit_down = abs(price - limit_down) < 0.01
    
    return is_limit_up, is_limit_down


def calculate_transaction_cost(amount: float, 
                              trade_type: str,
                              commission_rate: float = 0.0003,
                              commission_min: float = 5,
                              stamp_tax: float = 0.0005,
                              transfer_fee: float = 0.00001,
                              slippage: float = 0.001) -> float:
    """
    计算交易成本
    
    Args:
        amount: 交易金额
        trade_type: 'buy' 或 'sell'
        commission_rate: 佣金费率
        commission_min: 最低佣金
        stamp_tax: 印花税(仅卖出)
        transfer_fee: 过户费(双向)
        slippage: 滑点
        
    Returns:
        总成本
    """
    # 佣金
    commission = max(amount * commission_rate, commission_min)
    
    # 过户费
    transfer = amount * transfer_fee
    
    # 印花税(仅卖出)
    stamp = amount * stamp_tax if trade_type == 'sell' else 0
    
    # 滑点
    slip = amount * slippage
    
    return commission + transfer + stamp + slip


def resample_bootstrap(data: pd.Series, n_samples: int = 1000, 
                       block_size: Optional[int] = None) -> np.ndarray:
    """
    Bootstrap重采样
    
    Args:
        data: 原始数据
        n_samples: 重采样次数
        block_size: 块大小(用于时间序列数据)
        
    Returns:
        重采样结果数组
    """
    n = len(data)
    results = []
    
    for _ in range(n_samples):
        if block_size is None:
            # 简单重采样
            sample = data.sample(n=n, replace=True)
        else:
            # 块重采样(保持时间序列相关性)
            indices = []
            while len(indices) < n:
                start = np.random.randint(0, n - block_size + 1)
                indices.extend(range(start, min(start + block_size, n)))
            indices = indices[:n]
            sample = data.iloc[indices]
        
        results.append(sample.mean())
    
    return np.array(results)


class PerformanceMetrics:
    """绩效指标计算类"""
    
    @staticmethod
    def calculate_all_metrics(returns: pd.Series, 
                             benchmark_returns: Optional[pd.Series] = None) -> Dict:
        """计算所有绩效指标"""
        metrics = {}
        
        # 基础统计
        metrics['total_return'] = (1 + returns).prod() - 1
        metrics['annual_return'] = returns.mean() * 252
        metrics['annual_vol'] = returns.std() * np.sqrt(252)
        
        # 风险调整收益
        metrics['sharpe_ratio'] = calculate_sharpe_ratio(returns)
        
        # 回撤
        max_dd, dd_duration = calculate_max_drawdown(returns)
        metrics['max_drawdown'] = max_dd
        metrics['dd_duration'] = dd_duration
        
        # 胜率
        metrics['win_rate'] = (returns > 0).mean()
        
        # 盈亏比
        winning_returns = returns[returns > 0]
        losing_returns = returns[returns < 0]
        if len(losing_returns) > 0:
            metrics['profit_loss_ratio'] = winning_returns.mean() / abs(losing_returns.mean())
        else:
            metrics['profit_loss_ratio'] = np.inf
        
        # 相对基准指标
        if benchmark_returns is not None:
            excess_returns = returns - benchmark_returns
            metrics['excess_return'] = excess_returns.mean() * 252
            
            tracking_error = excess_returns.std() * np.sqrt(252)
            if tracking_error > 0:
                metrics['information_ratio'] = metrics['excess_return'] / tracking_error
            else:
                metrics['information_ratio'] = 0.0
        
        return metrics


def detect_st_stocks(stock_name: str) -> bool:
    """
    检测是否为ST股票
    
    Args:
        stock_name: 股票名称
        
    Returns:
        是否为ST股票
    """
    st_keywords = ['ST', '*ST', 'S', 'S*ST', 'SST']
    return any(keyword in stock_name for keyword in st_keywords)


def get_trading_dates(start_date: str, end_date: str) -> List[str]:
    """
    获取交易日列表(简化版本，实际应从交易所日历获取)
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        
    Returns:
        交易日列表
    """
    # 这里简化处理，实际应该从真实的交易日历获取
    # 可以使用akshare的tool_trade_date_hist_sina()函数
    dates = pd.date_range(start=start_date, end=end_date, freq='B')
    return [d.strftime('%Y-%m-%d') for d in dates]
