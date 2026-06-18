"""量能上下文。

量比、换手率、放量/缩量，以及"反抽量能是否充足"等复合判断。
增强：20日均量、60日量能分位数、量能趋势方向。
"""

from __future__ import annotations

import pandas as pd

from ..patterns.base import row_to_candle, avg_volume
from ..utils import load_settings


def compute_volume_quantile(df: pd.DataFrame, window: int = 60) -> float:
    """计算最新成交量在近 window 日中的分位数 (0~1)。"""
    if df is None or len(df) < 2:
        return 0.5
    tail = df["volume"].tail(window)
    if tail.empty or tail.nunique() <= 1:
        return 0.5
    last_v = float(tail.iloc[-1])
    rank = (tail < last_v).sum()
    return round(rank / len(tail), 4)


def compute_turnover_quantile(df: pd.DataFrame, window: int = 60) -> float:
    """计算最新换手率在近 window 日中的分位数 (0~1)。"""
    if df is None or len(df) < 2 or "turnover" not in df.columns:
        return 0.5
    tail = df["turnover"].tail(window)
    if tail.empty or tail.nunique() <= 1:
        return 0.5
    last_t = float(tail.iloc[-1])
    rank = (tail < last_t).sum()
    return round(rank / len(tail), 4)


def detect_volume_trend(df: pd.DataFrame) -> str:
    """判断量能趋势方向：rising / falling / flat。

    比较近5日均量与近20日均量的比值。
    """
    if df is None or len(df) < 20:
        return "flat"
    vol = df["volume"]
    ma5 = vol.tail(5).mean()
    ma20 = vol.tail(20).mean()
    if ma20 <= 0:
        return "flat"
    ratio = ma5 / ma20
    if ratio >= 1.15:
        return "rising"
    if ratio <= 0.85:
        return "falling"
    return "flat"


def analyze_volume(df: pd.DataFrame) -> dict:
    """分析量能上下文。

    返回:
        {
          "volume_context": "volume_surge" | "volume_shrink" | "rebound_volume_not_enough" | ...,
          "volume_ratio_5": float,          # 量比（vs 5日均量）
          "volume_ratio_20": float,         # 量比（vs 20日均量）
          "volume_ma20": float,             # 20日均量
          "turnover": float,                # 换手率 %
          "volume_quantile_60": float,      # 60日量能分位数
          "turnover_quantile_60": float,    # 60日换手率分位数
          "volume_trend": str,              # "rising"/"falling"/"flat"
          "score_hint": int
        }
    """
    if df is None or df.empty:
        return {"volume_context": "unknown", "volume_ratio_5": 0.0, "volume_ratio_20": 0.0,
                "volume_ma20": 0, "turnover": 0.0, "volume_quantile_60": 0.5,
                "turnover_quantile_60": 0.5, "volume_trend": "flat", "score_hint": 0}

    vc = load_settings().get("volume", {})
    window = vc.get("volume_ratio_window", 5)
    avg_v = avg_volume(df, window)
    last = row_to_candle(df.iloc[-1])

    ratio = last.volume / avg_v if avg_v > 0 else 1.0
    turnover = last.turnover  # %

    high_th = vc.get("volume_ratio_high", 2.0)
    low_th = vc.get("volume_ratio_low", 0.5)
    active_th = vc.get("turnover_active", 0.05) * 100  # 配置是比例，turnover 是 %
    extreme_th = vc.get("turnover_extreme", 0.15) * 100

    ctx = "normal"
    hint = 0

    if ratio >= high_th:
        ctx = "volume_surge"
        # 上涨放量为正，下跌放量为负
        hint = 8 if last.is_up else -8
    elif ratio <= low_th:
        ctx = "volume_shrink"
        hint = -2 if last.is_up else 2  # 缩量下跌杀伤有限；缩量上涨动能不足

    # 反抽量能不足：阳线但量比明显偏低
    if last.is_up and ratio < 0.8:
        ctx = "rebound_volume_not_enough"
        hint = -4

    # 换手率异常
    if turnover >= extreme_th:
        # 高位高换手偏空，低位高换手偏多 —— 简化为加风险标记
        ctx = (ctx + "+extreme_turnover") if ctx != "normal" else "extreme_turnover"
        hint -= 3
    elif turnover >= active_th and ctx == "normal":
        ctx = "active_turnover"

    # 20日均量
    vol_ma20 = float(df["volume"].tail(20).mean()) if len(df) >= 20 else 0.0
    ratio_20 = round(last.volume / vol_ma20, 2) if vol_ma20 > 0 else 1.0

    return {
        "volume_context": ctx,
        "volume_ratio_5": round(ratio, 2),
        "volume_ratio_20": ratio_20,
        "volume_ma20": round(vol_ma20, 0),
        "turnover": round(turnover, 2),
        "volume_quantile_60": compute_volume_quantile(df),
        "turnover_quantile_60": compute_turnover_quantile(df),
        "volume_trend": detect_volume_trend(df),
        "score_hint": hint,
    }
