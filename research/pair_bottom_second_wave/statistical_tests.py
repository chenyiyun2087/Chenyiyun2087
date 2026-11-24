"""Statistical Hypothesis Testing and Multiple Testing FDR Engine."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd
from scipy import stats


def benjamini_hochberg_fdr(p_values: Dict[str, float], alpha: float = 0.05) -> Dict[str, Any]:
    """Applies Benjamini-Hochberg False Discovery Rate correction."""
    valid_items = [(k, v) for k, v in p_values.items() if not np.isnan(v)]
    if not valid_items:
        return {}

    sorted_items = sorted(valid_items, key=lambda x: x[1])
    m = len(sorted_items)
    fdr_results = {}

    for rank, (k, p_val) in enumerate(sorted_items, start=1):
        crit_val = (rank / m) * alpha
        adj_p = min(1.0, p_val * (m / rank))
        fdr_results[k] = {
            "p_value": float(p_val),
            "rank": rank,
            "bh_threshold": float(crit_val),
            "q_value": float(adj_p),
            "significant_at_alpha": bool(p_val <= crit_val),
        }

    return fdr_results


class StatisticalTester:
    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha

    def compare_treatment_control(
        self,
        treatment_df: pd.DataFrame,
        control_df: pd.DataFrame,
        horizon: int = 40,
    ) -> Dict[str, Any]:
        """Performs Welch t-test and Wilcoxon rank-sum test on forward returns and MFE."""
        ret_col = f"ret_{horizon}d"
        mfe_col = f"mfe_{horizon}d"

        t_rets = treatment_df[ret_col].dropna().values if ret_col in treatment_df.columns else np.array([])
        c_rets = control_df[ret_col].dropna().values if ret_col in control_df.columns else np.array([])

        if len(t_rets) < 5 or len(c_rets) < 5:
            return {"status": "INSUFFICIENT_SAMPLES", "horizon": horizon}

        # Welch t-test
        t_stat, t_pval = stats.ttest_ind(t_rets, c_rets, equal_var=False)

        # Wilcoxon rank-sum
        w_stat, w_pval = stats.ranksums(t_rets, c_rets)

        diff_mean = float(np.mean(t_rets) - np.mean(c_rets))
        diff_median = float(np.median(t_rets) - np.median(c_rets))

        return {
            "horizon": horizon,
            "n_treatment": len(t_rets),
            "n_control": len(c_rets),
            "t_ret_mean": float(np.mean(t_rets)),
            "c_ret_mean": float(np.mean(c_rets)),
            "diff_mean": diff_mean,
            "diff_median": diff_median,
            "welch_t_stat": float(t_stat),
            "welch_t_pval": float(t_pval),
            "wilcoxon_ranksums_pval": float(w_pval),
        }
