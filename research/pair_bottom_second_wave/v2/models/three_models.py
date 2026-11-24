"""Phase 3: Three Reversal Models Engine & Comprehensive IC Analytics.

Closes P0-05 and P0-11 by splitting into short-term rebound, intermediate reversal,
and long-term main wave models, and reporting cross-sectional vs pooled Rank IC.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


class ThreeModelsEngine:
    """Implements SWI_REBOUND_V2, SWI_REVERSAL_V2, and SWI_MAIN_WAVE_V2."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def compute_all_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes factor scores for all three models and attaches them to df."""
        res = df.copy()
        res["score_rebound_v2"] = self.score_rebound_model(res)
        res["score_reversal_v2"] = self.score_reversal_model(res)
        res["score_main_wave_v2"] = self.score_main_wave_model(res)
        return res

    def score_rebound_model(self, df: pd.DataFrame) -> pd.Series:
        """SWI_REBOUND_V2 (5-20d oversold bounce): Focus on RSI, drawdown depth, lower shadow."""
        rsi = df.get("rsi_14", pd.Series(50.0, index=df.index)).fillna(50.0)
        dd = df.get("drawdown_from_peak_close", pd.Series(0.0, index=df.index)).fillna(0.0)
        shadow = df.get("lower_shadow_ratio", pd.Series(0.0, index=df.index)).fillna(0.0)
        clv = df.get("clv", pd.Series(0.0, index=df.index)).fillna(0.0)
        v_ratio = df.get("vol_ratio_20_60", pd.Series(1.0, index=df.index)).fillna(1.0)

        # Lower RSI (<35) -> higher rebound potential
        s_rsi = np.clip((50.0 - rsi) * 2.5, 0.0, 100.0)
        # Deep drawdown (-40% to -60%)
        s_dd = np.where((-0.65 <= dd) & (dd <= -0.35), 100.0, 40.0)
        # Long lower shadow + positive CLV
        s_candle = np.clip(shadow * 150.0 + (clv + 1.0) * 25.0, 0.0, 100.0)
        # Volume contraction
        s_vol = np.where(v_ratio < 0.75, 100.0, 50.0)

        score = 0.35 * s_rsi + 0.30 * s_dd + 0.20 * s_candle + 0.15 * s_vol
        return pd.Series(score, index=df.index)

    def score_reversal_model(self, df: pd.DataFrame) -> pd.Series:
        """SWI_REVERSAL_V2 (20-40d trend reversal): Focus on MA20 cross, volume expansion, trend slope."""
        dist_ma20 = df.get("dist_ma20", pd.Series(0.0, index=df.index)).fillna(0.0)
        v_turn = df.get("vol_ratio_5_20", pd.Series(1.0, index=df.index)).fillna(1.0)
        dd = df.get("drawdown_from_peak_close", pd.Series(0.0, index=df.index)).fillna(0.0)
        is_pair = (df.get("is_pair_low", pd.Series(False, index=df.index)) | df.get("is_pair_close", pd.Series(False, index=df.index))).astype(float)

        s_ma = np.where(dist_ma20 > -0.02, 100.0, np.where(dist_ma20 > -0.08, 60.0, 20.0))
        s_vol = np.where(v_turn >= 1.20, 100.0, np.where(v_turn >= 1.05, 70.0, 30.0))
        s_dd = np.where((-0.60 <= dd) & (dd <= -0.40), 100.0, 50.0)
        s_pair = is_pair * 100.0

        score = 0.35 * s_ma + 0.30 * s_vol + 0.25 * s_dd + 0.10 * s_pair
        return pd.Series(score, index=df.index)

    def score_main_wave_model(self, df: pd.DataFrame) -> pd.Series:
        """SWI_MAIN_WAVE_V2 (40-120d major run-up): Focus on Wave 1 impulse, events, healthy reset."""
        runup = df.get("wave1_runup_pct", pd.Series(0.0, index=df.index)).fillna(0.0)
        dd = df.get("drawdown_from_peak_close", pd.Series(0.0, index=df.index)).fillna(0.0)
        has_buyback = df.get("buyback_active", pd.Series(False, index=df.index)).astype(float)
        has_placement = df.get("deep_discount_placement", pd.Series(False, index=df.index)).astype(float)
        dist_ma20 = df.get("dist_ma20", pd.Series(0.0, index=df.index)).fillna(0.0)

        s_runup = np.clip(runup * 100.0, 0.0, 100.0)
        s_dd = np.where((-0.60 <= dd) & (dd <= -0.40), 100.0, np.where(dd < -0.65, 30.0, 50.0))
        s_event = (has_buyback * 50.0 + has_placement * 50.0)
        s_trend = np.where(dist_ma20 >= -0.05, 100.0, 40.0)

        score = 0.30 * s_runup + 0.30 * s_dd + 0.25 * s_event + 0.15 * s_trend
        return pd.Series(score, index=df.index)

    def evaluate_ic_analytics(
        self,
        df: pd.DataFrame,
        score_col: str = "score_rebound_v2",
        horizons: List[int] = [20, 40, 60],
    ) -> Dict[str, Any]:
        """Calculates both cross-sectional daily IC and pooled IC to clarify P0-05."""
        out = {}
        for h in horizons:
            ret_col = f"ret_{h}d"
            if ret_col not in df.columns:
                continue

            sub = df[[score_col, ret_col, "trade_date"]].dropna()
            if len(sub) < 30:
                continue

            # 1. Overall Pooled Rank IC
            pooled_ic, pooled_p = spearmanr(sub[score_col], sub[ret_col])

            # 2. Daily Cross-Sectional Rank IC
            daily_records = []
            for dt, g in sub.groupby("trade_date"):
                if len(g) >= 5:
                    r_ic, _ = spearmanr(g[score_col], g[ret_col])
                    if not np.isnan(r_ic):
                        daily_records.append({"trade_date": dt, "ic": r_ic})

            daily_df = pd.DataFrame(daily_records)
            if not daily_df.empty:
                mean_ic = float(daily_df["ic"].mean())
                std_ic = float(daily_df["ic"].std())
                n_days = len(daily_df)
                icir = float(mean_ic / (std_ic + 1e-6))
                t_stat = float(mean_ic / (std_ic / np.sqrt(n_days) + 1e-6)) if n_days > 1 else np.nan
                win_rate = float((daily_df["ic"] > 0).mean())
            else:
                mean_ic, std_ic, n_days, icir, t_stat, win_rate = np.nan, np.nan, 0, np.nan, np.nan, np.nan

            out[f"{h}d"] = {
                "pooled_rank_ic": float(pooled_ic),
                "pooled_p_value": float(pooled_p),
                "daily_rank_ic_mean": mean_ic,
                "daily_rank_ic_std": std_ic,
                "daily_ic_count": n_days,
                "daily_ic_t_stat": t_stat,
                "ic_ir": icir,
                "daily_win_rate": win_rate,
            }

        return out


# Alias for backward and forward compatibility
ThreeModels = ThreeModelsEngine

