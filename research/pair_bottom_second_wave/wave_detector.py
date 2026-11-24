"""Wave 1 and Prior Peak Detection Engine (Strict Point-in-Time).

Identifies the first upward impulsive wave (Wave 1) prior to each signal day
strictly using available historical information without lookahead bias.
"""
from __future__ import annotations

from typing import Dict, List, Tuple
import numpy as np
import pandas as pd


class WaveDetector:
    def __init__(self, lookback_window: int = 120, min_runup_pct: float = 0.50):
        self.lookback_window = lookback_window
        self.min_runup_pct = min_runup_pct

    def detect_wave1_for_stock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detects Wave 1 run-up and prior peak for a single stock series.
        
        Requires columns: ['trade_date', 'high', 'low', 'close'] sorted by trade_date.
        """
        n = len(df)
        if n < self.lookback_window:
            df["wave1_valid"] = False
            df["wave1_runup_pct"] = np.nan
            df["prior_peak_price"] = np.nan
            df["prior_peak_date"] = np.nan
            df["prior_peak_idx"] = np.nan
            df["prior_trough_price"] = np.nan
            df["peak_to_signal_days"] = np.nan
            return df

        highs = df["high"].values
        lows = df["low"].values
        dates = df["trade_date"].values

        wave1_valid = np.zeros(n, dtype=bool)
        runup_pcts = np.full(n, np.nan)
        peak_prices = np.full(n, np.nan)
        peak_dates = np.full(n, np.nan)
        peak_indices = np.full(n, np.nan)
        trough_prices = np.full(n, np.nan)
        peak_to_signal_days = np.full(n, np.nan)

        w = self.lookback_window
        for i in range(w, n):
            # Lookback window prior to current day: [i-w, i-1]
            window_highs = highs[i - w : i]
            window_dates = dates[i - w : i]
            
            # Find peak in the window
            rel_peak_idx = int(np.argmax(window_highs))
            abs_peak_idx = i - w + rel_peak_idx
            p_price = window_highs[rel_peak_idx]
            p_date = window_dates[rel_peak_idx]

            # Peak must be at least 5 days prior to current day to avoid immediate continuation
            days_from_peak = i - abs_peak_idx
            if days_from_peak < 5:
                continue

            # Prior trough before peak: [i-w, abs_peak_idx]
            sub_lows = lows[i - w : abs_peak_idx + 1]
            if len(sub_lows) < 3:
                continue
            t_price = np.min(sub_lows)

            if t_price <= 0:
                continue

            runup = (p_price / t_price) - 1.0

            runup_pcts[i] = runup
            peak_prices[i] = p_price
            peak_dates[i] = p_date
            peak_indices[i] = abs_peak_idx
            trough_prices[i] = t_price
            peak_to_signal_days[i] = days_from_peak

            if runup >= self.min_runup_pct:
                wave1_valid[i] = True

        df["wave1_valid"] = wave1_valid
        df["wave1_runup_pct"] = runup_pcts
        df["prior_peak_price"] = peak_prices
        df["prior_peak_date"] = peak_dates
        df["prior_peak_idx"] = peak_indices
        df["prior_trough_price"] = trough_prices
        df["peak_to_signal_days"] = peak_to_signal_days

        return df
