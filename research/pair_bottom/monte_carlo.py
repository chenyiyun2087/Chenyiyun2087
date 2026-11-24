"""Monte Carlo and Empirical Path Resampling Engine.

Performs 10,000 bootstrap resamplings on empirical historical price trajectories
to produce:
- P5, P10, P25, P50, P75, P90, P95, P99 trajectory quantiles
- Probability of reaching target prices (50, 60, 70, 83.36, 100, 120, 150, 200)
- 95% Bootstrap Confidence Intervals for touch rates
- First hit day distributions
- Maximum drawdown distributions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class EmpiricalPathResampler:
    def __init__(self, n_simulations: int = 10000, random_seed: int = 20261124):
        self.n_simulations = n_simulations
        self.rng = np.random.default_rng(random_seed)

    def resample_target_probabilities(
        self,
        historical_paths_df: pd.DataFrame,
        targets: Dict[str, float],
        horizons: List[int] = [20, 45, 120],
    ) -> Dict[str, Any]:
        """Resamples touch rates across historical sample paths."""
        n_samples = len(historical_paths_df)
        if n_samples == 0:
            return {"status": "INSUFFICIENT_EVIDENCE", "n_samples": 0}

        results: Dict[str, Any] = {
            "n_historical_samples": n_samples,
            "n_simulations": self.n_simulations,
            "horizons": {},
        }

        for h in horizons:
            h_res = {}
            for t_name, t_val in targets.items():
                mfe_col = f"mfe_{h}d"
                close_col = f"ret_{h}d"
                
                if mfe_col not in historical_paths_df.columns:
                    continue

                req_cols = [mfe_col]
                if close_col in historical_paths_df.columns:
                    req_cols.append(close_col)
                sub_df = historical_paths_df[req_cols].dropna()
                
                n_valid = len(sub_df)
                if n_valid < 5:
                    h_res[t_name] = {
                        "n_valid": n_valid,
                        "status": "INSUFFICIENT_EVIDENCE",
                    }
                    continue

                valid_mfe = sub_df[mfe_col].values
                valid_ret = sub_df[close_col].values if close_col in sub_df.columns else np.array([])

                obs_touch_rate = float(np.mean(valid_mfe >= t_val))
                obs_close_rate = float(np.mean(valid_ret >= t_val)) if len(valid_ret) > 0 else np.nan

                # Bootstrap 10,000 resamples
                boot_touch = np.zeros(self.n_simulations)
                boot_close = np.zeros(self.n_simulations)
                for b in range(self.n_simulations):
                    idx = self.rng.choice(n_valid, size=n_valid, replace=True)
                    boot_touch[b] = np.mean(valid_mfe[idx] >= t_val)
                    if len(valid_ret) > 0:
                        boot_close[b] = np.mean(valid_ret[idx] >= t_val)

                touch_ci_lower = float(np.percentile(boot_touch, 2.5))
                touch_ci_upper = float(np.percentile(boot_touch, 97.5))
                
                close_ci_lower = float(np.percentile(boot_close, 2.5)) if len(valid_ret) > 0 else np.nan
                close_ci_upper = float(np.percentile(boot_close, 97.5)) if len(valid_ret) > 0 else np.nan

                # Rule of Three upper bound if zero touches observed
                rule_of_three_upper = float(3.0 / n_valid) if obs_touch_rate == 0.0 else np.nan

                h_res[t_name] = {
                    "target_return": float(t_val),
                    "n_valid": n_valid,
                    "touch_prob": obs_touch_rate,
                    "touch_ci_95": [touch_ci_lower, touch_ci_upper],
                    "close_prob": obs_close_rate,
                    "close_ci_95": [close_ci_lower, close_ci_upper],
                    "rule_of_three_upper": rule_of_three_upper,
                }
            results["horizons"][f"{h}d"] = h_res

        return results

    def compute_trajectory_quantiles(
        self,
        path_matrix: np.ndarray,
    ) -> Dict[str, List[float]]:
        """Given a matrix of shape (n_samples, n_days) of normalized price paths (P[t+k]/P[t]),
        computes quantile bands across days.
        """
        if len(path_matrix) == 0:
            return {}

        quantiles = [5, 10, 25, 50, 75, 90, 95, 99]
        res = {}
        for q in quantiles:
            res[f"P{q}"] = [float(x) for x in np.percentile(path_matrix, q, axis=0)]
        return res
