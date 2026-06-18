"""均线趋势上下文。

判断最新收盘价相对 MA5/10/20/60 的位置，输出趋势状态字符串与方向分。
"""

from __future__ import annotations

import pandas as pd

from ..utils import load_settings


def compute_ma(df: pd.DataFrame) -> dict[str, float]:
    """计算各周期均线最新值。"""
    periods = load_settings().get("ma", {}).get("periods", [5, 10, 20, 60])
    out: dict[str, float] = {}
    close = df["close"]
    for p in periods:
        if len(close) >= p:
            out[f"ma{p}"] = float(close.tail(p).mean())
    return out


def analyze_trend(df: pd.DataFrame) -> dict:
    """分析趋势上下文。

    返回:
        {
          "trend_context": "above_ma5_ma10" | "below_ma10_ma20" | ...,
          "mas": {ma5:.., ma10:.., ma20:.., ma60:..},
          "direction": "up" | "down" | "tangle",
          "score_hint": int
        }
    """
    if df is None or df.empty:
        return {"trend_context": "unknown", "mas": {}, "direction": "tangle", "score_hint": 0}

    mas = compute_ma(df)
    close = float(df.iloc[-1]["close"])

    # 收盘价相对各均线位置
    above = {k: close > v for k, v in mas.items()}
    ma5 = mas.get("ma5")
    ma10 = mas.get("ma10")
    ma20 = mas.get("ma20")
    ma60 = mas.get("ma60")

    # 多头排列：ma5>ma10>ma20；空头排列反之
    def _ordered(a, b, c):
        return a is not None and b is not None and c is not None and a > b > c

    bull_align = _ordered(ma5, ma10, ma20)
    bear_align = _ordered(ma20, ma10, ma5)  # ma20>ma10>ma5

    tangle_ratio = load_settings().get("ma", {}).get("tangle_ratio", 0.01)

    # 缠绕：ma5/10/20 差距很小
    tangle = False
    if ma5 and ma10 and ma20:
        mvals = [ma5, ma10, ma20]
        if (max(mvals) - min(mvals)) / close < tangle_ratio:
            tangle = True

    if bull_align and close > ma5:
        ctx, direction, hint = "above_ma5_ma10_ma20_bull_align", "up", 15
    elif bear_align and close < ma5:
        ctx, direction, hint = "below_ma5_ma10_ma20_bear_align", "down", -15
    elif ma10 and ma20 and close < ma10 and close < ma20:
        ctx, direction, hint = "below_ma10_ma20", "down", -10
    elif ma10 and ma20 and close > ma10 and close > ma20:
        ctx, direction, hint = "above_ma10_ma20", "up", 10
    elif ma5 and ma10 and close > ma5 and close > ma10:
        ctx, direction, hint = "above_ma5_ma10", "up", 6
    elif ma5 and ma10 and close < ma5 and close < ma10:
        ctx, direction, hint = "below_ma5_ma10", "down", -6
    elif tangle:
        ctx, direction, hint = "ma_tangle", "tangle", 0
    else:
        ctx, direction, hint = "mixed", "tangle", 0

    # MA60 方向加成
    if ma60 and close > ma60 and direction == "up":
        hint += 2
    elif ma60 and close < ma60 and direction == "down":
        hint -= 2

    return {"trend_context": ctx, "mas": mas, "direction": direction, "score_hint": hint}
