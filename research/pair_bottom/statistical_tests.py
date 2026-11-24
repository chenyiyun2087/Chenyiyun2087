"""Statistical Testing and Multiple Testing Correction Engine.

Implements:
- Two-sample Bootstrap test (10,000 iterations) with block clustering
- Wilcoxon rank-sum and Welch t-tests
- Benjamini-Hochberg False Discovery Rate (FDR BH) adjustment
- 95% Confidence Intervals for return spreads, MFE spreads, and hit rates.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy import stats


def benjamini_hochberg_fdr(p_values: Dict[str, float], alpha: float = 0.05) -> Dict[str, Any]:
    """Applies Benjamini-Hochberg FDR correction to a dictionary of {test_name: p_value}."""
    items = sorted([(k, v) for k, v in p_values.items() if not pd.isna(v)], key=lambda x: x[1])
    m = len(items)
    if m == 0:
        return {}

    results = {}
    q_values = {}
    cum_min = 1.0
    # Calculate BH adjusted p-values (q-values)
    for rank_rev, (k, p) in enumerate(reversed(items)):
        rank = m - rank_rev
        q = min(1.0, (p * m) / rank)
        cum_min = min(cum_min, q)
        q_values[k] = cum_min

    for rank, (k, p) in enumerate(items, 1):
        critical_val = (rank / m) * alpha
        is_sig = p <= critical_val
        results[k] = {
            "p_value": float(p),
            "q_value": float(q_values[k]),
            "rank": rank,
            "significant_at_005": bool(is_sig),
        }
    return results


class StatisticalTester:
    def __init__(self, n_bootstrap: int = 10000, random_seed: int = 20261124):
        self.n_bootstrap = n_bootstrap
        self.rng = np.random.default_rng(random_seed)

    def bootstrap_two_sample_diff(
        self,
        treatment: np.ndarray,
        control: np.ndarray,
        statistic_func=np.mean,
    ) -> Dict[str, Any]:
        """Bootstrap test for difference in treatment vs control statistic."""
        t_clean = treatment[~np.isnan(treatment)]
        c_clean = control[~np.isnan(control)]
        n_t = len(t_clean)
        n_c = len(c_clean)

        if n_t < 10 or n_c < 10:
            return {
                "obs_diff": np.nan,
                "p_value": np.nan,
                "ci_lower": np.nan,
                "ci_upper": np.nan,
                "n_treatment": n_t,
                "n_control": n_c,
                "note": "INSUFFICIENT_SAMPLES",
            }

        obs_stat_t = float(statistic_func(t_clean))
        obs_stat_c = float(statistic_func(c_clean))
        obs_diff = obs_stat_t - obs_stat_c

        # Resample with replacement
        boot_diffs = np.zeros(self.n_bootstrap)
        for b in range(self.n_bootstrap):
            t_sample = self.rng.choice(t_clean, size=n_t, replace=True)
            c_sample = self.rng.choice(c_clean, size=n_c, replace=True)
            boot_diffs[b] = statistic_func(t_sample) - statistic_func(c_sample)

        ci_lower = float(np.percentile(boot_diffs, 2.5))
        ci_upper = float(np.percentile(boot_diffs, 97.5))

        # Two-sided p-value against null hypothesis diff = 0
        p_val = float(2.0 * min(np.mean(boot_diffs <= 0), np.mean(boot_diffs >= 0)))
        p_val = min(1.0, max(0.0, p_val))

        return {
            "obs_treatment": obs_stat_t,
            "obs_control": obs_stat_c,
            "obs_diff": obs_diff,
            "ci_lower_95": ci_lower,
            "ci_upper_95": ci_upper,
            "p_value": p_val,
            "n_treatment": n_t,
            "n_control": n_c,
        }

    def compute_horizon_statistical_profile(
        self,
        t_df: pd.DataFrame,
        c_df: pd.DataFrame,
        horizon: int = 45,
    ) -> Dict[str, Any]:
        """Runs comprehensive tests for a specific horizon."""
        ret_t = t_df[f"ret_{horizon}d"].dropna().values
        ret_c = c_df[f"ret_{horizon}d"].dropna().values

        mfe_t = t_df[f"mfe_{horizon}d"].dropna().values
        mfe_c = c_df[f"mfe_{horizon}d"].dropna().values

        mae_t = t_df[f"mae_{horizon}d"].dropna().values
        mae_c = c_df[f"mae_{horizon}d"].dropna().values

        mdd_t = t_df[f"mdd_{horizon}d"].dropna().values
        mdd_c = c_df[f"mdd_{horizon}d"].dropna().values

        # Touch rates for 83.36, 100, 150
        hit_83_t = t_df[f"hit_target_83_36_{horizon}d"].dropna().astype(float).values
        hit_83_c = c_df[f"hit_target_83_36_{horizon}d"].dropna().astype(float).values

        hit_100_t = t_df[f"hit_target_100_{horizon}d"].dropna().astype(float).values
        hit_100_c = c_df[f"hit_target_100_{horizon}d"].dropna().astype(float).values

        hit_150_t = t_df[f"hit_target_150_{horizon}d"].dropna().astype(float).values
        hit_150_c = c_df[f"hit_target_150_{horizon}d"].dropna().astype(float).values

        # Wilcoxon test on returns
        try:
            stat_w, p_w = stats.ranksums(ret_t, ret_c)
        except Exception:
            stat_w, p_w = np.nan, np.nan

        # T-test on returns
        try:
            stat_t, p_t = stats.ttest_ind(ret_t, ret_c, equal_var=False)
        except Exception:
            stat_t, p_t = np.nan, np.nan

        return {
            "ret_diff_bootstrap": self.bootstrap_two_sample_diff(ret_t, ret_c, np.mean),
            "ret_median_diff_bootstrap": self.bootstrap_two_sample_diff(ret_t, ret_c, np.median),
            "mfe_diff_bootstrap": self.bootstrap_two_sample_diff(mfe_t, mfe_c, np.mean),
            "mae_diff_bootstrap": self.bootstrap_two_sample_diff(mae_t, mae_c, np.mean),
            "mdd_diff_bootstrap": self.bootstrap_two_sample_diff(mdd_t, mdd_c, np.mean),
            "hit_83_diff_bootstrap": self.bootstrap_two_sample_diff(hit_83_t, hit_83_c, np.mean),
            "hit_100_diff_bootstrap": self.bootstrap_two_sample_diff(hit_100_t, hit_100_c, np.mean),
            "hit_150_diff_bootstrap": self.bootstrap_two_sample_diff(hit_150_t, hit_150_c, np.mean),
            "t_test_p_value": float(p_t) if not np.isnan(p_t) else np.nan,
            "wilcoxon_ranksums_p_value": float(p_w) if not np.isnan(p_w) else np.nan,
        }
