"""Daily Z-score mean-reversion signal service."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class ZSignalService:
    """Calculates 50-day rolling Z-score on adjusted close with ddof=1 and generates entry/exit intents."""

    def __init__(self, window: int = 50, entry_threshold: float = -2.0, exit_threshold: float = 0.0):
        self.window = window
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold

    def compute_signals(self, market_frame: pd.DataFrame) -> pd.DataFrame:
        """Compute rolling Z-score on the latest `window` valid closes, fresh entry cross, and exit signal."""
        df = market_frame.copy()
        valid_close = df["adj_close"].dropna()
        rolling = valid_close.rolling(window=self.window, min_periods=self.window)
        mean = rolling.mean()
        std = rolling.std(ddof=1)

        z_valid = (valid_close - mean) / std
        z_prev_valid = z_valid.shift(1)
        fresh_entry_valid = (z_prev_valid >= self.entry_threshold) & (z_valid < self.entry_threshold)
        exit_intent_valid = z_valid >= self.exit_threshold

        df["z"] = z_valid.reindex(df.index)
        df["fresh_entry"] = fresh_entry_valid.reindex(df.index).fillna(False).astype(bool)
        df["exit_intent"] = exit_intent_valid.reindex(df.index).fillna(False).astype(bool)
        return df
