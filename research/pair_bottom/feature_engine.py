"""Technical, Fundamental and Market Feature Engineering (PAIR_BOTTOM_FACTOR_V1).

Computes features strictly point-in-time (available_at <= decision_at):
- Drawdowns (20d, 60d, 120d, 250d)
- MA distances (MA5, MA20, MA60, MA120)
- RSI(14), ATR%(14), Bollinger position
- Candlestick shapes (lower shadow ratio, close position)
- Consecutive down days
- Volume ratios (5d, 20d)
- Market regime & relative strength
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class PairBottomFeatureEngine:
    def __init__(self):
        pass

    def compute_technical_features_for_stock(self, df_stock: pd.DataFrame) -> pd.DataFrame:
        """Computes technical indicator series on single stock time series."""
        df = df_stock.copy().sort_values("trade_date").reset_index(drop=True)
        c = df["close"].values
        h = df["high"].values
        l = df["low"].values
        o = df["open"].values
        v = df["vol"].values
        n = len(df)

        # 1. Moving averages & distances
        for window in [5, 20, 60, 120]:
            ma = pd.Series(c).rolling(window, min_periods=window // 2).mean().values
            df[f"ma_{window}"] = ma
            df[f"dist_ma{window}"] = np.where(ma > 0, c / ma - 1.0, np.nan)

        # 2. Drawdowns
        for window in [20, 60, 120, 250]:
            roll_max = pd.Series(c).rolling(window, min_periods=window // 4).max().values
            df[f"drawdown_{window}d"] = np.where(roll_max > 0, (roll_max - c) / roll_max, np.nan)

        # 3. RSI(14)
        delta = pd.Series(c).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=7).mean()
        avg_loss = loss.rolling(14, min_periods=7).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # 4. ATR(14) and ATR%
        tr1 = pd.Series(h - l)
        tr2 = (pd.Series(h) - pd.Series(c).shift(1)).abs()
        tr3 = (pd.Series(l) - pd.Series(c).shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_14 = tr.rolling(14, min_periods=7).mean()
        df["atr_pct_14"] = atr_14 / df["close"]

        # 5. Bollinger Bands (20d, 2 std)
        ma20 = df["ma_20"]
        std20 = pd.Series(c).rolling(20, min_periods=10).std()
        upper = ma20 + 2.0 * std20
        lower = ma20 - 2.0 * std20
        band_width = upper - lower
        df["boll_pos"] = np.where(band_width > 0, (c - lower) / band_width, 0.5)

        # 6. Candlestick features
        daily_range = h - l
        lower_body = np.minimum(o, c)
        safe_range = np.where(daily_range > 0, daily_range, 1.0)
        df["lower_shadow_ratio"] = np.where(daily_range > 0, (lower_body - l) / safe_range, 0.0)
        df["close_position"] = np.where(daily_range > 0, (c - l) / safe_range, 0.5)

        # 7. Consecutive down days
        down_days = np.zeros(n, dtype=int)
        cnt = 0
        for i in range(1, n):
            if c[i] < c[i - 1]:
                cnt += 1
            else:
                cnt = 0
            down_days[i] = cnt
        df["consecutive_down_days"] = down_days

        # 8. Volume ratios (5d, 20d)
        vol_s = pd.Series(v)
        v_ma5 = vol_s.shift(1).rolling(5, min_periods=2).mean().values
        v_ma20 = vol_s.shift(1).rolling(20, min_periods=5).mean().values
        df["volume_ratio_5d"] = np.where(v_ma5 > 0, v / v_ma5, np.nan)
        df["volume_ratio_20d"] = np.where(v_ma20 > 0, v / v_ma20, np.nan)

        return df
