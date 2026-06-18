"""第二层：A股特殊K线规则（自研）。

通用蜡烛图库多按美股/外汇逻辑写，覆盖不到 A 股高波动题材股的关键形态。
这里专门识别：涨停/跌停/一字板/炸板/开板回封/高位放量长阴/缩量反抽/
连跌后首阳/冲高回落长上影/反包失败/跌停后修复。
"""

from __future__ import annotations

import pandas as pd

from ..utils import load_settings
from .base import Candle, row_to_candle, avg_body, avg_volume
from .standard import get_explanation


def _limit_threshold(symbol: str, is_st: bool = False) -> float:
    """根据股票代码前缀返回涨停涨幅阈值。"""
    s = load_settings()
    th = s.get("limit_thresholds", {})
    if is_st:
        return th.get("st", 0.05)
    code = symbol.strip()
    if code.startswith("300") or code.startswith("301"):
        return th.get("gem", 0.20)
    if code.startswith("688") or code.startswith("689"):
        return th.get("star", 0.20)
    if code.startswith(("8", "4", "920")):
        return th.get("bse", 0.30)
    return th.get("main_board", 0.10)


def detect_ashare_patterns(df: pd.DataFrame, symbol: str = "", is_st: bool = False) -> list[dict]:
    """对 A 股日K做特殊形态识别。

    返回 [{key, name, direction, score_hint}]。
    score_hint 是该信号建议的分值方向（+ 看多 / - 看空 / 0 中性），供 scorer 参考。
    """
    if df is None or len(df) < 3:
        return []

    s = load_settings()
    candle_cfg = s.get("candle", {})
    vol_cfg = s.get("volume", {})
    tol = s.get("limit_tolerance", 0.99)
    th = _limit_threshold(symbol, is_st)

    results: list[dict] = []
    last = row_to_candle(df.iloc[-1])
    prev = row_to_candle(df.iloc[-2]) if len(df) >= 2 else None
    avg_b = avg_body(df, 20)
    avg_v = avg_volume(df, vol_cfg.get("volume_ratio_window", 5))

    # 涨跌幅（pct_chg 单位 %）
    pct = last.pct_chg / 100.0
    # 涨停判定：涨幅 >= 阈值*tol
    is_limit_up = pct >= th * tol
    is_limit_down = pct <= -th * tol

    # ---- 一字板：开=高=低=收，且涨/跌停 ----
    if is_limit_up and last.open == last.high == last.low == last.close and last.high > 0:
        results.append({"key": "one_word_limit_up", "name": "一字涨停", "direction": "long", "score_hint": 25})
    if is_limit_down and last.open == last.high == last.low == last.close and last.high > 0:
        results.append({"key": "one_word_limit_down", "name": "一字跌停", "direction": "short", "score_hint": -30})

    # ---- 普通涨停 / 跌停 ----
    if is_limit_up and last.open != last.high:
        results.append({"key": "limit_up", "name": "涨停", "direction": "long", "score_hint": 15})
    if is_limit_down and last.open != last.low:
        results.append({"key": "limit_down", "name": "跌停", "direction": "short", "score_hint": -20})

    # ---- 炸板：盘中触及涨停但收未封板 ----
    # 用 high 接近涨停价、但 close 明显低于 high 近似
    if prev is not None and last.range_ > 0:
        prev_close = prev.close
        limit_price = prev_close * (1 + th)
        # 盘中最高价达到/接近涨停价，但收盘涨幅 < 阈值*0.8
        if last.high >= limit_price * tol and pct < th * 0.8:
            results.append({"key": "broken_limit_up", "name": "炸板（涨停打开）", "direction": "short", "score_hint": -12})

    # ---- 开板回封：昨日炸板，今日重新封板 ----
    if len(df) >= 3 and is_limit_up:
        prev2 = row_to_candle(df.iloc[-3])
        prev_close = prev2.close
        limit_price = prev_close * (1 + th)
        if prev.high >= limit_price * tol and prev.pct_chg / 100.0 < th * 0.8:
            results.append({"key": "re_seal_limit_up", "name": "开板回封", "direction": "long", "score_hint": 10})

    # ---- 冲高回落长上影 ----
    upper_ratio = last.upper_shadow / last.range_ if last.range_ > 0 else 0
    if upper_ratio >= 0.5 and last.range_ > 0 and (last.upper_shadow >= (last.body or avg_b) * 2):
        results.append({"key": "long_upper_shadow_pullback", "name": "冲高回落长上影", "direction": "short", "score_hint": -8})

    # ---- 量能相关 ----
    vol_ratio = last.volume / avg_v if avg_v > 0 else 1.0
    high_vol = vol_ratio >= vol_cfg.get("volume_ratio_high", 2.0)
    low_vol = vol_ratio <= vol_cfg.get("volume_ratio_low", 0.5)

    # 高位放量长阴：前期处于相对高位 + 今日放量大阴
    recent_high = df["high"].tail(20).max() if len(df) >= 20 else df["high"].max()
    near_high = last.close >= recent_high * 0.92
    large_body = avg_b > 0 and last.body >= avg_b * candle_cfg.get("large_body_mult", 2.0)
    if high_vol and last.is_down and large_body and near_high:
        results.append({"key": "high_vol_long_bearish", "name": "高位放量长阴", "direction": "short", "score_hint": -18})

    # 缩量反抽：下跌后阳线但量能不足
    if last.is_up and low_vol:
        # 近5日多为下跌
        tail5 = df.tail(5)
        down_count = int((tail5["close"] < tail5["open"]).sum())
        if down_count >= 3:
            results.append({"key": "weak_rebound_low_volume", "name": "缩量反抽", "direction": "short", "score_hint": -6})

    # ---- 连跌后首阳 ----
    if len(df) >= 5 and last.is_up:
        prev4 = df.iloc[-5:-1]
        down_streak = int((prev4["close"] < prev4["open"]).sum())
        if down_streak >= 4:
            results.append({"key": "first_green_after_decline", "name": "连跌后首阳", "direction": "long", "score_hint": 8})

    # ---- 跌停后3日修复检查 ----
    if len(df) >= 4:
        base = df.iloc[-4]
        base_candle = row_to_candle(base)
        if base_candle.pct_chg / 100.0 <= -th * tol:
            # 之后3根是否收复跌停实体
            recover = df.iloc[-3:]["close"].max()
            if recover >= base_candle.open:
                results.append({"key": "recovered_after_limit_down", "name": "跌停后修复", "direction": "long", "score_hint": 6})
            else:
                results.append({"key": "unrecovered_limit_down", "name": "跌停未修复", "direction": "short", "score_hint": -8})

    # ---- 反包失败：今日大阳/大阴，次日反向吞没但收盘又回到前根中点下 ----
    if prev is not None and avg_b > 0:
        if prev.is_up and last.is_down and last.body >= prev.body and last.close < prev.mid:
            results.append({"key": "bullish_failure", "name": "反包失败", "direction": "short", "score_hint": -10})

    # 统一补充形态解释字段
    for r in results:
        if "explanation" not in r:
            r["explanation"] = get_explanation(r["key"])

    return results
