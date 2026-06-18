"""平台/箱体/盘整检测与突破识别。

box_breakout_v1 — 放量突破箱体（优先级最高的信号）
"""

from __future__ import annotations

from ..utils import load_settings
from .ohlcv_features import compute_rolling_high, compute_rolling_low, detect_gap
from .signal_registry import register, PatternSignal


def detect_box(df, window: int = 40) -> dict:
    """检测近 window 日是否形成箱体。

    箱体定义：
    - 价格振幅 (high-low)/mid < 25%
    - 在箱体内停留天数 >= 20
    """
    if df is None or len(df) < window:
        return {"is_box": False, "box_high": 0.0, "box_low": 0.0, "box_mid": 0.0,
                "box_width": 0.0, "days_in_box": 0, "close_position": 0.0}

    tail = df.tail(window)
    box_high = float(tail["high"].max())
    box_low = float(tail["low"].min())
    box_mid = (box_high + box_low) / 2.0
    box_width = (box_high - box_low) / box_low if box_low > 0 else 0.0

    close = float(tail.iloc[-1]["close"])
    close_position = (close - box_low) / (box_high - box_low) if box_high > box_low else 0.5
    days_inside = int(((tail["close"] >= box_low * 0.98) & (tail["close"] <= box_high * 1.02)).sum())

    max_width = load_settings().get("pattern_engine", {}).get("box_max_width", 0.25)
    min_days = load_settings().get("pattern_engine", {}).get("box_min_days", 20)

    return {
        "is_box": box_width < max_width and days_inside >= min_days,
        "box_high": round(box_high, 3),
        "box_low": round(box_low, 3),
        "box_mid": round(box_mid, 3),
        "box_width": round(box_width, 4),
        "days_in_box": days_inside,
        "close_position": round(close_position, 4),
    }


def _score_breakout_quality(close, box_high, vol_ratio_20, turnover_q, body_r, gap_info) -> tuple[float, float, list[str], list[str]]:
    """对突破质量评分。返回 (score, confidence, reasons, risk_flags)."""
    reasons = []
    risk_flags = []
    score_parts = 0.0
    max_parts = 4.0

    # 1. 价格突破
    if close > box_high * 1.01:
        score_parts += 1.0
        reasons.append(f"收于箱体上沿 {box_high:.2f} 之上")

    # 2. 成交量确认
    if vol_ratio_20 >= 1.5:
        score_parts += 1.0
        reasons.append(f"成交量 {vol_ratio_20:.1f}x 20日均量")
    elif vol_ratio_20 >= 1.0:
        score_parts += 0.5
        reasons.append(f"成交量 {vol_ratio_20:.1f}x 20日均量（略低）")

    # 3. 换手率分位
    if turnover_q >= 0.7:
        score_parts += 1.0
        reasons.append(f"换手率处于 {turnover_q:.0%} 分位，交易活跃")

    # 4. 实体质量
    if body_r >= 0.5:
        score_parts += 1.0
        reasons.append("实体饱满，突破质量高")
    elif body_r >= 0.3:
        score_parts += 0.5
    else:
        risk_flags.append("实体偏小，突破力度存疑")

    # 5. 风控：跳空/一字板
    if gap_info.get("has_gap_up"):
        risk_flags.append("跳空突破，注意回补风险")
    if gap_info.get("gap_ratio", 0) > 0.03:
        risk_flags.append("跳空幅度过大")

    confidence = score_parts / max_parts
    score = score_parts / max_parts * 100

    return round(score, 1), round(confidence, 2), reasons, risk_flags


@register("box_breakout_v1", family="breakout", description="放量突破箱体")
def evaluate_box_breakout(df, trend_state, vol_result, levels_result,
                           patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """箱体突破检测。"""
    if df is None or len(df) < 40:
        return PatternSignal("box_breakout_v1", "breakout", "neutral", "fail")

    box = detect_box(df, window=40)
    if not box["is_box"]:
        return PatternSignal("box_breakout_v1", "breakout", "neutral", "candidate",
                             detail=box)

    close = float(df.iloc[-1]["close"])
    last = df.iloc[-1]
    is_up = float(last["close"]) >= float(last["open"])
    body_r = abs(float(last["close"]) - float(last["open"])) / (float(last["high"]) - float(last["low"])) if (float(last["high"]) - float(last["low"])) > 0 else 0

    vol_ratio_20 = vol_result.get("volume_ratio_20", 0) or 1.0
    turnover_q = vol_result.get("turnover_quantile_60", 0) or 0.5
    gap_info = detect_gap(df)

    is_breakout = close > box["box_high"] * 1.01 and vol_ratio_20 >= 1.5

    score, confidence, reasons, risk_flags = _score_breakout_quality(
        close, box["box_high"], vol_ratio_20, turnover_q, body_r, gap_info
    )

    state = "pass" if is_breakout else "candidate"
    direction = "bullish" if is_breakout else "neutral"

    metrics = {
        "box_width": box["box_width"],
        "days_in_box": box["days_in_box"],
        "close_position": box["close_position"],
        "breakout_strength": round((close / box["box_high"] - 1) * 100, 2),
    }

    return PatternSignal(
        pattern_id="box_breakout_v1",
        pattern_family="breakout",
        direction=direction,
        signal_state=state,
        score=score,
        confidence=confidence,
        metrics=metrics,
        reasons=reasons,
        risk_flags=risk_flags,
        detail={
            "close": round(close, 2),
            "box_high": box["box_high"],
            "box_low": box["box_low"],
            "volume_ratio_20": vol_ratio_20,
            "turnover_quantile_60": turnover_q,
            "body_ratio": round(body_r, 2),
        },
    )
