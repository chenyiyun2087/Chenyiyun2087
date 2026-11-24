"""Phase 4: Extreme Second Wave (>245.38%) Case Study & Case-Control Matching Engine.

Highest research priority: rigorously discovers every single historical episode
that achieved MFE >= 245.38338% (target 150 from 43.43) within 40/45/60 days,
conducts deep individual case audits, and matches 3-5 failure controls per success.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler


class ExtremeCaseEngine:
    def __init__(
        self,
        config_or_ratio: Any = None,
        exact_target_ratio: float = 2.4538338,  # (150.0 - 43.43) / 43.43
        horizons: Optional[List[int]] = None,
    ):
        if isinstance(config_or_ratio, (int, float)):
            self.exact_target_ratio = float(config_or_ratio)
        else:
            self.exact_target_ratio = exact_target_ratio
        self.horizons = horizons or [20, 40, 45, 60, 90, 120]

    def extract_extreme_cases(
        self,
        episodes_df: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Scans all episodes and extracts full extreme cases meeting MFE thresholds."""
        if episodes_df.empty:
            return {"cases": pd.DataFrame(), "summary_counts": {}}

        summary_counts = {}
        for h in self.horizons:
            mfe_col = f"mfe_{h}d"
            if mfe_col not in episodes_df.columns:
                continue

            valid_ep = episodes_df.dropna(subset=[mfe_col])
            n_total = len(valid_ep)

            c_100 = int((valid_ep[mfe_col] >= 1.00).sum())
            c_150 = int((valid_ep[mfe_col] >= 1.50).sum())
            c_200 = int((valid_ep[mfe_col] >= 2.00).sum())
            c_exact_245 = int((valid_ep[mfe_col] >= self.exact_target_ratio).sum())
            c_300 = int((valid_ep[mfe_col] >= 3.00).sum())

            summary_counts[f"{h}d"] = {
                "n_mature": n_total,
                "hit_100pct": c_100,
                "freq_100pct": float(c_100 / n_total) if n_total > 0 else 0.0,
                "hit_150pct": c_150,
                "freq_150pct": float(c_150 / n_total) if n_total > 0 else 0.0,
                "hit_200pct": c_200,
                "freq_200pct": float(c_200 / n_total) if n_total > 0 else 0.0,
                "hit_245pct": c_exact_245,
                "freq_245pct": float(c_exact_245 / n_total) if n_total > 0 else 0.0,
                "hit_300pct": c_300,
                "freq_300pct": float(c_300 / n_total) if n_total > 0 else 0.0,
            }

        # Identify all episodes that hit >= 200% or >= 245.38% in 40d, 45d, or 60d
        mask_extreme = pd.Series(False, index=episodes_df.index)
        for h in [40, 45, 60]:
            col = f"mfe_{h}d"
            if col in episodes_df.columns:
                mask_extreme = mask_extreme | (episodes_df[col] >= 2.00)

        extreme_df = episodes_df[mask_extreme].copy().reset_index(drop=True)
        return {
            "cases": extreme_df,
            "summary_counts": summary_counts,
        }

    def mine_extreme_cases(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Convenience executor for mining all extreme episodes."""
        res = self.extract_extreme_cases(df)
        extreme_df = res["cases"]
        cases_list = extreme_df.to_dict(orient="records") if not extreme_df.empty else []
        summary = res["summary_counts"]
        freq_60 = summary.get("60d", {}).get("freq_245pct", 0.0)
        freq_40 = summary.get("40d", {}).get("freq_245pct", 0.0)
        return {
            "extreme_cases": cases_list,
            "extreme_hit_ratio_40d": freq_40,
            "extreme_hit_ratio_60d": freq_60,
            "summary_counts": summary,
        }

    def match_case_control_failures(
        self,
        extreme_df: pd.DataFrame,
        all_episodes_df: pd.DataFrame,
        horizon: int = 40,
        n_controls: int = 3,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        """Matches 3-5 failure controls for each extreme success case."""
        mfe_col = f"mfe_{horizon}d"
        ret_col = f"ret_{horizon}d"

        if extreme_df.empty or mfe_col not in all_episodes_df.columns:
            return pd.DataFrame(), pd.DataFrame(), {}

        # Success group: hit >= 2.00 or 2.45
        t_clean = extreme_df.copy().reset_index(drop=True)

        # Control candidates: failure or ordinary return (MFE < 0.30 or Return < 0)
        c_cand = all_episodes_df[
            (all_episodes_df[mfe_col] < 0.30) & (all_episodes_df["trade_date"] >= 20211124)
        ].copy().reset_index(drop=True)

        if c_cand.empty:
            return t_clean, pd.DataFrame(), {}

        # Features to match: trade_date (temporal), drawdown, wave1_runup, log_mv
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
        _, indices = nn.kneighbors(X_t)

        matched_control_rows = []
        for i, idx_list in enumerate(indices):
            succ_row = t_mat.iloc[i]
            for c_idx in idx_list:
                ctrl_row = c_mat.iloc[c_idx].to_dict()
                ctrl_row["matched_success_ts_code"] = succ_row["ts_code"]
                ctrl_row["matched_success_date"] = succ_row["trade_date"]
                matched_control_rows.append(ctrl_row)

        ctrl_df = pd.DataFrame(matched_control_rows)

        # Comparison metrics between success and failure groups
        diff_analysis = {
            "n_success": len(t_mat),
            "n_matched_controls": len(ctrl_df),
            "success_avg_wave1_runup": float(t_mat["wave1_runup_pct"].mean()),
            "control_avg_wave1_runup": float(ctrl_df["wave1_runup_pct"].mean()),
            "success_avg_drawdown": float(t_mat["drawdown_from_peak_close"].mean()),
            "control_avg_drawdown": float(ctrl_df["drawdown_from_peak_close"].mean()),
        }

        return t_mat, ctrl_df, diff_analysis
