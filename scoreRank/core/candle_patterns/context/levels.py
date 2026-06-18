"""支撑压力位上下文。

以近 N 日高低点作为支撑/压力位，判断最新价是否接近或破位。
"""

from __future__ import annotations

import pandas as pd

from ..patterns.base import row_to_candle
from ..utils import load_settings


def analyze_levels(df: pd.DataFrame) -> dict:
    """分析支撑压力位。

    返回:
        {
          "support": float,        # 近N日最低点
          "resistance": float,     # 近N日最高点
          "support_status": "above_recent_low" | "broken_support" | ...,
          "resistance_status": "failed_near_recent_high" | "breakout_resistance" | ...,
          "score_hint": int
        }
    """
    if df is None or df.empty:
        return {"support": 0, "resistance": 0, "support_status": "unknown",
                "resistance_status": "unknown", "score_hint": 0}

    lc = load_settings().get("levels", {})
    window = lc.get("lookback_window", 20)
    near = lc.get("near_threshold", 0.02)

    tail = df.tail(window)
    support = float(tail["low"].min())
    resistance = float(tail["high"].max())
    last = row_to_candle(df.iloc[-1])
    close = last.close

    if resistance <= 0:
        return {"support": support, "resistance": resistance, "support_status": "unknown",
                "resistance_status": "unknown", "score_hint": 0}

    hint = 0

    # 支撑位判断
    if close < support:
        s_status = "broken_support"
        hint -= 10
    elif close <= support * (1 + near):
        s_status = "near_support"
        hint += 3
    else:
        s_status = "above_recent_low"

    # 压力位判断
    if close > resistance:
        r_status = "breakout_resistance"
        hint += 10
    elif close >= resistance * (1 - near):
        # 冲高接近压力位但未突破（长上影回落也算）
        if last.upper_shadow > last.body and last.body > 0:
            r_status = "failed_near_recent_high"
            hint -= 5
        else:
            r_status = "near_resistance"
            hint += 0
    else:
        r_status = "below_recent_high"

    return {
        "support": round(support, 3),
        "resistance": round(resistance, 3),
        "support_status": s_status,
        "resistance_status": r_status,
        "score_hint": hint,
    }
