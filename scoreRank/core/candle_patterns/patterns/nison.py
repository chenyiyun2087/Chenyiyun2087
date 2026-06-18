"""第一层补充：日本蜡烛图传统形态自研校准。

参考 Steve Nison《日本蜡烛图技术》，用更贴近原教旨的规则复算锤头/射击/
十字星/阳包阴/三白兵/三只乌鸦。与 standard.py 互为校验，可交叉确认。
所有阈值来自内置 candle pattern 配置。
"""

from __future__ import annotations

import pandas as pd

from ..utils import load_settings
from .base import Candle, row_to_candle, avg_body
from .standard import get_explanation


def _cfg() -> dict:
    return load_settings().get("candle", {})


def _exp(key: str) -> str:
    """获取形态解释，无解释时返回空字符串。"""
    return get_explanation(key)


def detect_nison_patterns(df: pd.DataFrame) -> list[dict]:
    """对 df 最后 1~3 根 K 线做传统形态识别。

    返回 [{key, name, direction, explanation}]。
    """
    if df is None or len(df) < 3:
        return []

    c = _cfg()
    doji_ratio = c.get("doji_body_ratio", 0.10)
    spinning_ratio = c.get("spinning_body_ratio", 0.40)
    hammer_lower = c.get("hammer_lower_shadow_mult", 2.0)
    hammer_upper = c.get("hammer_upper_shadow_mult", 1.2)
    shooting_upper = c.get("shooting_upper_shadow_mult", 2.0)
    results: list[dict] = []

    last_candle = row_to_candle(df.iloc[-1])
    body = last_candle.body
    rng = last_candle.range_

    # ---- 单 K 形态 ----
    if rng > 0 and body / rng <= doji_ratio:
        results.append({"key": "doji", "name": "十字星", "direction": "neutral", "explanation": _exp("doji")})

    if rng > 0 and doji_ratio < body / rng <= spinning_ratio:
        results.append({"key": "spinning_top", "name": "纺锤顶", "direction": "neutral", "explanation": _exp("spinning_top")})

    if body > 0 and last_candle.lower_shadow >= body * hammer_lower and last_candle.upper_shadow <= body * hammer_upper:
        results.append({"key": "hammer_like", "name": "锤头/上吊类", "direction": "neutral", "explanation": _exp("hammer_like")})

    if body > 0 and last_candle.upper_shadow >= body * shooting_upper and last_candle.lower_shadow <= body * hammer_upper:
        results.append({"key": "shooting_star_like", "name": "射击之星类", "direction": "short", "explanation": _exp("shooting_star_like")})

    avg_b = avg_body(df, 20)
    if avg_b > 0:
        big_mult = c.get("large_body_mult", 2.0)
        if body >= avg_b * big_mult:
            if last_candle.is_up:
                results.append({"key": "large_bullish", "name": "大阳线", "direction": "long", "explanation": _exp("large_bullish")})
            else:
                results.append({"key": "large_bearish", "name": "大阴线", "direction": "short", "explanation": _exp("large_bearish")})

    # ---- 双 K 形态 ----
    if len(df) >= 2:
        prev = row_to_candle(df.iloc[-2])
        if prev.body > 0 and last_candle.body > prev.body:
            if last_candle.close >= prev.open and last_candle.open <= prev.close and prev.is_down and last_candle.is_up:
                results.append({"key": "bullish_engulfing", "name": "阳包阴", "direction": "long", "explanation": _exp("bullish_engulfing")})
            elif last_candle.open >= prev.close and last_candle.close <= prev.open and prev.is_up and last_candle.is_down:
                results.append({"key": "bearish_engulfing", "name": "阴包阳", "direction": "short", "explanation": _exp("bearish_engulfing")})

    # ---- 三 K 形态 ----
    if len(df) >= 3:
        c1 = row_to_candle(df.iloc[-3])
        c2 = row_to_candle(df.iloc[-2])
        c3 = last_candle
        if c1.is_up and c2.is_up and c3.is_up and c2.close > c1.close and c3.close > c2.close:
            results.append({"key": "three_white_soldiers", "name": "三白兵", "direction": "long", "explanation": _exp("three_white_soldiers")})
        if c1.is_down and c2.is_down and c3.is_down and c2.close < c1.close and c3.close < c2.close:
            results.append({"key": "three_black_crows", "name": "三只乌鸦", "direction": "short", "explanation": _exp("three_black_crows")})

    return results
