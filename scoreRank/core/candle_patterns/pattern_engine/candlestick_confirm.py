"""蜡烛图确认信号（放在 CONFIRMATION 层，不做独立 entry）。

bullish_engulfing_support_v1 — 支撑位看涨吞没
hammer_support_v1          — 支撑位锤子线
morning_star_support_v1    — 支撑位早晨星
"""

from __future__ import annotations

from ..utils import load_settings
from .ohlcv_features import body_ratio, upper_shadow_ratio, lower_shadow_ratio
from .signal_registry import register, PatternSignal


def _check_support(df, trend_state) -> dict:
    """判断是否接近支撑位。

    支持三类支撑：
    - MA20
    - 箱体上沿/前高（levels 中的 resistance 作为潜在支撑）
    - 60日最低点
    """
    close = float(df.iloc[-1]["close"])
    close_s = df["close"]

    # MA20 支撑
    if len(close_s) >= 20:
        ma20 = float(close_s.tail(20).mean())
        near_ma20 = abs(close / ma20 - 1) < 0.03
    else:
        ma20, near_ma20 = close, False

    # 前高/箱体支撑（用 60 日高点作为参考）
    if len(df) >= 60:
        high_60 = float(df["high"].tail(60).max())
        near_prior_high = abs(close / high_60 - 1) < 0.04
    else:
        high_60, near_prior_high = close, False

    # 60日最低点支撑
    if len(df) >= 60:
        low_60 = float(df["low"].tail(60).min())
        near_low_60 = abs(close / low_60 - 1) < 0.05
    else:
        low_60, near_low_60 = close, False

    return {
        "near_ma20": near_ma20,
        "near_prior_high": near_prior_high,
        "near_low_60": near_low_60,
        "ma20": round(ma20, 2),
        "high_60": round(high_60, 2),
        "low_60": round(low_60, 2),
    }


@register("bullish_engulfing_support_v1", family="reversal",
          description="支撑位看涨吞没 + 放量确认")
def evaluate_engulfing_support(df, trend_state, vol_result, levels_result,
                                patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """支撑位看涨吞没。"""
    if df is None or len(df) < 5:
        return PatternSignal("bullish_engulfing_support_v1", "reversal", "neutral", "fail")

    pat_keys = [p.get("key") for p in patterns]
    has_engulfing = "bullish_engulfing" in pat_keys
    support = _check_support(df, trend_state)
    near_support = support["near_ma20"] or support["near_prior_high"] or support["near_low_60"]
    vol_ok = (vol_result.get("volume_ratio_20", 0) or 0) >= 1.2

    score_parts = sum([has_engulfing, near_support, vol_ok])
    confidence = round(score_parts / 3.0, 2)
    score = score_parts / 3.0 * 100

    triggered = has_engulfing and near_support and vol_ok
    state = "pass" if triggered else "candidate"
    direction = "bullish" if triggered else "neutral"

    reasons = []
    if has_engulfing:
        reasons.append("出现看涨吞没形态")
    if near_support:
        tags = [k for k in ["near_ma20", "near_prior_high", "near_low_60"] if support.get(k)]
        reasons.append(f"接近支撑位: {', '.join(tags)}")
    if vol_ok:
        reasons.append(f"成交量 {vol_result.get('volume_ratio_20'):.1f}x 20日均量")

    return PatternSignal(
        pattern_id="bullish_engulfing_support_v1",
        pattern_family="reversal",
        direction=direction,
        signal_state=state,
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        detail={**support, "has_engulfing": has_engulfing, "volume_ratio_20": vol_result.get("volume_ratio_20")},
    )


@register("hammer_support_v1", family="reversal",
          description="支撑位锤子线 + 放量确认")
def evaluate_hammer_support(df, trend_state, vol_result, levels_result,
                             patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """支撑位锤子线。"""
    if df is None or len(df) < 5:
        return PatternSignal("hammer_support_v1", "reversal", "neutral", "fail")

    pat_keys = [p.get("key") for p in patterns]
    has_hammer = "hammer" in pat_keys
    support = _check_support(df, trend_state)
    near_support = support["near_ma20"] or support["near_prior_high"] or support["near_low_60"]
    vol_ok = (vol_result.get("volume_ratio_20", 0) or 0) >= 1.2

    score_parts = sum([has_hammer, near_support, vol_ok])
    confidence = round(score_parts / 3.0, 2)
    score = score_parts / 3.0 * 100

    triggered = has_hammer and near_support and vol_ok
    state = "pass" if triggered else "candidate"

    reasons = []
    if has_hammer:
        reasons.append("出现锤子线形态")
    if near_support:
        tags = [k for k in ["near_ma20", "near_prior_high", "near_low_60"] if support.get(k)]
        reasons.append(f"接近支撑位: {', '.join(tags)}")
    if vol_ok:
        reasons.append(f"成交量 {vol_result.get('volume_ratio_20'):.1f}x 20日均量确认")

    return PatternSignal(
        pattern_id="hammer_support_v1",
        pattern_family="reversal",
        direction="bullish" if triggered else "neutral",
        signal_state=state,
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        detail={**support, "has_hammer": has_hammer, "volume_ratio_20": vol_result.get("volume_ratio_20")},
    )


@register("morning_star_support_v1", family="reversal",
          description="支撑位早晨星 + 第三日放量确认")
def evaluate_morning_star_support(df, trend_state, vol_result, levels_result,
                                   patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """支撑位早晨星。"""
    if df is None or len(df) < 5:
        return PatternSignal("morning_star_support_v1", "reversal", "neutral", "fail")

    pat_keys = [p.get("key") for p in patterns]
    has_morning_star = "morning_star" in pat_keys
    support = _check_support(df, trend_state)
    near_support = support["near_ma20"] or support["near_prior_high"] or support["near_low_60"]
    vol_ok = (vol_result.get("volume_ratio_20", 0) or 0) >= 1.5

    score_parts = sum([has_morning_star, near_support, vol_ok])
    confidence = round(score_parts / 3.0, 2)
    score = score_parts / 3.0 * 100

    triggered = has_morning_star and near_support and vol_ok
    state = "pass" if triggered else "candidate"

    reasons = []
    if has_morning_star:
        reasons.append("出现早晨星形态")
    if near_support:
        tags = [k for k in ["near_ma20", "near_prior_high", "near_low_60"] if support.get(k)]
        reasons.append(f"接近支撑位: {', '.join(tags)}")
    if vol_ok:
        reasons.append("第三日放量确认")

    return PatternSignal(
        pattern_id="morning_star_support_v1",
        pattern_family="reversal",
        direction="bullish" if triggered else "neutral",
        signal_state=state,
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        detail={**support, "has_morning_star": has_morning_star, "volume_ratio_20": vol_result.get("volume_ratio_20")},
    )
