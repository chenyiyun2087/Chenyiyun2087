"""Propensity and Caliper Control Matching Engine for Pair Bottom Signals.

Matches Treatment (Pair Bottom PB01/PB03) against Control (Ordinary New Lows / Ordinary Closes)
to isolate the incremental predictive value of the pair price pattern.
Computes Standardized Mean Differences (SMD), matching coverage, and placebo comparisons.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


class ControlMatcher:
    def __init__(self, caliper: float = 0.5):
        self.caliper = caliper
        self.scaler = StandardScaler()

    def match_treatment_control(
        self,
        treatment_df: pd.DataFrame,
        control_df: pd.DataFrame,
        feature_cols: List[str],
        n_neighbors: int = 1,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        """Matches each treatment row to nearest control row within caliper on standardized features."""
        if treatment_df.empty or control_df.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "coverage": 0.0, "smd": {}}

        # Drop rows with NaNs in feature_cols
        t_clean = treatment_df.dropna(subset=feature_cols).copy().reset_index(drop=True)
        c_clean = control_df.dropna(subset=feature_cols).copy().reset_index(drop=True)

        if t_clean.empty or c_clean.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "coverage": 0.0, "smd": {}}

        # Fit scaler on pooled data to avoid scale distortion
        pooled = pd.concat([t_clean[feature_cols], c_clean[feature_cols]], ignore_index=True)
        self.scaler.fit(pooled)

        X_t = self.scaler.transform(t_clean[feature_cols])
        X_c = self.scaler.transform(c_clean[feature_cols])

        # Nearest neighbors matching
        nn = NearestNeighbors(n_neighbors=n_neighbors, metric="euclidean")
        nn.fit(X_c)
        distances, indices = nn.kneighbors(X_t)

        matched_t_idx = []
        matched_c_idx = []
        for i, (dist_list, idx_list) in enumerate(zip(distances, indices)):
            # check caliper (distance threshold)
            if dist_list[0] <= self.caliper * np.sqrt(len(feature_cols)):
                matched_t_idx.append(i)
                matched_c_idx.append(idx_list[0])

        matched_treatment = t_clean.iloc[matched_t_idx].copy().reset_index(drop=True)
        matched_control = c_clean.iloc[matched_c_idx].copy().reset_index(drop=True)

        # Compute SMD for each feature before and after matching
        smd_stats = {}
        for col in feature_cols:
            mean_t_pre = t_clean[col].mean()
            mean_c_pre = c_clean[col].mean()
            var_t_pre = t_clean[col].var()
            var_c_pre = c_clean[col].var()
            pooled_std_pre = np.sqrt((var_t_pre + var_c_pre) / 2.0) if (var_t_pre + var_c_pre) > 0 else 1.0
            smd_pre = (mean_t_pre - mean_c_pre) / pooled_std_pre

            if not matched_treatment.empty:
                mean_t_post = matched_treatment[col].mean()
                mean_c_post = matched_control[col].mean()
                var_t_post = matched_treatment[col].var()
                var_c_post = matched_control[col].var()
                pooled_std_post = np.sqrt((var_t_post + var_c_post) / 2.0) if (var_t_post + var_c_post) > 0 else 1.0
                smd_post = (mean_t_post - mean_c_post) / pooled_std_post
            else:
                smd_post = np.nan

            smd_stats[col] = {
                "smd_before": float(smd_pre),
                "smd_after": float(smd_post),
            }

        coverage = len(matched_treatment) / len(t_clean) if len(t_clean) > 0 else 0.0
        meta = {
            "treatment_total": len(t_clean),
            "control_total": len(c_clean),
            "matched_pairs": len(matched_treatment),
            "coverage": float(coverage),
            "smd": smd_stats,
        }
        return matched_treatment, matched_control, meta
