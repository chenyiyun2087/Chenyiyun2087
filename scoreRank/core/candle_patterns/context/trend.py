"""增强趋势分析：60日高低百分位、ROC动量、趋势状态机。

提供比 ma.py 更丰富的位置和动量指标，供规则层使用。
"""

from __future__ import annotations

import pandas as pd

from ..utils import load_settings
from .ma import analyze_trend as basic_trend


def compute_percentile(df: pd.DataFrame, window: int = 60) -> float:
    """计算最新收盘价在近 window 日高低区间中的百分位。(0~1)

    Args:
        df: 日K DataFrame，需含 close/high/low 列，按日期升序。
        window: 回溯窗口。

    Returns:
        0 ~ 1 之间的值，0 = 近期最低，1 = 近期最高。
    """
    if df is None or len(df) < 2:
        return 0.5
    tail = df.tail(window)
    low = tail["low"].min()
    high = tail["high"].max()
    if high <= low:
        return 0.5
    close = float(tail.iloc[-1]["close"])
    return round((close - low) / (high - low), 4)


def compute_roc(series: pd.Series, period: int = 5) -> float:
    """计算变动率 (Rate of Change)，单位 %。"""
    if len(series) < period + 1:
        return 0.0
    prev = float(series.iloc[-(period + 1)])
    if prev == 0:
        return 0.0
    return round((float(series.iloc[-1]) - prev) / prev * 100, 2)


def compute_max_drawdown_20d(close_series: pd.Series) -> float:
    """近20日最大回撤比例。"""
    if len(close_series) < 2:
        return 0.0
    tail = close_series.tail(20)
    peak = tail.expanding().max()
    dd = (tail - peak) / peak
    return round(float(dd.min()), 4)


def detect_trend_state(df: pd.DataFrame) -> dict:
    """趋势状态机：整合 MA 排列、百分位、ROC、回撤等指标。

    返回:
        {
            "state": "strong_uptrend" | "weak_uptrend" | "ranging" | "weak_downtrend" | "strong_downtrend",
            "percentile_60d": float,       # 60日高低百分位
            "roc_5": float,                # 5日 ROC %
            "roc_10": float,               # 10日 ROC %
            "roc_20": float,               # 20日 ROC %
            "close_20d_return": float,      # 近20日涨幅 %
            "max_drawdown_20d": float,      # 近20日最大回撤 %
            "above_ma60": bool,            # 是否站上 MA60
            "score_hint": int,             # 趋势方向分（-20~+20）
        }
    """
    if df is None or len(df) < 20:
        return {
            "state": "ranging", "percentile_60d": 0.5,
            "roc_5": 0.0, "roc_10": 0.0, "roc_20": 0.0,
            "close_20d_return": 0.0, "max_drawdown_20d": 0.0,
            "above_ma60": False, "score_hint": 0,
        }

    close_s = df["close"]
    close = float(close_s.iloc[-1])
    percentile = compute_percentile(df, 60)
    roc_5 = compute_roc(close_s, 5)
    roc_10 = compute_roc(close_s, 10)
    roc_20 = compute_roc(close_s, 20)
    close_20d_return = compute_roc(close_s, 20)  # 就是 roc_20
    max_dd = compute_max_drawdown_20d(close_s)

    # MA60
    if len(df) >= 60:
        ma60 = float(close_s.tail(60).mean())
        above_ma60 = close > ma60
    else:
        above_ma60 = False

    # 基础 MA 排列信息
    basic = basic_trend(df)
    ma_direction = basic.get("direction", "tangle")

    # 状态判定
    try:
        cfg = load_settings().get("trend", {})
        strong_up_th = cfg.get("strong_uptrend_percentile", 0.85)
        strong_down_th = cfg.get("strong_downtrend_percentile", 0.15)
    except Exception:
        strong_up_th, strong_down_th = 0.85, 0.15

    if percentile >= strong_up_th and ma_direction == "up":
        state = "strong_uptrend"
        hint = 20
    elif percentile >= 0.65 and ma_direction == "up":
        state = "weak_uptrend"
        hint = 10
    elif percentile <= strong_down_th and ma_direction == "down":
        state = "strong_downtrend"
        hint = -20
    elif percentile <= 0.35 and ma_direction == "down":
        state = "weak_downtrend"
        hint = -10
    else:
        state = "ranging"
        hint = 0

    return {
        "state": state,
        "percentile_60d": percentile,
        "roc_5": roc_5,
        "roc_10": roc_10,
        "roc_20": roc_20,
        "close_20d_return": close_20d_return,
        "max_drawdown_20d": max_dd,
        "above_ma60": above_ma60,
        "score_hint": hint,
    }
