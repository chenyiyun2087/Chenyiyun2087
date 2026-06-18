"""量价背离信号（适合做降权 / 风控，不适合单独做买卖点）。

bearish_divergence_v1   — 看跌背离：价格新高但 OBV/MFI 未新高
bullish_divergence_v1   — 看涨背离：价格新低但 OBV/MFI 未新低（仅 confirmation）
"""

from __future__ import annotations

from ..utils import load_settings
from .volume_features import detect_bearish_divergence as _bearish_div, detect_bullish_divergence as _bullish_div
from .signal_registry import register, PatternSignal


@register("bearish_divergence_v1", family="divergence",
          description="看跌背离：价格新高但 OBV/MFI 未新高，适合做排名降权")
def evaluate_bearish_divergence(df, trend_state, vol_result, levels_result,
                                 patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """看跌背离。"""
    if df is None or len(df) < 60:
        return PatternSignal("bearish_divergence_v1", "divergence", "neutral", "fail")

    div = _bearish_div(df)
    triggered = div.get("bearish_divergence", False)

    reasons = []
    risk_flags = []
    if div.get("obv_divergence"):
        reasons.append("价格创新高但 OBV 未创新高")
    if div.get("mfi_divergence"):
        reasons.append("价格创新高但 MFI 未创新高")
    if triggered:
        risk_flags.append("量价背离，上涨动能衰减")

    score = 80 if triggered else 0
    confidence = 0.8 if triggered else 0.0

    return PatternSignal(
        pattern_id="bearish_divergence_v1",
        pattern_family="divergence",
        direction="bearish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=score,
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail={
            "obv_divergence": div.get("obv_divergence", False),
            "mfi_divergence": div.get("mfi_divergence", False),
            "price_new_high": div.get("price_new_high", False),
        },
    )


@register("bullish_divergence_v1", family="divergence",
          description="看涨背离：价格新低但 OBV/MFI 未新低，仅做确认")
def evaluate_bullish_divergence(df, trend_state, vol_result, levels_result,
                                 patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """看涨背离（仅 confirmation，不做 entry）。"""
    if df is None or len(df) < 60:
        return PatternSignal("bullish_divergence_v1", "divergence", "neutral", "fail")

    div = _bullish_div(df)
    triggered = div.get("bullish_divergence", False)

    reasons = []
    if div.get("obv_divergence"):
        reasons.append("价格创新低但 OBV 未创新低")
    if div.get("mfi_divergence"):
        reasons.append("价格创新低但 MFI 未创新低")

    score = 60 if triggered else 0
    confidence = 0.6 if triggered else 0.0

    return PatternSignal(
        pattern_id="bullish_divergence_v1",
        pattern_family="divergence",
        direction="bullish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=score,
        confidence=confidence,
        reasons=reasons,
        detail={
            "obv_divergence": div.get("obv_divergence", False),
            "mfi_divergence": div.get("mfi_divergence", False),
            "price_new_low": div.get("price_new_low", False),
        },
    )
