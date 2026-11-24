"""Propensity Score and Caliper Control Matching Engine.

Matches treatment episodes (Wave 1 + Deep Drawdown + Bottom/Pair) against
control episodes (Wave 1 + Deep Drawdown without bottom/pair patterns, or ordinary new lows)
to isolate the incremental alpha of specific technical and event patterns.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
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
        """Matches treatment to nearest control within caliper on standardized features."""
        if treatment_df.empty or control_df.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "coverage": 0.0, "smd": {}}

        t_clean = treatment_df.dropna(subset=feature_cols).copy().reset_index(drop=True)
        c_clean = control_df.dropna(subset=feature_cols).copy().reset_index(drop=True)

        if t_clean.empty or c_clean.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "coverage": 0.0, "smd": {}}

        pooled = pd.concat([t_clean[feature_cols], c_clean[feature_cols]], ignore_index=True)
        self.scaler.fit(pooled)

        X_t = self.scaler.transform(t_clean[feature_cols])
        X_c = self.scaler.transform(c_clean[feature_cols])

        nn = NearestNeighbors(n_neighbors=n_neighbors, metric="euclidean")
        nn.fit(X_c)
        distances, indices = nn.kneighbors(X_t)

        matched_t_idx = []
        matched_c_idx = []
        caliper_bound = self.caliper * np.sqrt(len(feature_cols))

        for i, (dist_list, idx_list) in enumerate(zip(distances, indices)):
            if dist_list[0] <= caliper_bound:
                matched_t_idx.append(i)
                matched_c_idx.append(idx_list[0])

        matched_t = t_clean.iloc[matched_t_idx].copy().reset_index(drop=True)
        matched_c = c_clean.iloc[matched_c_idx].copy().reset_index(drop=True)

        # Compute Standardized Mean Differences (SMD) before and after
        smd_dict = {}
        for col in feature_cols:
            mean_t_pre = t_clean[col].mean()
            mean_c_pre = c_clean[col].mean()
            std_pooled_pre = np.sqrt(0.5 * (t_clean[col].var() + c_clean[col].var()))
            smd_pre = (mean_t_pre - mean_c_pre) / (std_pooled_pre + 1e-6)

            if not matched_t.empty and not matched_c.empty:
                mean_t_post = matched_t[col].mean()
                mean_c_post = matched_c[col].mean()
                std_pooled_post = np.sqrt(0.5 * (matched_t[col].var() + matched_c[col].var()))
                smd_post = (mean_t_post - mean_c_post) / (std_pooled_post + 1e-6)
            else:
                smd_post = np.nan

            smd_dict[col] = {
                "smd_before": float(smd_pre),
                "smd_after": float(smd_post),
                "balanced": bool(abs(smd_post) < 0.10) if not np.isnan(smd_post) else False,
            }

        meta = {
            "matched_pairs": len(matched_t),
            "treatment_pool": len(t_clean),
            "control_pool": len(c_clean),
            "coverage": float(len(matched_t) / len(t_clean)) if len(t_clean) > 0 else 0.0,
            "smd": smd_dict,
        }

        return matched_t, matched_c, meta
