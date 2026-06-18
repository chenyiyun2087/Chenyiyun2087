"""双底 / 三重底突破颈线。

double_bottom_neckline_breakout_v1
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..utils import load_settings
from .signal_registry import register, PatternSignal


def _find_local_minima(series: pd.Series, order: int = 5) -> list[int]:
    """找到局部最低点的索引（order 控制灵敏度）。"""
    length = len(series)
    if length < order * 2 + 1:
        return []
    minima = []
    for i in range(order, length - order):
        window = series.iloc[i - order : i + order + 1]
        if series.iloc[i] == window.min():
            minima.append(i)
    return minima


@register("double_bottom_neckline_breakout_v1", family="reversal",
          description="双底突破颈线 + 放量确认")
def evaluate_double_bottom(df, trend_state, vol_result, levels_result,
                            patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """双底 / 三重底 检测，含颈线突破。"""
    if df is None or len(df) < 60:
        return PatternSignal("double_bottom_neckline_breakout_v1", "reversal", "neutral", "fail")

    cfg = load_settings().get("pattern_engine", {})
    max_similarity = cfg.get("double_bottom_similarity", 0.08)
    min_days = cfg.get("double_bottom_min_days", 15)
    max_days = cfg.get("double_bottom_max_days", 90)

    close = df["close"]
    low = df["low"]
    high = df["high"]

    # 在近60日中找局部最低点
    search_window = min(60, len(df))
    minima_idx = _find_local_minima(low.tail(search_window), order=5)
    if len(minima_idx) < 2:
        return PatternSignal("double_bottom_neckline_breakout_v1", "reversal", "neutral", "candidate")

    # 转换索引为相对于 tail 的全局索引
    base = len(df) - search_window
    minima_global = [base + i for i in minima_idx]

    # 找最近两个底部
    recent_min1_idx = minima_global[-1]
    recent_min2_idx = minima_global[-2]

    bottom1 = float(low.iloc[recent_min1_idx])
    bottom2 = float(low.iloc[recent_min2_idx])
    bottom_similarity = abs(bottom1 - bottom2) / max(bottom1, bottom2) if max(bottom1, bottom2) > 0 else 1.0

    # 两个底部之间的间距
    spacing = abs(recent_min1_idx - recent_min2_idx)

    # 颈线 = 两个底部之间的最高价
    if recent_min1_idx < recent_min2_idx:
        neck_range = high.iloc[recent_min1_idx : recent_min2_idx + 1]
    else:
        neck_range = high.iloc[recent_min2_idx : recent_min1_idx + 1]
    neckline = float(neck_range.max())

    # 颈线相对底部高度
    neck_rise = neckline / min(bottom1, bottom2) - 1 if min(bottom1, bottom2) > 0 else 0

    # 条件判定
    cond_shape = (
        bottom_similarity <= max_similarity
        and min_days <= spacing <= max_days
        and neck_rise >= 0.08
    )

    # 突破条件
    current_close = float(close.iloc[-1])
    cond_breakout = current_close > neckline * 1.01
    vol_ratio = vol_result.get("volume_ratio_20", 0) or 1.0
    cond_volume = vol_ratio >= 1.5

    triggered = cond_shape and cond_breakout and cond_volume

    score_parts = sum([cond_shape, cond_breakout, cond_volume])
    confidence = round(score_parts / 3.0, 2)
    score = score_parts / 3.0 * 100

    reasons = []
    if cond_shape:
        reasons.append(f"双底形态：底部1={bottom1:.2f}, 底部2={bottom2:.2f}, 相似度 {bottom_similarity:.1%}")
        reasons.append(f"颈线位置={neckline:.2f}, 涨幅要求 {neck_rise*100:.1f}%")
    if cond_breakout:
        reasons.append(f"突破颈线 {neckline:.2f}")
    if cond_volume:
        reasons.append(f"成交量 {vol_ratio:.1f}x 20日均量确认")

    return PatternSignal(
        pattern_id="double_bottom_neckline_breakout_v1",
        pattern_family="reversal",
        direction="bullish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        detail={
            "bottom1": round(bottom1, 2),
            "bottom2": round(bottom2, 2),
            "similarity": round(bottom_similarity, 4),
            "neckline": round(neckline, 2),
            "neck_rise": round(neck_rise, 4),
            "spacing_days": spacing,
            "close": round(current_close, 2),
            "volume_ratio_20": vol_ratio,
        },
    )
