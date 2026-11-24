"""Second Wave Initiation Factor (SWI_FACTOR) Engine.

Constructs a reusable, production-ready quantitative factor to identify
the onset of a secondary major upward wave (Wave 2) following an initial
impulsive run-up and a deep healthy drawdown reset.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


class SecondWaveFactorEngine:
    """Calculates the Second Wave Initiation Factor (SWI) score for any stock."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or {
            "w_wave1": 0.20,       # Wave 1 strength & runup quality
            "w_drawdown": 0.25,    # Reset depth & slope flattening
            "w_volume": 0.20,      # Contraction followed by accumulation
            "w_support": 0.20,     # Candle action, lower shadow & CLV
            "w_event": 0.15,       # Corporate events & incentives
        }

    def compute_swi_features_for_stock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes technical and structural component features for SWI factor."""
        n = len(df)
        if n < 60:
            df["swi_score"] = np.nan
            return df

        closes = df["close"].values
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        vols = df["vol"].values if "vol" in df.columns else np.ones(n)

        # 1. Lower Shadow & CLV
        ranges = highs - lows
        ranges[ranges == 0] = 0.01
        lower_body = np.minimum(opens, closes)
        lower_shadow = (lower_body - lows) / ranges
        clv = ((closes - lows) - (highs - closes)) / ranges  # [-1, +1]

        # 2. RSI (14)
        deltas = np.diff(closes, prepend=closes[0])
        gains = np.maximum(deltas, 0)
        losses = np.maximum(-deltas, 0)
        s_gains = pd.Series(gains).rolling(14, min_periods=14).mean().values
        s_losses = pd.Series(losses).rolling(14, min_periods=14).mean().values
        denom = s_gains + s_losses
        denom[denom == 0] = 1.0
        rsi_14 = 100.0 * (s_gains / denom)

        # 3. Volume ratios
        s_vols = pd.Series(vols)
        vol_5 = s_vols.rolling(5, min_periods=5).mean().values
        vol_20 = s_vols.rolling(20, min_periods=20).mean().values
        vol_60 = s_vols.rolling(60, min_periods=60).mean().values
        vol_ratio_5_20 = np.full(n, 1.0)
        vol_ratio_20_60 = np.full(n, 1.0)

        mask_v20 = ~np.isnan(vol_20) & (vol_20 > 0)
        vol_ratio_5_20[mask_v20] = vol_5[mask_v20] / vol_20[mask_v20]

        mask_v60 = ~np.isnan(vol_60) & (vol_60 > 0)
        vol_ratio_20_60[mask_v60] = vol_20[mask_v60] / vol_60[mask_v60]

        # 4. Moving Average Distances
        s_closes = pd.Series(closes)
        ma_20 = s_closes.rolling(20, min_periods=20).mean().values
        ma_60 = s_closes.rolling(60, min_periods=60).mean().values
        dist_ma20 = np.full(n, 0.0)
        dist_ma60 = np.full(n, 0.0)
        mask_ma20 = ~np.isnan(ma_20) & (ma_20 > 0)
        dist_ma20[mask_ma20] = (closes[mask_ma20] / ma_20[mask_ma20]) - 1.0
        mask_ma60 = ~np.isnan(ma_60) & (ma_60 > 0)
        dist_ma60[mask_ma60] = (closes[mask_ma60] / ma_60[mask_ma60]) - 1.0

        # Assign back
        df["lower_shadow_ratio"] = lower_shadow
        df["clv"] = clv
        df["rsi_14"] = rsi_14
        df["vol_ratio_5_20"] = vol_ratio_5_20
        df["vol_ratio_20_60"] = vol_ratio_20_60
        df["dist_ma20"] = dist_ma20
        df["dist_ma60"] = dist_ma60

        # 5. Composite SWI Factor Score calculation
        swi_scores = np.full(n, np.nan)
        wave1_runup = df.get("wave1_runup_pct", pd.Series(np.nan, index=df.index)).values
        dd_close = df.get("drawdown_from_peak_close", pd.Series(np.nan, index=df.index)).values
        is_pair = (df.get("is_pair_low", pd.Series(False, index=df.index)) | df.get("is_pair_close", pd.Series(False, index=df.index))).values
        has_buyback = df.get("buyback_active", pd.Series(False, index=df.index)).values
        has_placement = df.get("deep_discount_placement", pd.Series(False, index=df.index)).values

        for i in range(60, n):
            # S_wave1: 0 to 100
            runup = wave1_runup[i] if not np.isnan(wave1_runup[i]) else 0.0
            if runup >= 1.0:
                s_wave1 = 100.0
            elif runup >= 0.50:
                s_wave1 = 60.0 + (runup - 0.50) / 0.50 * 40.0
            elif runup >= 0.30:
                s_wave1 = 30.0 + (runup - 0.30) / 0.20 * 30.0
            else:
                s_wave1 = max(0.0, runup * 100.0)

            # S_drawdown: sweet spot is -40% to -58%
            dd = dd_close[i] if not np.isnan(dd_close[i]) else 0.0
            if -0.58 <= dd <= -0.40:
                s_dd = 100.0
            elif -0.65 <= dd < -0.40:
                s_dd = 80.0
            elif -0.40 < dd <= -0.30:
                s_dd = 70.0
            elif -0.75 <= dd < -0.65:
                s_dd = 40.0
            elif dd > -0.30:
                s_dd = 20.0
            else:
                s_dd = 10.0  # Over -75% collapse risk

            # S_volume: contraction in medium-term, expansion in short-term
            # Ideal: vol_20_60 < 0.7 (exhaustion) and vol_5_20 >= 1.15 (early inflow)
            v_reversal = 50.0
            if vol_ratio_20_60[i] < 0.75:
                v_reversal += 25.0
            if vol_ratio_5_20[i] >= 1.10:
                v_reversal += 25.0
            s_vol = min(100.0, max(0.0, v_reversal))

            # S_support: lower shadow + CLV + RSI oversold + Pair
            s_supp = 50.0
            if lower_shadow[i] >= 0.30:
                s_supp += 15.0
            if clv[i] >= 0.0:
                s_supp += 15.0
            if 30.0 <= rsi_14[i] <= 45.0:
                s_supp += 10.0
            if is_pair[i]:
                s_supp += 10.0
            s_supp = min(100.0, max(0.0, s_supp))

            # S_event: Corporate catalysts
            s_evt = 30.0
            if has_buyback[i]:
                s_evt += 35.0
            if has_placement[i]:
                s_evt += 35.0
            s_evt = min(100.0, max(0.0, s_evt))

            # Weighted sum
            score = (
                self.weights["w_wave1"] * s_wave1
                + self.weights["w_drawdown"] * s_dd
                + self.weights["w_volume"] * s_vol
                + self.weights["w_support"] * s_supp
                + self.weights["w_event"] * s_evt
            )
            swi_scores[i] = score

        df["swi_score"] = swi_scores
        return df

    def evaluate_factor_performance(
        self,
        panel_df: pd.DataFrame,
        horizons: List[int] = [20, 40, 60],
        score_col: str = "swi_score",
    ) -> Dict[str, Any]:
        """Evaluates Rank IC, Decile Spreads, and Hit Rates for SWI factor."""
        results = {}
        for h in horizons:
            ret_col = f"ret_{h}d"
            if ret_col not in panel_df.columns:
                continue

            sub = panel_df[[score_col, ret_col, "trade_date"]].dropna()
            if len(sub) < 30:
                continue

            # Overall Rank IC
            overall_ic, p_val = spearmanr(sub[score_col], sub[ret_col])

            # Time-series Mean Rank IC
            daily_ics = []
            for dt, g in sub.groupby("trade_date"):
                if len(g) >= 5:
                    r_ic, _ = spearmanr(g[score_col], g[ret_col])
                    if not np.isnan(r_ic):
                        daily_ics.append(r_ic)
            mean_ic = float(np.mean(daily_ics)) if daily_ics else float(overall_ic)
            ic_ir = float(mean_ic / (np.std(daily_ics) + 1e-6)) if len(daily_ics) > 5 else np.nan

            # Decile Buckets (Q1 to Q5 or Q1 to Q10)
            try:
                sub["decile"] = pd.qcut(sub[score_col], q=5, labels=False, duplicates="drop")
                decile_rets = sub.groupby("decile")[ret_col].mean().to_dict()
                top_q = decile_rets.get(4, np.nan)
                bot_q = decile_rets.get(0, np.nan)
                long_short = float(top_q - bot_q) if not np.isnan(top_q) and not np.isnan(bot_q) else np.nan
            except Exception:
                decile_rets = {}
                long_short = np.nan

            results[f"{h}d"] = {
                "rank_ic_overall": float(overall_ic),
                "rank_ic_p_value": float(p_val),
                "rank_ic_mean": mean_ic,
                "ic_ir": ic_ir,
                "decile_returns_5q": {str(k): float(v) for k, v in decile_rets.items()},
                "long_short_spread": long_short,
            }

        return results
