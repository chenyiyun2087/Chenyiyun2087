"""杯柄形态检测（简化版）。

cup_handle_breakout_v1 — 杯柄突破 + 放量确认

先只做 candidate 标记，不直接作为 entry 信号，
避免过拟合。
"""

from __future__ import annotations

from ..utils import load_settings
from .signal_registry import register, PatternSignal


@register("cup_handle_breakout_v1", family="breakout",
          description="杯柄形态突破（简化版，仅标记结构）")
def evaluate_cup_handle(df, trend_state, vol_result, levels_result,
                         patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """杯柄形态检测（简化保守版）。

    检测窗口参数：
    - 杯形总长度：60-180日
    - 杯深：12%-45%
    - 右杯沿 >= 左杯沿 85%
    - 柄部：5-25日，回撤 <= 杯深 1/3
    """
    if df is None or len(df) < 60:
        return PatternSignal("cup_handle_breakout_v1", "breakout", "neutral", "fail")

    cfg = load_settings().get("pattern_engine", {})
    min_cup = cfg.get("cup_min_days", 60)
    max_cup = cfg.get("cup_max_days", 180)
    min_depth = cfg.get("cup_min_depth", 0.12)
    max_depth = cfg.get("cup_max_depth", 0.45)
    handle_max = cfg.get("handle_max_days", 25)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 搜索窗口
    search_window = min(max_cup, len(df))
    tail = df.tail(search_window)

    # 左杯沿（窗口前半段最高点）
    mid = len(tail) // 2
    left_half = tail.iloc[:mid]
    right_half = tail.iloc[mid:]

    left_peak = float(left_half["high"].max())
    left_peak_idx = left_half["high"].idxmax()

    # 杯底（左杯沿之后的最低价）
    after_left = df.loc[left_peak_idx:]
    if len(after_left) < 10:
        return PatternSignal("cup_handle_breakout_v1", "breakout", "neutral", "fail")

    bottom_idx = after_left["low"].idxmin()
    bottom = float(after_left["low"].min())
    bottom_pos = df.index.get_loc(bottom_idx)

    # 杯深
    cup_depth = (left_peak - bottom) / left_peak if left_peak > 0 else 0

    # 右杯沿（杯底到现在的最高点）
    from_bottom = df.loc[bottom_idx:]
    right_peak = float(from_bottom["high"].max())
    right_recovery = right_peak / left_peak if left_peak > 0 else 0

    # 杯形总长度
    total_days = len(df) - bottom_pos

    # 柄部：右杯沿到现在的回撤
    after_right_peak = from_bottom.loc[from_bottom["high"].idxmax():]
    if len(after_right_peak) < 3:
        handle_low = bottom
    else:
        handle_low = float(after_right_peak["low"].min())
    handle_depth = (right_peak - handle_low) / right_peak if right_peak > 0 else 0

    # 条件判定
    cond_cup_shape = (
        min_cup <= total_days <= max_cup
        and min_depth <= cup_depth <= max_depth
        and right_recovery >= 0.85
    )
    cond_handle = (
        len(after_right_peak) <= handle_max
        and handle_depth <= cup_depth / 3 if cup_depth > 0 else True
    )
    cond_structure = cond_cup_shape and cond_handle

    # 突破确认
    current_close = float(close.iloc[-1])
    cond_breakout = current_close > right_peak * 1.01
    vol_ratio = vol_result.get("volume_ratio_20", 0) or 1.0
    cond_volume = vol_ratio >= 1.5

    triggered = cond_structure and cond_breakout and cond_volume

    score_parts = sum([cond_cup_shape, cond_handle, cond_breakout, cond_volume])
    confidence = round(score_parts / 4.0, 2)
    score = score_parts / 4.0 * 100

    reasons = []
    if cond_cup_shape:
        reasons.append(f"杯形结构识别：杯深 {cup_depth:.1%}，右杯沿恢复 {right_recovery:.1%}")
    if cond_handle:
        reasons.append(f"柄部回撤 {handle_depth:.1%}，柄长 {len(after_right_peak)} 日")
    if cond_breakout:
        reasons.append(f"突破柄部高点 {right_peak:.2f}")
    if cond_volume:
        reasons.append(f"成交量 {vol_ratio:.1f}x 20日均量确认")

    risk_flags = []
    if triggered:
        risk_flags.append("杯柄信号参数敏感，建议后续确认")

    return PatternSignal(
        pattern_id="cup_handle_breakout_v1",
        pattern_family="breakout",
        direction="bullish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail={
            "cup_depth": round(cup_depth, 4),
            "right_recovery": round(right_recovery, 4),
            "handle_depth": round(handle_depth, 4),
            "handle_days": len(after_right_peak),
            "total_days": total_days,
            "right_peak": round(right_peak, 2),
            "close": round(current_close, 2),
            "volume_ratio_20": vol_ratio,
        },
    )
