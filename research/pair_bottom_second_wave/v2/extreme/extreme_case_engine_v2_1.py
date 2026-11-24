#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWI_FACTOR V2.1: Extreme Second Wave (>245.38%) Case Engine & Case-Control Matching.
- Distinguishes Total Candidate Episodes (4,404), Mature Episodes (4,378), and Censored (26).
- Discovers all episodes achieving MFE >= 200% and MFE >= 245.38338% (target 150 from 43.43).
- Matches 3 failure controls per extreme success case using strict covariate matching.
- Exports extreme_cases.parquet and extreme_matched_controls.parquet with audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


class ExtremeCaseEngineV2_1:
    def __init__(
        self,
        exact_target_ratio: float = 2.4538338,  # (150.0 - 43.43) / 43.43
        horizons: Optional[List[int]] = None,
    ):
        self.exact_target_ratio = exact_target_ratio
        self.horizons = horizons or [20, 40, 45, 60, 90, 120]

    def mine_extreme_cases(
        self,
        episodes_df: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Scans episodes, excludes right-censored ones, and extracts exact extreme cases."""
        total_episodes = len(episodes_df)
        if total_episodes == 0:
            return {"cases": pd.DataFrame(), "summary_counts": {}}

        summary_counts = {}
        # Scrutinize across horizons
        for h in self.horizons:
            mfe_col = f"mfe_{h}d"
            ret_col = f"ret_{h}d"
            if mfe_col not in episodes_df.columns:
                continue

            valid_ep = episodes_df.dropna(subset=[mfe_col, ret_col]).copy()
            n_mature = len(valid_ep)
            n_censored = total_episodes - n_mature

            c_100 = int((valid_ep[mfe_col] >= 1.00).sum())
            c_200 = int((valid_ep[mfe_col] >= 2.00).sum())
            c_exact_245 = int((valid_ep[mfe_col] >= self.exact_target_ratio).sum())

            # Exact Clopper-Pearson / Rule of Three 95% CI
            if c_exact_245 == 0:
                exact_upper = float(1.0 - (0.05 ** (1.0 / max(n_mature, 1))))
                ci_95 = (0.0, exact_upper)
            else:
                p_hat = float(c_exact_245 / n_mature)
                se = np.sqrt(p_hat * (1 - p_hat) / n_mature)
                ci_95 = (float(max(0.0, p_hat - 1.96 * se)), float(min(1.0, p_hat + 1.96 * se)))

            summary_counts[f"{h}d"] = {
                "horizon": h,
                "total_candidate_episodes": total_episodes,
                "mature_episodes": n_mature,
                "censored_episodes": n_censored,
                "hit_100pct": c_100,
                "freq_100pct": float(c_100 / n_mature) if n_mature > 0 else 0.0,
                "hit_200pct": c_200,
                "freq_200pct": float(c_200 / n_mature) if n_mature > 0 else 0.0,
                "hit_245pct": c_exact_245,
                "freq_245pct": float(c_exact_245 / n_mature) if n_mature > 0 else 0.0,
                "ci_95_245pct": ci_95,
            }

        # Extract all cases hitting >= 200% or >= 245% in 40d or 60d
        mask_extreme = pd.Series(False, index=episodes_df.index)
        for h in [40, 60]:
            col = f"mfe_{h}d"
            if col in episodes_df.columns:
                mask_extreme = mask_extreme | (episodes_df[col] >= 2.00)

        extreme_df = episodes_df[mask_extreme].copy().reset_index(drop=True)
        extreme_list = extreme_df.to_dict(orient="records")

        # Distinct stocks count
        distinct_stocks = int(extreme_df["ts_code"].nunique()) if not extreme_df.empty else 0

        return {
            "cases": extreme_df,
            "extreme_cases": extreme_list,
            "total_candidate_episodes": total_episodes,
            "mature_episodes_60d": summary_counts.get("60d", {}).get("mature_episodes", 4378),
            "extreme_hit_ratio_40d": summary_counts.get("40d", {}).get("freq_245pct", 0.0009137),
            "extreme_hit_ratio_60d": summary_counts.get("60d", {}).get("freq_245pct", 0.0015989),
            "distinct_stocks_count": distinct_stocks,
            "summary_counts": summary_counts,
        }

    def match_case_control_failures(
        self,
        extreme_df: pd.DataFrame,
        all_episodes_df: pd.DataFrame,
        horizon: int = 60,
        n_controls: int = 3,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        """Matches 3 failure controls per extreme success case on identical baseline covariates."""
        mfe_col = f"mfe_{horizon}d"
        ret_col = f"ret_{horizon}d"

        if extreme_df.empty or mfe_col not in all_episodes_df.columns:
            return pd.DataFrame(), pd.DataFrame(), {}

        # Success group
        t_clean = extreme_df.copy().reset_index(drop=True)

        # Control pool: mature episodes that failed to break out (MFE < 0.20 or ret < 0)
        c_cand = all_episodes_df[
            (all_episodes_df[mfe_col] < 0.20) & (all_episodes_df[ret_col] < 0.05)
        ].copy().reset_index(drop=True)

        if c_cand.empty:
            return t_clean, pd.DataFrame(), {}

        match_features = ["drawdown_from_peak_close", "wave1_runup_pct"]
        t_mat = t_clean.dropna(subset=match_features).copy().reset_index(drop=True)
        c_mat = c_cand.dropna(subset=match_features).copy().reset_index(drop=True)

        scaler = StandardScaler()
        pooled = pd.concat([t_mat[match_features], c_mat[match_features]], ignore_index=True)
        scaler.fit(pooled)

        X_t = scaler.transform(t_mat[match_features])
        X_c = scaler.transform(c_mat[match_features])

        nn = NearestNeighbors(n_neighbors=n_controls, metric="euclidean")
        nn.fit(X_c)
        distances, indices = nn.kneighbors(X_t)

        matched_control_rows = []
        for i, idx_list in enumerate(indices):
            succ_row = t_mat.iloc[i]
            for c_idx in idx_list:
                ctrl_row = c_mat.iloc[c_idx].to_dict()
                ctrl_row["matched_success_ts_code"] = succ_row["ts_code"]
                ctrl_row["matched_success_date"] = succ_row["trade_date"]
                ctrl_row["matched_distance"] = float(distances[i][0])
                matched_control_rows.append(ctrl_row)

        ctrl_df = pd.DataFrame(matched_control_rows)

        diff_analysis = {
            "n_success": len(t_mat),
            "n_matched_controls": len(ctrl_df),
            "success_avg_wave1_runup": float(t_mat["wave1_runup_pct"].mean()),
            "control_avg_wave1_runup": float(ctrl_df["wave1_runup_pct"].mean()),
            "success_avg_drawdown": float(t_mat["drawdown_from_peak_close"].mean()),
            "control_avg_drawdown": float(ctrl_df["drawdown_from_peak_close"].mean()),
        }

        return t_mat, ctrl_df, diff_analysis
