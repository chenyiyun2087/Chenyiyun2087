"""K线基础特征提取。

提供所有形态检测器共享的底层特征计算函数，
避免各模块重复实现。
"""

from __future__ import annotations

import pandas as pd

from ..patterns.base import row_to_candle


def compute_rolling_high(df: pd.DataFrame, window: int = 60) -> pd.Series:
    """滚动最高价。"""
    return df["high"].rolling(window, min_periods=1).max()


def compute_rolling_low(df: pd.DataFrame, window: int = 60) -> pd.Series:
    """滚动最低价。"""
    return df["low"].rolling(window, min_periods=1).min()


def compute_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """平均真实波幅 (ATR)。"""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=1).mean()


def body_ratio(open_, high, low, close) -> float:
    """实体占全距比例。"""
    r = high - low
    if r <= 0:
        return 0.0
    return abs(close - open_) / r


def upper_shadow_ratio(open_, high, low, close) -> float:
    """上影线占全距比例。"""
    r = high - low
    if r <= 0:
        return 0.0
    upper = high - max(open_, close)
    return upper / r


def lower_shadow_ratio(open_, high, low, close) -> float:
    """下影线占全距比例。"""
    r = high - low
    if r <= 0:
        return 0.0
    lower = min(open_, close) - low
    return lower / r


def detect_gap(df: pd.DataFrame) -> dict:
    """检测跳空缺口。"""
    if df is None or len(df) < 2:
        return {"has_gap_up": False, "has_gap_down": False, "gap_ratio": 0.0}
    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    gap_up = float(today["low"]) > float(yesterday["high"])
    gap_down = float(today["high"]) < float(yesterday["low"])

    gap_ratio = 0.0
    if gap_up and float(yesterday["high"]) > 0:
        gap_ratio = (float(today["low"]) - float(yesterday["high"])) / float(yesterday["high"])
    elif gap_down and float(yesterday["low"]) > 0:
        gap_ratio = (float(yesterday["low"]) - float(today["high"])) / float(yesterday["low"])

    return {"has_gap_up": gap_up, "has_gap_down": gap_down, "gap_ratio": round(gap_ratio, 4)}


def compute_ma_series(df: pd.DataFrame, periods: list[int] = None) -> dict[str, pd.Series]:
    """批量计算均线序列。"""
    if periods is None:
        periods = [5, 10, 20, 60]
    return {f"ma{p}": df["close"].rolling(p).mean() for p in periods}
