"""Signal Detector for Pair Bottom Patterns (PAIR_BOTTOM_FACTOR_V1).

Implements exact specification from Chapter 3 of pair_bottom_backtest_plan_v1.md:
- Price digit pattern classification (Decimal precision tick check)
- Patterns: ABAB, AAAA, ABBA, DECIMAL_PAIR
- Signals: PB01 (Standard), PB02 (120d), PB03 (Pair Close), PB04 (Pair Support),
           PB05 (Double Bottom), PB06 (Reversal Confirmation)
- Episode cooldown and deduplication.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


def classify_pair_price(price: Any) -> Dict[str, Any]:
    """Classifies a price into pair pattern categories according to Section 3.1."""
    try:
        p = Decimal(str(price))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid price: {price}") from exc

    if not p.is_finite() or p <= 0 or p * 100 != (p * 100).to_integral_value():
        raise ValueError(f"Price must be positive and exactly tick-aligned: {price}")

    cents = int(p * 100)
    integer, fraction = divmod(cents, 100)
    ab, cd = f"{integer:02d}", f"{fraction:02d}"
    eligible = 10 <= integer <= 99

    flags = {
        "pair_aaaa": eligible and len(set(ab + cd)) == 1,
        "pair_abab": eligible and ab == cd,
        "pair_abba": eligible and ab == cd[::-1],
        "pair_decimal": cd[0] == cd[1],
    }
    exclusive = next(
        (
            name
            for flag, name in [
                ("pair_aaaa", "AAAA"),
                ("pair_abab", "ABAB"),
                ("pair_abba", "ABBA"),
                ("pair_decimal", "DECIMAL_PAIR"),
            ]
            if flags[flag]
        ),
        "NONE",
    )
    return {
        "price_cents": cents,
        "eligible_two_digit": eligible,
        **flags,
        "exclusive_pattern": exclusive,
    }


def is_pair_pattern(price: float, pattern_type: str = "ABAB_INCLUDING_AAAA") -> bool:
    try:
        res = classify_pair_price(round(price, 2))
        if pattern_type == "ABAB_INCLUDING_AAAA":
            return res["pair_abab"] or res["pair_aaaa"]
        elif pattern_type == "AAAA":
            return res["pair_aaaa"]
        elif pattern_type == "ABAB":
            return res["pair_abab"]
        elif pattern_type == "ABBA":
            return res["pair_abba"]
        elif pattern_type == "DECIMAL_PAIR":
            return res["pair_decimal"]
        elif pattern_type == "ANY_PAIR":
            return res["exclusive_pattern"] != "NONE"
        return False
    except ValueError:
        return False


class PairBottomSignalDetector:
    def __init__(
        self,
        primary_pattern: str = "ABAB_INCLUDING_AAAA",
        cooldown_days: int = 20,
    ):
        self.primary_pattern = primary_pattern
        self.cooldown_days = cooldown_days

    def detect_signals_for_stock(self, df_stock: pd.DataFrame) -> pd.DataFrame:
        """Detects PB01 to PB06 on a single stock's time series (sorted ascending by trade_date)."""
        if len(df_stock) < 60:
            return pd.DataFrame()

        df = df_stock.copy().sort_values("trade_date").reset_index(drop=True)

        # Comparable price (HFQ: price * adj_factor)
        df["comp_low"] = df["low"] * df["adj_factor"]
        df["comp_close"] = df["close"] * df["adj_factor"]
        df["comp_high"] = df["high"] * df["adj_factor"]

        # Rolling minimums (excluding current day: shift 1)
        df["min_low_20d"] = df["comp_low"].shift(1).rolling(19, min_periods=19).min()
        df["min_low_60d"] = df["comp_low"].shift(1).rolling(59, min_periods=59).min()
        df["min_low_120d"] = df["comp_low"].shift(1).rolling(119, min_periods=119).min()

        # Rolling max close for drawdown
        df["max_close_60d"] = df["comp_close"].rolling(60, min_periods=20).max()
        df["drawdown_60d"] = (df["max_close_60d"] - df["comp_close"]) / df["max_close_60d"]

        # Vectorized pattern detection on unadjusted prices
        def check_p(val, ptype):
            if pd.isna(val) or val <= 0:
                return False
            return is_pair_pattern(val, ptype)

        low_pattern = [check_p(v, self.primary_pattern) for v in df["low"]]
        close_pattern = [check_p(v, self.primary_pattern) for v in df["close"]]
        
        # Placebo pattern: ending in .00 or .05 or .50
        def check_placebo(val):
            if pd.isna(val) or val <= 0:
                return False
            cents = int(round(val * 100)) % 100
            return cents in (0, 5, 50)
            
        low_placebo = [check_placebo(v) for v in df["low"]]

        df["is_pair_low"] = low_pattern
        df["is_pair_close"] = close_pattern
        df["is_placebo_low"] = low_placebo

        # Ordinary new lows (for control matching)
        df["is_new_low_60d"] = (df["comp_low"] <= df["min_low_60d"])
        df["is_new_low_20d"] = (df["comp_low"] <= df["min_low_20d"])
        df["is_new_low_120d"] = (df["comp_low"] <= df["min_low_120d"])

        # Signal types:
        # PB01: standard pair bottom (pair low and 60d new low)
        df["sig_PB01"] = df["is_pair_low"] & df["is_new_low_60d"]
        
        # PB02: 120d pair bottom
        df["sig_PB02"] = df["is_pair_low"] & df["is_new_low_120d"]
        
        # PB03: pair close (close is pair pattern, no new low required)
        df["sig_PB03"] = df["is_pair_close"]

        # PB04: pair support (pair low or close is within 3% of past 60d min)
        df["dist_to_min60"] = (df["comp_low"] - df["min_low_60d"]) / df["min_low_60d"]
        df["dist_close_to_min60"] = (df["comp_close"] - df["min_low_60d"]) / df["min_low_60d"]
        df["sig_PB04"] = (
            (df["is_pair_low"] & df["dist_to_min60"].between(-0.01, 0.03)) |
            (df["is_pair_close"] & df["dist_close_to_min60"].between(-0.01, 0.03))
        ) & (~df["sig_PB01"])

        # PB05: double bottom retest (vectorized numpy)
        pb05_flags = np.zeros(len(df), dtype=bool)
        comp_low_v = df["comp_low"].values
        comp_high_v = df["comp_high"].values
        is_pair_low_v = df["is_pair_low"].values

        for i in range(35, len(df)):
            if not is_pair_low_v[i]:
                continue
            curr_low = comp_low_v[i]
            window_lows = comp_low_v[i-30:i-5]
            if len(window_lows) == 0:
                continue
            prior_min_idx = np.argmin(window_lows)
            prior_min_val = window_lows[prior_min_idx]
            abs_prior_idx = (i - 30) + prior_min_idx
            intermediate_high = np.max(comp_high_v[abs_prior_idx:i+1])
            if intermediate_high >= prior_min_val * 1.05:
                if abs(curr_low - prior_min_val) / prior_min_val <= 0.03:
                    pb05_flags[i] = True
        df["sig_PB05"] = pb05_flags

        # PB06: reversal confirmation (vectorized numpy)
        pb06_flags = np.zeros(len(df), dtype=bool)
        df["vol_ma5"] = df["vol"].shift(1).rolling(5).mean()
        df["vol_ratio"] = df["vol"] / df["vol_ma5"]
        vol_ratio_v = df["vol_ratio"].values
        comp_close_v = df["comp_close"].values
        sig_prior_v = (df["sig_PB01"].values) | pb05_flags

        for i in range(10, len(df)):
            if np.any(sig_prior_v[i-10:i-5]) and not np.isnan(vol_ratio_v[i]) and vol_ratio_v[i] >= 1.2:
                cand_mask = sig_prior_v[i-10:i-5]
                cand_highs = comp_high_v[i-10:i-5][cand_mask]
                if len(cand_highs) > 0 and comp_close_v[i] > np.max(cand_highs):
                    pb06_flags[i] = True
        df["sig_PB06"] = pb06_flags

        # Standard Control Group: Ordinary 60-day new low WITHOUT pair pattern
        df["is_control_60d"] = (~df["is_pair_low"]) & df["is_new_low_60d"]
        
        # Placebo Control Group: 60-day new low with .00, .05, .50 endings
        df["is_placebo_60d"] = df["is_placebo_low"] & df["is_new_low_60d"]

        # Control Group for PB03: Ordinary close (no pair pattern)
        df["is_control_close"] = (~df["is_pair_close"])

        # Cooldown deduplication (keep first signal in every 20-day window)
        def apply_cooldown(mask: pd.Series) -> pd.Series:
            result = np.zeros(len(mask), dtype=bool)
            last_idx = -999
            for idx in mask[mask].index:
                if idx - last_idx >= self.cooldown_days:
                    result[idx] = True
                    last_idx = idx
            return pd.Series(result, index=mask.index)

        df["sig_PB01_dedup"] = apply_cooldown(df["sig_PB01"])
        df["sig_PB02_dedup"] = apply_cooldown(df["sig_PB02"])
        df["sig_PB03_dedup"] = apply_cooldown(df["sig_PB03"])
        df["sig_PB04_dedup"] = apply_cooldown(df["sig_PB04"])
        df["sig_PB05_dedup"] = apply_cooldown(df["sig_PB05"])
        df["sig_PB06_dedup"] = apply_cooldown(df["sig_PB06"])
        df["control_60d_dedup"] = apply_cooldown(df["is_control_60d"])
        df["placebo_60d_dedup"] = apply_cooldown(df["is_placebo_60d"])

        return df
