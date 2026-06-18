"""成交量/换手率特征提取。

OBV、MFI、量价背离检测。
"""

from __future__ import annotations

import pandas as pd


def compute_obv(df: pd.DataFrame) -> pd.Series:
    """OBV (On-Balance Volume) 能量潮。

    OBV = 前日OBV + sign(close - prev_close) * volume
    上涨日 + volume，下跌日 - volume。
    """
    close = df["close"]
    volume = df["volume"]
    sign = (close.diff() > 0).astype(int) * 2 - 1  # 1: up, -1: down
    obv = (sign * volume).cumsum()
    return obv


def compute_mfi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """MFI (Money Flow Index) 资金流量指数。

    类似 RSI，但使用成交额加权的价格变化。
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    money_flow = typical_price * df["volume"]

    positive_flow = pd.Series(0.0, index=df.index)
    negative_flow = pd.Series(0.0, index=df.index)

    diff = typical_price.diff()
    pos_mask = diff > 0
    neg_mask = diff < 0
    positive_flow[pos_mask] = money_flow[pos_mask]
    negative_flow[neg_mask] = money_flow[neg_mask]

    pos_sum = positive_flow.rolling(window, min_periods=1).sum()
    neg_sum = negative_flow.rolling(window, min_periods=1).sum()

    mfi = 100.0 - (100.0 / (1.0 + pos_sum / (neg_sum + 1e-10)))
    return mfi


def detect_bearish_divergence(df: pd.DataFrame, window: int = 60) -> dict:
    """看跌背离检测：价格创新高但 OBV/MFI 未创新高。"""
    if df is None or len(df) < window + 5:
        return {"bearish_divergence": False, "obv_divergence": False, "mfi_divergence": False}

    close = df["close"]
    obv = compute_obv(df)
    mfi = compute_mfi(df)

    # 近 window 日最高价
    high_window = close.tail(window)
    price_new_high = float(close.iloc[-1]) >= float(high_window.max()) * 0.99

    # OBV 未创新高
    obv_window = obv.tail(window)
    obv_not_high = float(obv.iloc[-1]) < float(obv_window.max()) * 0.99

    # MFI 未创新高
    mfi_window = mfi.tail(window)
    mfi_not_high = float(mfi.iloc[-1]) < float(mfi_window.max()) * 0.99

    obv_div = price_new_high and obv_not_high
    mfi_div = price_new_high and mfi_not_high

    return {
        "bearish_divergence": obv_div or mfi_div,
        "obv_divergence": obv_div,
        "mfi_divergence": mfi_div,
        "price_new_high": price_new_high,
    }


def detect_bullish_divergence(df: pd.DataFrame, window: int = 60) -> dict:
    """看涨背离检测：价格创新低但 OBV/MFI 未创新低。"""
    if df is None or len(df) < window + 5:
        return {"bullish_divergence": False, "obv_divergence": False, "mfi_divergence": False}

    close = df["close"]
    obv = compute_obv(df)
    mfi = compute_mfi(df)

    low_window = close.tail(window)
    price_new_low = float(close.iloc[-1]) <= float(low_window.min()) * 1.01

    obv_window = obv.tail(window)
    obv_not_low = float(obv.iloc[-1]) > float(obv_window.min()) * 1.01

    mfi_window = mfi.tail(window)
    mfi_not_low = float(mfi.iloc[-1]) > float(mfi_window.min()) * 1.01

    obv_div = price_new_low and obv_not_low
    mfi_div = price_new_low and mfi_not_low

    return {
        "bullish_divergence": obv_div or mfi_div,
        "obv_divergence": obv_div,
        "mfi_divergence": mfi_div,
        "price_new_low": price_new_low,
    }
