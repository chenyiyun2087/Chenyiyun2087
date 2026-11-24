#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWI_FACTOR V2.1: Three Independent Decoupled Models & True IC Analytics.
- REBOUND (5, 10, 20d) with score_rebound_v2
- REVERSAL (20, 30, 40d) with score_reversal_v2
- MAIN_WAVE (40, 60, 90, 120d) with score_main_wave_v2
- True daily cross-sectional Rank IC, HAC/t-stat, p-value, Q1-Q5 quintiles, and feature coverage tracking.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import spearmanr


class ThreeModelsV2_1:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.feature_coverage: Dict[str, float] = {}

    def score_rebound_model(self, df: pd.DataFrame) -> Tuple[pd.Series, Dict[str, Any]]:
        """SWI_REBOUND_V2 (5-20d oversold bounce): Focus on RSI, drawdown depth, lower shadow."""
        n = len(df)
        if n == 0:
            return pd.Series(dtype=float), {"coverage": 0.0}

        rsi = df.get("rsi_14", pd.Series(np.nan, index=df.index))
        dd = df.get("drawdown_from_peak_close", pd.Series(np.nan, index=df.index))
        shadow = df.get("lower_shadow_ratio", pd.Series(np.nan, index=df.index))
        clv = df.get("clv", pd.Series(np.nan, index=df.index))
        v_ratio = df.get("vol_ratio_20_60", pd.Series(np.nan, index=df.index))

        # Track coverage
        cov_rsi = float((~rsi.isna()).mean())
        cov_dd = float((~dd.isna()).mean())
        cov_shadow = float((~shadow.isna()).mean())
        cov_clv = float((~clv.isna()).mean())
        cov_v = float((~v_ratio.isna()).mean())
        avg_cov = float(np.mean([cov_rsi, cov_dd, cov_shadow, cov_clv, cov_v]))

        # Fill with neutral values if missing, but log coverage
        rsi_f = rsi.fillna(50.0)
        dd_f = dd.fillna(-0.45)
        shadow_f = shadow.fillna(0.0)
        clv_f = clv.fillna(0.0)
        v_f = v_ratio.fillna(1.0)

        # Lower RSI (<35) -> higher rebound score
        s_rsi = np.clip((50.0 - rsi_f) * 2.5, 0.0, 100.0)
        # Deep drawdown (-40% to -60%) -> higher rebound potential
        s_dd = np.where((-0.65 <= dd_f) & (dd_f <= -0.35), 100.0, np.where(dd_f < -0.65, 50.0, 30.0))
        # Long lower shadow + positive close location
        s_candle = np.clip(shadow_f * 150.0 + (clv_f + 1.0) * 25.0, 0.0, 100.0)
        # Volume contraction (< 0.75 of 60d MA)
        s_vol = np.where(v_f < 0.75, 100.0, np.where(v_f < 1.0, 60.0, 30.0))

        score = 0.35 * s_rsi + 0.30 * s_dd + 0.20 * s_candle + 0.15 * s_vol
        meta = {
            "model": "SWI_REBOUND_V2",
            "coverage": avg_cov,
            "feature_coverages": {"rsi": cov_rsi, "drawdown": cov_dd, "shadow": cov_shadow, "clv": cov_clv, "vol": cov_v},
            "status": "HEALTHY" if avg_cov >= 0.70 else "DEGRADED_COVERAGE",
        }
        return pd.Series(score, index=df.index), meta

    def score_reversal_model(self, df: pd.DataFrame) -> Tuple[pd.Series, Dict[str, Any]]:
        """SWI_REVERSAL_V2 (20-40d trend reversal): MA20 convergence, volume expansion, trend slope."""
        n = len(df)
        if n == 0:
            return pd.Series(dtype=float), {"coverage": 0.0}

        dist_ma20 = df.get("dist_ma20", pd.Series(np.nan, index=df.index))
        v_turn = df.get("vol_ratio_5_20", pd.Series(np.nan, index=df.index))
        dd = df.get("drawdown_from_peak_close", pd.Series(np.nan, index=df.index))

        cov_ma = float((~dist_ma20.isna()).mean())
        cov_v = float((~v_turn.isna()).mean())
        cov_dd = float((~dd.isna()).mean())
        avg_cov = float(np.mean([cov_ma, cov_v, cov_dd]))

        ma_f = dist_ma20.fillna(-0.05)
        v_f = v_turn.fillna(1.0)
        dd_f = dd.fillna(-0.45)

        s_ma = np.where(ma_f > -0.02, 100.0, np.where(ma_f > -0.08, 65.0, 25.0))
        s_vol = np.where(v_f >= 1.20, 100.0, np.where(v_f >= 1.00, 60.0, 30.0))
        s_dd = np.where((-0.60 <= dd_f) & (dd_f <= -0.40), 100.0, 50.0)

        # Per ablation findings, pair digit alpha is non-significant; zero weight assigned
        score = 0.40 * s_ma + 0.35 * s_vol + 0.25 * s_dd
        meta = {
            "model": "SWI_REVERSAL_V2",
            "coverage": avg_cov,
            "feature_coverages": {"dist_ma20": cov_ma, "vol_ratio_5_20": cov_v, "drawdown": cov_dd},
            "status": "HEALTHY" if avg_cov >= 0.70 else "DEGRADED_COVERAGE",
        }
        return pd.Series(score, index=df.index), meta

    def score_main_wave_model(self, df: pd.DataFrame) -> Tuple[pd.Series, Dict[str, Any]]:
        """SWI_MAIN_WAVE_V2 (40-120d major run-up): Wave1 impulse, reset depth, corporate actions."""
        n = len(df)
        if n == 0:
            return pd.Series(dtype=float), {"coverage": 0.0}

        runup = df.get("wave1_runup_pct", pd.Series(np.nan, index=df.index))
        dd = df.get("drawdown_from_peak_close", pd.Series(np.nan, index=df.index))
        dist_ma20 = df.get("dist_ma20", pd.Series(np.nan, index=df.index))

        cov_r = float((~runup.isna()).mean())
        cov_dd = float((~dd.isna()).mean())
        cov_ma = float((~dist_ma20.isna()).mean())
        avg_cov = float(np.mean([cov_r, cov_dd, cov_ma]))

        r_f = runup.fillna(0.50)
        dd_f = dd.fillna(-0.50)
        ma_f = dist_ma20.fillna(-0.05)

        s_runup = np.clip(r_f * 100.0, 0.0, 100.0)
        s_dd = np.where((-0.60 <= dd_f) & (dd_f <= -0.40), 100.0, np.where(dd_f < -0.65, 30.0, 50.0))
        s_trend = np.where(ma_f >= -0.05, 100.0, 40.0)

        score = 0.45 * s_runup + 0.35 * s_dd + 0.20 * s_trend
        meta = {
            "model": "SWI_MAIN_WAVE_V2",
            "coverage": avg_cov,
            "feature_coverages": {"wave1_runup": cov_r, "drawdown": cov_dd, "dist_ma20": cov_ma},
            "status": "HEALTHY" if avg_cov >= 0.70 else "DEGRADED_COVERAGE",
        }
        return pd.Series(score, index=df.index), meta

    def compute_all_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attaches factor scores for all 3 models to the DataFrame."""
        res = df.copy()
        s_reb, meta_reb = self.score_rebound_model(res)
        s_rev, meta_rev = self.score_reversal_model(res)
        s_mw, meta_mw = self.score_main_wave_model(res)

        res["score_rebound_v2"] = s_reb
        res["score_reversal_v2"] = s_rev
        res["score_main_wave_v2"] = s_mw
        self.feature_coverage = {
            "rebound": meta_reb["coverage"],
            "reversal": meta_rev["coverage"],
            "main_wave": meta_mw["coverage"],
        }
        return res

    def evaluate_model_ic(
        self,
        df: pd.DataFrame,
        score_col: str,
        horizons: List[int],
    ) -> Dict[str, Any]:
        """
        Computes genuine cross-sectional daily Rank IC, HAC t-stat, p-value,
        and Q1-Q5 quintile forward returns.
        """
        results = {}
        for h in horizons:
            ret_col = f"ret_{h}d"
            if ret_col not in df.columns or score_col not in df.columns:
                continue

            sub = df[[score_col, ret_col, "trade_date"]].dropna().copy()
            if len(sub) < 30:
                continue

            # 1. Daily Cross-Sectional Rank IC
            daily_ics = []
            daily_dates = []
            for dt, g in sub.groupby("trade_date"):
                if len(g) >= 5:
                    r_ic, _ = spearmanr(g[score_col], g[ret_col])
                    if not np.isnan(r_ic):
                        daily_ics.append(float(r_ic))
                        daily_dates.append(dt)

            n_days = len(daily_ics)
            if n_days >= 5:
                ics_arr = np.array(daily_ics)
                mean_ic = float(np.mean(ics_arr))
                std_ic = float(np.std(ics_arr, ddof=1)) + 1e-6
                icir = float(mean_ic / std_ic)
                
                # Newey-West / HAC adjustment: lag = min(h, 5)
                lag = min(h, 5)
                gamma_sum = 0.0
                centered = ics_arr - mean_ic
                for l in range(1, lag + 1):
                    gamma_l = np.mean(centered[l:] * centered[:-l])
                    weight = 1.0 - l / (lag + 1.0)
                    gamma_sum += 2.0 * weight * gamma_l
                var_hac = (np.var(ics_arr, ddof=1) + gamma_sum) / n_days
                se_hac = np.sqrt(max(var_hac, 1e-8))
                t_stat_hac = float(mean_ic / se_hac)
                p_val_hac = float(2.0 * (1.0 - stats.t.cdf(np.abs(t_stat_hac), df=n_days - 1)))
                win_rate = float(np.mean(ics_arr > 0))
            else:
                mean_ic, std_ic, icir, t_stat_hac, p_val_hac, win_rate = 0.0, 0.0, 0.0, 0.0, 1.0, 0.5

            # 2. Overall Pooled Rank IC
            pooled_ic, pooled_p = spearmanr(sub[score_col], sub[ret_col])

            # 3. Quintile Q1 - Q5 forward returns
            try:
                sub["quintile"] = pd.qcut(sub[score_col], q=5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
                q_rets = sub.groupby("quintile")[ret_col].mean().to_dict()
                q1_ret = float(q_rets.get("Q1", 0.0))
                q5_ret = float(q_rets.get("Q5", 0.0))
                ls_spread = q5_ret - q1_ret
            except Exception:
                q_rets = {}
                q1_ret, q5_ret, ls_spread = 0.0, 0.0, 0.0

            results[f"{h}d"] = {
                "horizon": h,
                "score_col": score_col,
                "sample_size": len(sub),
                "daily_ic_count": n_days,
                "daily_rank_ic_mean": mean_ic,
                "daily_rank_ic_std": std_ic,
                "ic_ir": icir,
                "t_stat_hac": t_stat_hac,
                "p_value_hac": p_val_hac,
                "daily_win_rate": win_rate,
                "pooled_rank_ic": float(pooled_ic),
                "pooled_p_value": float(pooled_p),
                "quintile_returns": {k: float(v) for k, v in q_rets.items()},
                "q5_minus_q1_spread": ls_spread,
            }

        return results
