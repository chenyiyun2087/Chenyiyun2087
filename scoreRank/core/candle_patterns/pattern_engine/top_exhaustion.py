"""顶部风险信号（放在 EXIT_VETO 层）。

top_exhaustion_volume_v1    — 高位放量滞涨/长上影
shooting_star_volume_v1     — 射击之星 + 放量
evening_star_volume_v1      — 黄昏星 + 放量
"""

from __future__ import annotations

from ..utils import load_settings
from .ohlcv_features import upper_shadow_ratio, body_ratio
from .signal_registry import register, PatternSignal


@register("top_exhaustion_volume_v1", family="exhaustion",
          description="高位放量滞涨/长上影，适合做风险否决")
def evaluate_top_exhaustion(df, trend_state, vol_result, levels_result,
                             patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """高位放量长上影 / 放量滞涨。

    条件：
    - 近20日涨幅 >= 50%
    - 上影线占比 >= 45%
    - 成交量 >= 2x 20日均量
    - 收盘 < 最高价 * 0.94
    """
    if df is None or len(df) < 20:
        return PatternSignal("top_exhaustion_volume_v1", "exhaustion", "neutral", "fail")

    close_s = df["close"]
    close = float(close_s.iloc[-1])
    last = df.iloc[-1]
    open_, high_, low_ = float(last["open"]), float(last["high"]), float(last["low"])

    # 1. 近20日涨幅
    if len(close_s) >= 21:
        prior_rise = close / float(close_s.iloc[-21]) - 1
    else:
        prior_rise = 0.0

    # 2. 上影线比例
    us_ratio = upper_shadow_ratio(open_, high_, low_, close)

    # 3. 成交量
    vol_ratio = vol_result.get("volume_ratio_20", 0) or 1.0

    # 4. 收盘弱势
    weak_close = close < high_ * 0.94

    # 5. 高位判定
    if len(df) >= 120:
        high_120 = float(df["high"].tail(120).max())
        top_position = close >= high_120 * 0.90
    else:
        top_position = prior_rise >= 0.30

    # 条件组合
    cond_prior_rise = prior_rise >= 0.50
    cond_upper_shadow = us_ratio >= 0.45
    cond_high_volume = vol_ratio >= 2.0
    cond_weak = weak_close
    cond_top = top_position

    triggered = cond_prior_rise and cond_upper_shadow and cond_high_volume and cond_weak and cond_top

    score_parts = sum([cond_prior_rise, cond_upper_shadow, cond_high_volume, cond_weak, cond_top])
    confidence = round(score_parts / 5.0, 2)
    score = score_parts / 5.0 * 100

    reasons = []
    risk_flags = []
    if triggered:
        if cond_prior_rise: reasons.append(f"近20日涨幅 {prior_rise*100:.1f}%，累计涨幅大")
        if cond_upper_shadow: reasons.append(f"上影线占比 {us_ratio:.0%}")
        if cond_high_volume: reasons.append(f"天量 {vol_ratio:.1f}x 20日均量")
        if cond_weak: reasons.append("收盘弱势，盘中冲高回落")
        risk_flags.append("高位放量滞涨，警惕分歧派发")

    detail = {
        "prior_20d_return": round(prior_rise * 100, 2),
        "upper_shadow_ratio": round(us_ratio, 4),
        "volume_ratio_20": vol_ratio,
        "weak_close": weak_close,
        "close": round(close, 2),
        "high": round(high_, 2),
    }

    return PatternSignal(
        pattern_id="top_exhaustion_volume_v1",
        pattern_family="exhaustion",
        direction="bearish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail=detail,
    )


@register("shooting_star_volume_v1", family="exhaustion",
          description="射击之星 + 放量")
def evaluate_shooting_star(df, trend_state, vol_result, levels_result,
                            patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """射击之星 + 放量确认。"""
    if df is None or len(df) < 5:
        return PatternSignal("shooting_star_volume_v1", "exhaustion", "neutral", "fail")

    pat_keys = [p.get("key") for p in patterns]
    has_shooting = "shooting_star" in pat_keys or "shooting_star_like" in pat_keys
    vol_ok = (vol_result.get("volume_ratio_20", 0) or 0) >= 1.5

    # 位置：高位
    close = float(df.iloc[-1]["close"])
    if len(df) >= 60:
        high_60 = float(df["high"].tail(60).max())
        top_position = close >= high_60 * 0.90
    else:
        top_position = False

    triggered = has_shooting and vol_ok and top_position
    score_parts = sum([has_shooting, vol_ok, top_position])
    confidence = round(score_parts / 3.0, 2)

    reasons = []
    risk_flags = []
    if has_shooting: reasons.append("出现射击之星形态")
    if vol_ok: reasons.append(f"放量 {vol_result.get('volume_ratio_20'):.1f}x 20日均量")
    if top_position: reasons.append("处于近期高位")
    if triggered:
        risk_flags.append("射击之星+放量，警惕顶部反转")

    return PatternSignal(
        pattern_id="shooting_star_volume_v1",
        pattern_family="exhaustion",
        direction="bearish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score_parts / 3.0 * 100, 1),
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail={
            "has_shooting_star": has_shooting,
            "volume_ratio_20": vol_result.get("volume_ratio_20"),
            "top_position": top_position,
        },
    )


@register("evening_star_volume_v1", family="exhaustion",
          description="黄昏星 + 放量")
def evaluate_evening_star(df, trend_state, vol_result, levels_result,
                           patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """黄昏星 + 放量确认。"""
    if df is None or len(df) < 5:
        return PatternSignal("evening_star_volume_v1", "exhaustion", "neutral", "fail")

    pat_keys = [p.get("key") for p in patterns]
    has_evening = "evening_star" in pat_keys
    vol_ok = (vol_result.get("volume_ratio_20", 0) or 0) >= 1.5

    triggered = has_evening and vol_ok
    score_parts = sum([has_evening, vol_ok])
    confidence = round(score_parts / 2.0, 2)

    reasons = []
    risk_flags = []
    if has_evening: reasons.append("出现黄昏星形态")
    if vol_ok: reasons.append(f"放量 {vol_result.get('volume_ratio_20'):.1f}x 20日均量确认")
    if triggered:
        risk_flags.append("黄昏星+放量，顶部反转信号")

    return PatternSignal(
        pattern_id="evening_star_volume_v1",
        pattern_family="exhaustion",
        direction="bearish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score_parts / 2.0 * 100, 1),
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail={
            "has_evening_star": has_evening,
            "volume_ratio_20": vol_result.get("volume_ratio_20"),
        },
    )
