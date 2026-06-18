"""三角形突破检测。

triangle_breakout_volume_v1 — 三角形整理突破 + 放量确认
"""

from __future__ import annotations

import numpy as np

from ..utils import load_settings
from .signal_registry import register, PatternSignal


def _linear_regression_slope(values) -> float:
    """简单线性回归斜率。正=上升，负=下降。"""
    n = len(values)
    if n < 3:
        return 0.0
    x = np.arange(n)
    y = np.array(values)
    slope = np.polyfit(x, y, 1)[0]
    return float(slope)


@register("triangle_breakout_volume_v1", family="breakout",
          description="三角形整理突破 + 放量确认")
def evaluate_triangle_breakout(df, trend_state, vol_result, levels_result,
                                patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """三角形突破检测。

    识别特征：
    - 近20日内高点和低点分别形成收敛趋势
    - 高点斜率下降（压力线），低点斜率上升（支撑线）
    - 价格突破压力线或支撑线
    """
    if df is None or len(df) < 30:
        return PatternSignal("triangle_breakout_volume_v1", "breakout", "neutral", "fail")

    # 取近30日数据分段
    window = min(30, len(df) // 2)
    tail = df.tail(window)

    # 分段取高点和低点（每5日一段）
    seg_size = max(5, window // 6)
    seg_highs = []
    seg_lows = []
    for i in range(0, window - seg_size + 1, seg_size):
        seg = tail.iloc[i : i + seg_size]
        seg_highs.append(float(seg["high"].max()))
        seg_lows.append(float(seg["low"].min()))

    if len(seg_highs) < 3:
        return PatternSignal("triangle_breakout_volume_v1", "breakout", "neutral", "fail")

    high_slope = _linear_regression_slope(seg_highs)
    low_slope = _linear_regression_slope(seg_lows)

    # 三角形特征：高点下降(压力)、低点上升(支撑)
    is_triangle = high_slope < 0 and low_slope > 0

    # 价格收敛（区间缩小）
    first_range = seg_highs[0] - seg_lows[0]
    last_range = seg_highs[-1] - seg_lows[-1]
    converging = last_range < first_range * 0.8 if first_range > 0 else False

    close = float(tail.iloc[-1]["close"])
    recent_high = max(seg_highs[-2:])  # 最近压力
    recent_low = min(seg_lows[-2:])   # 最近支撑

    # 突破方向
    cond_up = close > recent_high * 1.01
    cond_down = close < recent_low * 0.99

    vol_ratio = vol_result.get("volume_ratio_20", 0) or 1.0
    cond_volume = vol_ratio >= 1.5

    triggered_up = is_triangle and converging and cond_up and cond_volume
    triggered_down = is_triangle and converging and cond_down and cond_volume
    triggered = triggered_up or triggered_down
    direction = "bullish" if triggered_up else ("bearish" if triggered_down else "neutral")

    score_parts = sum([is_triangle, converging, cond_up or cond_down, cond_volume])
    confidence = round(score_parts / 4.0, 2)
    score = score_parts / 4.0 * 100

    reasons = []
    if is_triangle and converging:
        reasons.append("三角形收敛整理形态")
        reasons.append(f"高点斜率 {high_slope:.4f} / 低点斜率 {low_slope:.4f}")
    if triggered_up:
        reasons.append(f"向上突破压力线 {recent_high:.2f}")
        reasons.append(f"成交量 {vol_ratio:.1f}x 20日均量确认")
    elif triggered_down:
        reasons.append(f"向下破位支撑线 {recent_low:.2f}")
        reasons.append(f"成交量 {vol_ratio:.1f}x 20日均量")

    return PatternSignal(
        pattern_id="triangle_breakout_volume_v1",
        pattern_family="breakout",
        direction=direction,
        signal_state="pass" if triggered else "candidate",
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        detail={
            "high_slope": round(high_slope, 4),
            "low_slope": round(low_slope, 4),
            "converging": converging,
            "recent_high": round(recent_high, 2),
            "recent_low": round(recent_low, 2),
            "close": round(close, 2),
            "volume_ratio_20": vol_ratio,
        },
    )
