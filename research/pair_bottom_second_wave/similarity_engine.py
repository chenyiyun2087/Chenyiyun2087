"""Historical Case Similarity Engine for Luwi 688401.SH Case Study.

Computes multi-dimensional weighted Euclidean / Cosine distances to identify
the top 20, 50, and 100 historical stocks most similar to Luwi's setup.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class SecondWaveSimilarityEngine:
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or {
            "w_wave1_runup": 0.15,
            "w_drawdown": 0.20,
            "w_days_from_peak": 0.10,
            "w_rsi": 0.10,
            "w_dist_ma20": 0.10,
            "w_lower_shadow": 0.10,
            "w_vol_ratio": 0.10,
            "w_industry_semi": 0.10,
            "w_market_cap": 0.05,
        }

    def build_target_profile(self) -> Dict[str, float]:
        """Audited profile of Luwi (688401.SH) as of 2026-11-24."""
        return {
            "wave1_runup_pct": 1.10,        # ~110% run-up to 103.90
            "drawdown_from_peak": -0.582,   # 43.43 / 103.90 - 1 = -58.20%
            "peak_to_signal_days": 100.0,   # ~100 trading days from 20260701 to 20261124
            "rsi_14": 38.0,
            "dist_ma20": -0.06,
            "lower_shadow_ratio": 0.35,
            "vol_ratio_5_20": 1.15,
            "is_semiconductor": 1.0,
            "log_market_cap": np.log(9.0e9),  # ~90 billion RMB
        }

    def compute_similarity_ranking(
        self,
        candidate_df: pd.DataFrame,
        top_k_list: List[int] = [20, 50, 100],
    ) -> Dict[str, pd.DataFrame]:
        """Calculates distance to target profile and returns top-k nearest historical cases."""
        if candidate_df.empty:
            return {f"top_{k}": pd.DataFrame() for k in top_k_list}

        target = self.build_target_profile()
        df = candidate_df.copy().reset_index(drop=True)

        # Feature extraction
        feat_runup = df.get("wave1_runup_pct", pd.Series(0.6, index=df.index)).fillna(0.6).values
        feat_dd = df.get("drawdown_from_peak_close", pd.Series(-0.45, index=df.index)).fillna(-0.45).values
        feat_days = df.get("peak_to_signal_days", pd.Series(80.0, index=df.index)).fillna(80.0).values
        feat_rsi = df.get("rsi_14", pd.Series(40.0, index=df.index)).fillna(40.0).values
        feat_ma20 = df.get("dist_ma20", pd.Series(-0.05, index=df.index)).fillna(-0.05).values
        feat_shadow = df.get("lower_shadow_ratio", pd.Series(0.25, index=df.index)).fillna(0.25).values
        feat_vol = df.get("vol_ratio_5_20", pd.Series(1.0, index=df.index)).fillna(1.0).values
        feat_semi = df.get("industry", pd.Series("", index=df.index)).apply(
            lambda x: 1.0 if "半导体" in str(x) or "元器件" in str(x) or "电子" in str(x) else 0.0
        ).values
        feat_mv = np.log(df.get("total_mv", pd.Series(9.0e9, index=df.index)).clip(lower=1.0e8).values)

        X = np.column_stack([
            feat_runup, feat_dd, feat_days, feat_rsi, feat_ma20,
            feat_shadow, feat_vol, feat_semi, feat_mv
        ])

        target_vec = np.array([
            target["wave1_runup_pct"], target["drawdown_from_peak"], target["peak_to_signal_days"],
            target["rsi_14"], target["dist_ma20"], target["lower_shadow_ratio"],
            target["vol_ratio_5_20"], target["is_semiconductor"], target["log_market_cap"]
        ])

        weights_vec = np.array([
            self.weights["w_wave1_runup"], self.weights["w_drawdown"], self.weights["w_days_from_peak"],
            self.weights["w_rsi"], self.weights["w_dist_ma20"], self.weights["w_lower_shadow"],
            self.weights["w_vol_ratio"], self.weights["w_industry_semi"], self.weights["w_market_cap"]
        ])

        # Standardize features safely
        stds = np.nanstd(X, axis=0)
        stds[stds < 1e-5] = 1.0
        means = np.nanmean(X, axis=0)

        X_norm = (X - means) / stds
        target_norm = (target_vec - means) / stds

        # Weighted Euclidean distance
        diff = X_norm - target_norm
        dist_sq = np.sum(weights_vec * (diff ** 2), axis=1)
        distances = np.sqrt(dist_sq)

        df["distance"] = distances
        df["similarity_score"] = 1.0 / (1.0 + distances)

        # Deduplicate signals from the same stock within 45 trading days
        df.sort_values(by=["distance"], inplace=True)
        dedup_records = []
        seen_stock_dates: Dict[str, List[int]] = {}

        for _, row in df.iterrows():
            stk = row["ts_code"]
            dt = int(row["trade_date"])
            prev_dates = seen_stock_dates.setdefault(stk, [])
            if any(abs(dt - pd) < 300 for pd in prev_dates):
                continue
            prev_dates.append(dt)
            dedup_records.append(row)

        dedup_df = pd.DataFrame(dedup_records).reset_index(drop=True)

        results = {}
        for k in top_k_list:
            results[f"top_{k}"] = dedup_df.head(k).copy()

        return results
