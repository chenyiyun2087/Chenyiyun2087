"""Drawdown Reset Detection Engine.

Computes Point-in-Time drawdowns from the prior Wave 1 peak,
categorizing resets into DD30, DD40, DD50, and DD60 brackets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class DrawdownDetector:
    def __init__(self, thresholds: list[float] = [0.30, 0.40, 0.50, 0.60]):
        self.thresholds = sorted(thresholds)

    def compute_drawdowns_for_stock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes drawdowns from prior_peak_price strictly in Point-in-Time."""
        if "prior_peak_price" not in df.columns:
            raise ValueError("df must contain 'prior_peak_price' from WaveDetector.")

        closes = df["close"].values
        lows = df["low"].values
        peaks = df["prior_peak_price"].values
        peak_days = df["peak_to_signal_days"].values

        n = len(df)
        dd_close = np.full(n, np.nan)
        dd_low = np.full(n, np.nan)
        dd_slope = np.full(n, np.nan)

        valid_mask = ~np.isnan(peaks) & (peaks > 0)
        dd_close[valid_mask] = (closes[valid_mask] / peaks[valid_mask]) - 1.0
        dd_low[valid_mask] = (lows[valid_mask] / peaks[valid_mask]) - 1.0

        # slope: drawdown / days
        slope_mask = valid_mask & (peak_days > 0)
        dd_slope[slope_mask] = dd_close[slope_mask] / peak_days[slope_mask]

        df["drawdown_from_peak_close"] = dd_close
        df["drawdown_from_peak_low"] = dd_low
        df["drawdown_slope"] = dd_slope

        # Drawdown flags
        df["is_DD30"] = dd_close <= -0.30
        df["is_DD40"] = dd_close <= -0.40
        df["is_DD50"] = dd_close <= -0.50
        df["is_DD60"] = dd_close <= -0.60

        return df
