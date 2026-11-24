"""Block Bootstrap and Monte Carlo Simulation Engine.

Performs 10,000 resamples of empirical forward price paths to determine
target price touch and close attainment probabilities with 95% confidence intervals.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class SecondWaveBootstrapEngine:
    def __init__(self, n_simulations: int = 10000, random_seed: int = 20261124):
        self.n_simulations = n_simulations
        self.rng = np.random.default_rng(random_seed)

    def resample_target_probabilities(
        self,
        historical_cases_df: pd.DataFrame,
        base_price: float = 43.43,
        target_prices: Optional[List[float]] = None,
        horizons: List[int] = [20, 40, 60],
    ) -> Dict[str, Any]:
        """Calculates touch and close attainment probabilities for specified target prices."""
        target_prices = target_prices or [50.0, 60.0, 68.0, 80.0, 83.36, 100.0, 120.0, 150.0]

        n_samples = len(historical_cases_df)
        if n_samples == 0:
            return {"status": "INSUFFICIENT_EVIDENCE", "n_samples": 0}

        results: Dict[str, Any] = {
            "n_samples": n_samples,
            "base_price": base_price,
            "horizons": {},
        }

        for h in horizons:
            mfe_col = f"mfe_{h}d"
            ret_col = f"ret_{h}d"

            req_cols = [mfe_col]
            if ret_col in historical_cases_df.columns:
                req_cols.append(ret_col)

            valid_df = historical_cases_df[req_cols].dropna()
            n_valid = len(valid_df)

            h_res = {}
            if n_valid < 5:
                for tp in target_prices:
                    h_res[f"target_{tp:.2f}"] = {
                        "target_price": tp,
                        "required_gain": tp / base_price - 1.0,
                        "n_valid": n_valid,
                        "status": "INSUFFICIENT_EVIDENCE",
                    }
                results["horizons"][f"{h}d"] = h_res
                continue

            valid_mfe = valid_df[mfe_col].values
            valid_ret = valid_df[ret_col].values if ret_col in valid_df.columns else np.array([])

            for tp in target_prices:
                required_gain = (tp / base_price) - 1.0
                obs_touch = float(np.mean(valid_mfe >= required_gain))
                obs_close = float(np.mean(valid_ret >= required_gain)) if len(valid_ret) > 0 else 0.0

                # 10,000 Bootstrap iterations
                boot_touch = np.zeros(self.n_simulations)
                boot_close = np.zeros(self.n_simulations)

                for b in range(self.n_simulations):
                    idx = self.rng.choice(n_valid, size=n_valid, replace=True)
                    boot_touch[b] = np.mean(valid_mfe[idx] >= required_gain)
                    if len(valid_ret) > 0:
                        boot_close[b] = np.mean(valid_ret[idx] >= required_gain)

                touch_ci_low = float(np.percentile(boot_touch, 2.5))
                touch_ci_high = float(np.percentile(boot_touch, 97.5))
                close_ci_low = float(np.percentile(boot_close, 2.5)) if len(valid_ret) > 0 else 0.0
                close_ci_high = float(np.percentile(boot_close, 97.5)) if len(valid_ret) > 0 else 0.0

                rule_of_three = float(3.0 / n_valid) if obs_touch == 0.0 else np.nan

                h_res[f"target_{tp:.2f}"] = {
                    "target_price": tp,
                    "required_gain": required_gain,
                    "n_valid": n_valid,
                    "touch_prob": obs_touch,
                    "touch_ci_95": [touch_ci_low, touch_ci_high],
                    "close_prob": obs_close,
                    "close_ci_95": [close_ci_low, close_ci_high],
                    "rule_of_three_upper": rule_of_three,
                }

            results["horizons"][f"{h}d"] = h_res

        return results
