"""Pair Bottom and Support Signal Detector (PB01 to PB10).

Implements exact decimal tick pattern classification and signal emission
with episode cooldown deduplication.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Tuple
import numpy as np
import pandas as pd


def classify_exact_price_pattern(price_float: float) -> Tuple[Dict[str, bool], str]:
    """Classifies unadjusted price into AAAA, ABAB, ABBA, DECIMAL_PAIR patterns."""
    if np.isnan(price_float) or price_float <= 0:
        return {"AAAA": False, "ABAB": False, "ABBA": False, "DECIMAL_PAIR": False}, "NONE"

    d = Decimal(str(price_float)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents_total = int(d * 100)
    int_part = cents_total // 100
    frac_part = cents_total % 100

    d1 = frac_part // 10
    d2 = frac_part % 10

    i1 = (int_part % 100) // 10
    i2 = int_part % 10

    is_decimal_pair = (d1 == d2)
    is_aaaa = (int_part >= 10) and (i1 == i2) and (i2 == d1) and (d1 == d2)
    is_abab = (int_part >= 10) and (i1 == d1) and (i2 == d2) and not is_aaaa
    is_abba = (int_part >= 10) and (i1 == d2) and (i2 == d1) and not is_aaaa and not is_abab

    patterns = {
        "AAAA": is_aaaa,
        "ABAB": is_abab,
        "ABBA": is_abba,
        "DECIMAL_PAIR": is_decimal_pair,
    }

    if is_aaaa:
        exclusive = "AAAA"
    elif is_abab:
        exclusive = "ABAB"
    elif is_abba:
        exclusive = "ABBA"
    elif is_decimal_pair:
        exclusive = "DECIMAL_PAIR"
    else:
        exclusive = "NONE"

    return patterns, exclusive


class PairSignalDetector:
    def __init__(self, cooldown_days: int = 20):
        self.cooldown_days = cooldown_days

    def detect_signals_for_stock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Emits PB01 to PB10 signals for a single stock DataFrame."""
        n = len(df)
        if n < 60:
            return pd.DataFrame()

        lows = df["low"].values
        closes = df["close"].values
        vols = df["vol"].values if "vol" in df.columns else np.ones(n)

        # 1. Classify price patterns
        is_pair_low = np.zeros(n, dtype=bool)
        is_pair_close = np.zeros(n, dtype=bool)
        exclusive_pat_low = ["NONE"] * n
        exclusive_pat_close = ["NONE"] * n

        for i in range(n):
            p_low, exc_low = classify_exact_price_pattern(lows[i])
            is_pair_low[i] = any(p_low.values())
            exclusive_pat_low[i] = exc_low

            p_cls, exc_cls = classify_exact_price_pattern(closes[i])
            is_pair_close[i] = any(p_cls.values())
            exclusive_pat_close[i] = exc_cls

        df["is_pair_low"] = is_pair_low
        df["is_pair_close"] = is_pair_close
        df["exclusive_pattern_low"] = exclusive_pat_low
        df["exclusive_pattern_close"] = exclusive_pat_close

        # 2. Window new lows
        sig_PB01 = is_pair_low.copy()
        sig_PB02 = is_pair_close.copy()
        sig_PB03 = np.zeros(n, dtype=bool)  # 20d new low
        sig_PB04 = np.zeros(n, dtype=bool)  # 60d new low
        sig_PB05 = np.zeros(n, dtype=bool)  # 120d new low
        sig_PB06 = np.zeros(n, dtype=bool)  # Secondary test (within 3% of prior low)
        sig_PB07 = np.zeros(n, dtype=bool)  # Volume contraction at bottom
        sig_PB08 = np.zeros(n, dtype=bool)  # Confirmed reversal (Close > MA20)

        # Controls
        ctrl_60d = np.zeros(n, dtype=bool)
        placebo_60d = np.zeros(n, dtype=bool)

        # Vectorized rolling lows and volume moving averages
        s_lows = pd.Series(lows)
        s_vols = pd.Series(vols)
        s_closes = pd.Series(closes)

        min_20 = s_lows.rolling(20, min_periods=20).min().shift(1).values
        min_60 = s_lows.rolling(60, min_periods=60).min().shift(1).values
        min_120 = s_lows.rolling(120, min_periods=120).min().shift(1).values
        ma_20_cls = s_closes.rolling(20, min_periods=20).mean().values
        vol_20_avg = s_vols.rolling(20, min_periods=20).mean().shift(1).values

        # PB03, PB04, PB05
        mask_20 = ~np.isnan(min_20)
        mask_60 = ~np.isnan(min_60)
        mask_120 = ~np.isnan(min_120)

        sig_PB03[mask_20] = is_pair_low[mask_20] & (lows[mask_20] <= min_20[mask_20])
        sig_PB04[mask_60] = is_pair_low[mask_60] & (lows[mask_60] <= min_60[mask_60])
        sig_PB05[mask_120] = is_pair_low[mask_120] & (lows[mask_120] <= min_120[mask_120])

        ctrl_60d[mask_60] = (lows[mask_60] <= min_60[mask_60]) & (~is_pair_low[mask_60])

        # Placebo (.00, .05, .50)
        for i in range(60, n):
            c_tot = int(Decimal(str(lows[i])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
            fp = c_tot % 100
            if fp in [0, 5, 50] and lows[i] <= min_60[i]:
                placebo_60d[i] = True

        # PB06: Secondary test within 3% of prior 5 to 60 days stage low
        for i in range(25, n):
            prior_min = np.min(lows[max(0, i - 60) : max(0, i - 5)])
            if prior_min > 0 and abs(lows[i] / prior_min - 1.0) <= 0.03:
                sig_PB06[i] = True

        # PB07: Volume contraction (current vol < 0.75 * 20d avg)
        vol_mask = ~np.isnan(vol_20_avg) & (vol_20_avg > 0)
        sig_PB07[vol_mask] = is_pair_low[vol_mask] & (vols[vol_mask] < 0.75 * vol_20_avg[vol_mask])

        # PB08: Reversal confirmation (Close > MA20)
        ma_mask = ~np.isnan(ma_20_cls)
        sig_PB08[ma_mask] = is_pair_low[ma_mask] & (closes[ma_mask] > ma_20_cls[ma_mask])

        # PB09: Wave 1 + Deep Drawdown (DD40) + Pair Signal
        wave_mask = df.get("wave1_valid", pd.Series(False, index=df.index)).values
        dd40_mask = df.get("is_DD40", pd.Series(False, index=df.index)).values
        sig_PB09 = wave_mask & dd40_mask & (is_pair_low | is_pair_close | sig_PB06)

        # PB10: PB09 + Event (Placement / Buyback / Unlock)
        buyback_mask = df.get("buyback_active", pd.Series(False, index=df.index)).values
        placement_mask = df.get("deep_discount_placement", pd.Series(False, index=df.index)).values
        sig_PB10 = sig_PB09 & (buyback_mask | placement_mask)

        df["sig_PB01"] = sig_PB01
        df["sig_PB02"] = sig_PB02
        df["sig_PB03"] = sig_PB03
        df["sig_PB04"] = sig_PB04
        df["sig_PB05"] = sig_PB05
        df["sig_PB06"] = sig_PB06
        df["sig_PB07"] = sig_PB07
        df["sig_PB08"] = sig_PB08
        df["sig_PB09"] = sig_PB09
        df["sig_PB10"] = sig_PB10
        df["control_60d"] = ctrl_60d
        df["placebo_60d"] = placebo_60d

        # 3. Episode deduplication
        for col in ["sig_PB01", "sig_PB02", "sig_PB03", "sig_PB04", "sig_PB05", "sig_PB06", "sig_PB09", "control_60d", "placebo_60d"]:
            dedup = np.zeros(n, dtype=bool)
            last_sig = -999
            for i in range(n):
                if df[col].iloc[i]:
                    if i - last_sig >= self.cooldown_days:
                        dedup[i] = True
                        last_sig = i
            df[f"{col}_dedup"] = dedup

        return df
