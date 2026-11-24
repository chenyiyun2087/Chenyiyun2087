"""Walk Forward Split with Purging and Embargo for Pair Bottom Research.

Enforces strict out-of-sample discipline:
- Training: 2021-11-24 to 2023-12-31
- Validation: 2024-01-01 to 2024-12-31
- Holdout Test: 2025-01-01 to 2026-11-24
- Purging: Signals whose forward label horizon crosses split boundary are removed from prior split.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import pandas as pd


class WalkForwardSplitter:
    def __init__(
        self,
        train_end: int = 20231231,
        val_end: int = 20241231,
        test_end: int = 20261124,
        primary_horizon: int = 45,
    ):
        self.train_end = train_end
        self.val_end = val_end
        self.test_end = test_end
        self.primary_horizon = primary_horizon

    def split(self, df: pd.DataFrame, date_col: str = "trade_date") -> Dict[str, pd.DataFrame]:
        """Splits signals into Train, Validation, and Test partitions with label purging."""
        if df.empty:
            return {"train": pd.DataFrame(), "validation": pd.DataFrame(), "test": pd.DataFrame()}

        dates = df[date_col].astype(int)

        # Approximate purging: 45 trading days ~ 65 calendar days
        train_purge_cutoff = self.train_end - 215  # conservative cutoff for train signals
        val_purge_cutoff = self.val_end - 215

        train_mask = dates <= train_purge_cutoff
        val_mask = (dates > self.train_end) & (dates <= val_purge_cutoff)
        test_mask = (dates > self.val_end) & (dates <= self.test_end)

        return {
            "train": df[train_mask].copy().reset_index(drop=True),
            "validation": df[val_mask].copy().reset_index(drop=True),
            "test": df[test_mask].copy().reset_index(drop=True),
        }
