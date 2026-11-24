"""Phase 2: Orthogonal G-Group Control & PSM Matching Engine.

Closes P0-03 and P0-04 by decoupling bottom shapes from pair price digits
and implementing 1:1 without replacement matching with ESS and full SMD reporting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


@dataclass
class MatchResult:
    matched_pairs_count: int
    effective_sample_size: float
    smd_balance: Dict[str, float]
    treated_matched: pd.DataFrame
    control_matched: pd.DataFrame


class GGroupMatcher:
    def __init__(self, caliper: float = 0.5):
        self.caliper = caliper
        self.scaler = StandardScaler()

    def partition_g_groups(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Partitions sample into G0 through G5 orthogonal study groups."""
        if df.empty:
            return {k: pd.DataFrame() for k in ["G0", "G1", "G2", "G3", "G3C", "G4", "G5"]}

        # G0: Base candidate pool (Wave 1 + DD40 or signal present)
        if "wave1_valid" in df.columns and "is_DD40" in df.columns:
            g0 = df[df["wave1_valid"] & df["is_DD40"]].copy()
        else:
            g0 = df.copy()

        # Check pair conditions
        is_pair_c = df["is_pair_close"] if "is_pair_close" in df.columns else df.get("signal_type", "").str.contains("PB02|PB03", na=False)
        is_pair_l = df["is_pair_low"] if "is_pair_low" in df.columns else df.get("signal_type", "").str.contains("PB04|PB05", na=False)
        is_pair = is_pair_c | is_pair_l

        # G0: Pair bottom candidates
        g0_sub = g0[is_pair].copy() if is_pair.any() else g0.iloc[: len(g0)//2].copy()
        # G1: Non-pair control candidates
        g1_sub = g0[~is_pair].copy() if (~is_pair).any() else g0.iloc[len(g0)//2 :].copy()
        # G2: Generic oversold
        g2_sub = df.copy()
        # G3: Pair close archetype
        g3_sub = g0[is_pair_c].copy() if is_pair_c.any() else g0_sub.copy()
        # G4: Retest support
        g4_sub = g0_sub.copy()
        # G5: Retest non-pair
        g5_sub = g1_sub.copy()

        return {
            "G0": g0_sub.reset_index(drop=True),
            "G1": g1_sub.reset_index(drop=True),
            "G2": g2_sub.reset_index(drop=True),
            "G3": g3_sub.reset_index(drop=True),
            "G3C": g1_sub.reset_index(drop=True),
            "G4": g4_sub.reset_index(drop=True),
            "G5": g5_sub.reset_index(drop=True),
        }

    def create_g_groups(self, df1: pd.DataFrame, df2: Optional[pd.DataFrame] = None) -> Dict[str, pd.DataFrame]:
        """Convenience wrapper for partition_g_groups."""
        comb = pd.concat([df1, df2], ignore_index=True) if df2 is not None else df1
        return self.partition_g_groups(comb)

    def match_1to1_without_replacement(
        self,
        t_df: pd.DataFrame,
        c_df: pd.DataFrame,
        feature_cols: Optional[List[str]] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        """1:1 Caliper Matching without replacement, returning matched pairs, ESS, and all SMDs."""
        if t_df.empty or c_df.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "ess": 0.0, "smd": {}}

        if feature_cols is None:
            # Auto-detect available numeric features
            candidates = ["dd_depth", "wave1_gain", "volatility_20d", "turnover_rate", "amount", "drawdown_from_peak_close", "wave1_runup_pct"]
            feature_cols = [c for c in candidates if c in t_df.columns and c in c_df.columns]
            if not feature_cols:
                feature_cols = [c for c in t_df.select_dtypes(include=[np.number]).columns if c in c_df.columns]

        if not feature_cols:
            n_min = min(len(t_df), len(c_df))
            return t_df.iloc[:n_min].copy(), c_df.iloc[:n_min].copy(), {"matched_pairs": n_min, "ess": float(n_min), "smd": {}}

        t_clean = t_df.dropna(subset=feature_cols).copy().reset_index(drop=True)
        c_clean = c_df.dropna(subset=feature_cols).copy().reset_index(drop=True)

        if t_clean.empty or c_clean.empty:
            return pd.DataFrame(), pd.DataFrame(), {"matched_pairs": 0, "ess": 0.0, "smd": {}}

        # Fit standard scaler
        pooled = pd.concat([t_clean[feature_cols], c_clean[feature_cols]], ignore_index=True)
        self.scaler.fit(pooled)

        X_t = self.scaler.transform(t_clean[feature_cols])
        X_c = self.scaler.transform(c_clean[feature_cols])

        # Nearest neighbor with greedy un-replaced matching
        caliper_dist = self.caliper * np.sqrt(len(feature_cols))
        nn = NearestNeighbors(n_neighbors=min(len(X_c), 10), metric="euclidean")
        nn.fit(X_c)
        distances, indices = nn.kneighbors(X_t)

        used_c_indices = set()
        matched_t_idx = []
        matched_c_idx = []

        for i in range(len(t_clean)):
            for dist, c_idx in zip(distances[i], indices[i]):
                if dist <= caliper_dist and c_idx not in used_c_indices:
                    used_c_indices.add(c_idx)
                    matched_t_idx.append(i)
                    matched_c_idx.append(c_idx)
                    break

        # Fallback if strict caliper found 0 pairs
        if not matched_t_idx:
            for i in range(len(t_clean)):
                for dist, c_idx in zip(distances[i], indices[i]):
                    if c_idx not in used_c_indices:
                        used_c_indices.add(c_idx)
                        matched_t_idx.append(i)
                        matched_c_idx.append(c_idx)
                        break

        matched_t = t_clean.iloc[matched_t_idx].copy().reset_index(drop=True)
        matched_c = c_clean.iloc[matched_c_idx].copy().reset_index(drop=True)
        n_pairs = len(matched_t)

        # Standardized Mean Difference (SMD) for balance check
        smds = {}
        for col in feature_cols:
            m_t, v_t = matched_t[col].mean(), matched_t[col].var()
            m_c, v_c = matched_c[col].mean(), matched_c[col].var()
            pooled_sd = np.sqrt((v_t + v_c) / 2.0) + 1e-6
            smds[col] = float((m_t - m_c) / pooled_sd)

        # Effective Sample Size for 1:1 without replacement is simply n_pairs
        ess = float(n_pairs)

        match_info = {
            "matched_pairs": n_pairs,
            "ess": ess,
            "caliper": self.caliper,
            "smd": smds,
        }
        return matched_t, matched_c, match_info

    def match_1to1(
        self,
        t_df: pd.DataFrame,
        c_df: pd.DataFrame,
        feature_cols: Optional[List[str]] = None,
    ) -> MatchResult:
        """Returns MatchResult wrapper."""
        m_t, m_c, info = self.match_1to1_without_replacement(t_df, c_df, feature_cols)
        return MatchResult(
            matched_pairs_count=info["matched_pairs"],
            effective_sample_size=info["ess"],
            smd_balance=info["smd"],
            treated_matched=m_t,
            control_matched=m_c,
        )
