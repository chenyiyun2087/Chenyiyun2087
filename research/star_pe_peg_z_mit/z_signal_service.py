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
        """Compute rolling Z-score, fresh entry cross, and exit signal for a single stock."""
        df = market_frame.copy()
        adj_close = df["adj_close"]
        rolling = adj_close.rolling(window=self.window)
        mean = rolling.mean()
        std = rolling.std(ddof=1)

        z = (adj_close - mean) / std
        df["z"] = z

        # Fresh cross: previous day z >= entry_threshold and current day z < entry_threshold
        z_prev = z.shift(1)
        fresh_entry = (z_prev >= self.entry_threshold) & (z < self.entry_threshold)
        exit_intent = z >= self.exit_threshold

        df["fresh_entry"] = fresh_entry.fillna(False).astype(bool)
        df["exit_intent"] = exit_intent.fillna(False).astype(bool)
        return df
