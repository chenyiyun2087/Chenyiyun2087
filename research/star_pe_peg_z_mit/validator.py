"""MIT Three Gates validation and paired block bootstrap confidence interval evaluation."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats


def calc_sharpe(returns: np.ndarray | pd.Series, annual_sessions: int = 252) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or np.std(r, ddof=1) == 0:
        return 0.0
    return float(np.mean(r) / np.std(r, ddof=1) * math.sqrt(annual_sessions))


def calc_deflated_sharpe(returns: np.ndarray, n_trials: int, sr_var: float) -> float:
    """Deflated Sharpe ratio (Bailey & Lopez de Prado, 2014)."""
    r = np.asarray(returns, float)
    T = len(r)
    if T < 30 or np.std(r, ddof=1) == 0:
        return 0.0
    sr = float(np.mean(r) / np.std(r, ddof=1))
    g3 = float(stats.skew(r))
    g4 = float(stats.kurtosis(r, fisher=False))
    n = max(int(n_trials), 2)
    emc = 0.5772156649
    sr0 = math.sqrt(max(sr_var, 1e-12)) * (
        (1.0 - emc) * stats.norm.ppf(1.0 - 1.0 / n) + emc * stats.norm.ppf(1.0 - 1.0 / (n * math.e))
    )
    denom = math.sqrt(max(1.0 - g3 * sr + (g4 - 1.0) / 4.0 * (sr ** 2), 1e-12))
    return float(stats.norm.cdf((sr - sr0) * math.sqrt(T - 1) / denom))


def block_permutation_pvalue(
    exposure_series: np.ndarray,
    asset_returns: np.ndarray,
    annual_sessions: int = 252,
    n_perm: int = 2000,
    block_size: int = 20,
    seed: int = 20261127,
) -> Tuple[float, float]:
    """Luck test: shuffle blocks of exposure against asset returns."""
    pos = np.asarray(exposure_series, float)
    ret = np.asarray(asset_returns, float)
    obs = calc_sharpe(pos * ret, annual_sessions)
    rng = np.random.default_rng(seed)
    n = len(pos)
    nb = int(math.ceil(n / block_size))
    blocks = [pos[i * block_size : (i + 1) * block_size] for i in range(nb)]
    hits = 0
    for _ in range(n_perm):
        order = rng.permutation(nb)
        shuffled = np.concatenate([blocks[i] for i in order])[:n]
        if calc_sharpe(shuffled * ret, annual_sessions) >= obs:
            hits += 1
    return obs, float((hits + 1) / (n_perm + 1))


def paired_block_bootstrap_ci(
    returns_a: np.ndarray,
    returns_b: np.ndarray,
    block_size: int = 20,
    n_boot: int = 2000,
    confidence: float = 0.95,
    seed: int = 20261127,
) -> Dict[str, Any]:
    """Paired block bootstrap confidence interval for difference series diff = returns_a - returns_b."""
    ra = np.asarray(returns_a, float)
    rb = np.asarray(returns_b, float)
    valid_mask = np.isfinite(ra) & np.isfinite(rb)
    if np.sum(valid_mask) < block_size:
        return {
            "mean_diff_annualized": 0.0,
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "confidence": confidence,
            "p_value": 1.0,
            "significant": False,
        }
    ra = ra[valid_mask]
    rb = rb[valid_mask]
    diff = ra - rb
    n = len(diff)
    if n < block_size:
        return {
            "mean_diff_annualized": float(np.mean(diff) * 252.0),
            "ci_lower": 0.0,
            "ci_upper": 0.0,
            "confidence": confidence,
            "p_value": 1.0,
            "significant": False,
        }

    nb = int(math.ceil(n / block_size))
    blocks = [diff[i * block_size : (i + 1) * block_size] for i in range(nb)]

    rng = np.random.default_rng(seed)
    boot_means = []
    for _ in range(n_boot):
        order = rng.choice(nb, size=nb, replace=True)
        resampled = np.concatenate([blocks[i] for i in order])[:n]
        boot_means.append(float(np.mean(resampled) * 252.0))

    boot_means = np.array(boot_means)
    alpha = (1.0 - confidence) / 2.0
    ci_lower = float(np.percentile(boot_means, alpha * 100.0))
    ci_upper = float(np.percentile(boot_means, (1.0 - alpha) * 100.0))
    # Two-sided empirical p-value of mean == 0
    obs_ann_mean = float(np.mean(diff) * 252.0)
    p_val = float(min(np.mean(boot_means <= 0), np.mean(boot_means >= 0)) * 2.0)

    return {
        "mean_diff_annualized": obs_ann_mean,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "confidence": confidence,
        "p_value": min(p_val, 1.0),
        "significant": bool((ci_lower > 0 and ci_upper > 0) or (ci_lower < 0 and ci_upper < 0)),
    }


class MitThreeGatesValidator:
    """Runs 5-fold sequential walk-forward and evaluates Gate 1 (OOS), Gate 2 (Luck), and Gate 3 (Cost)."""

    def __init__(
        self,
        folds_count: int = 5,
        min_oos_sessions: int = 497,
        gross_sharpe_threshold: float = 0.50,
        positive_folds_ratio: float = 0.60,
        permutation_trials: int = 2000,
        fdr_q: float = 0.10,
        dsr_threshold: float = 0.95,
        net_sharpe_threshold: float = 0.30,
        annual_sessions: int = 252,
    ):
        self.folds_count = folds_count
        self.min_oos_sessions = min_oos_sessions
        self.gross_sharpe_threshold = gross_sharpe_threshold
        self.positive_folds_ratio = positive_folds_ratio
        self.permutation_trials = permutation_trials
        self.fdr_q = fdr_q
        self.dsr_threshold = dsr_threshold
        self.net_sharpe_threshold = net_sharpe_threshold
        self.annual_sessions = annual_sessions

    def evaluate_strategy(
        self,
        daily_snapshots: List[Any],
        benchmark_snapshots: List[Any],
        double_cost_snapshots: Optional[List[Any]] = None,
        total_strategies_evaluated: int = 6,
    ) -> Dict[str, Any]:
        """Evaluates Gate 1, 2, 3 on daily snapshots."""
        T = len(daily_snapshots)
        insufficient_sample = T < self.min_oos_sessions

        net_returns = np.array([s.daily_return for s in daily_snapshots])
        exposures = np.array([s.exposure for s in daily_snapshots])
        bm_returns = np.array([b.daily_return for b in benchmark_snapshots]) if benchmark_snapshots else np.zeros(T)

        # Gross return approximation (return without deduction of fees)
        fee_impact = np.array([s.fills_count * 0.00075 / 3.0 for s in daily_snapshots])  # approx fee load
        gross_returns = net_returns + fee_impact

        # 5-fold sequential walk forward
        fold_indices = np.array_split(np.arange(T), self.folds_count)
        folds_detail = []
        positive_folds = 0

        for f_idx, fold in enumerate(fold_indices):
            f_gross_sr = calc_sharpe(gross_returns[fold], self.annual_sessions)
            f_net_sr = calc_sharpe(net_returns[fold], self.annual_sessions)
            is_pos = f_gross_sr > 0
            if is_pos:
                positive_folds += 1
            folds_detail.append(
                {
                    "fold": f_idx + 1,
                    "sessions": len(fold),
                    "start_date": daily_snapshots[fold[0]].trade_date,
                    "end_date": daily_snapshots[fold[-1]].trade_date,
                    "gross_sharpe": round(f_gross_sr, 4),
                    "net_sharpe": round(f_net_sr, 4),
                    "is_positive": is_pos,
                }
            )

        pos_fold_ratio = positive_folds / self.folds_count
        overall_gross_sr = calc_sharpe(gross_returns, self.annual_sessions)
        overall_net_sr = calc_sharpe(net_returns, self.annual_sessions)

        double_cost_sr = (
            calc_sharpe([s.daily_return for s in double_cost_snapshots], self.annual_sessions)
            if double_cost_snapshots
            else overall_net_sr - 0.15
        )

        # Gate 1: OOS Sharpe >= 0.5 and >= 3 folds positive
        gate1_pass = (overall_gross_sr >= self.gross_sharpe_threshold) and (pos_fold_ratio >= self.positive_folds_ratio)

        # Gate 2: Luck test (Block permutation p-value & Deflated Sharpe Ratio)
        _, p_val = block_permutation_pvalue(exposures, bm_returns, self.annual_sessions, self.permutation_trials)
        dsr_val = calc_deflated_sharpe(gross_returns, total_strategies_evaluated, sr_var=0.04)
        gate2_pass = (p_val <= self.fdr_q) and (dsr_val >= self.dsr_threshold)

        # Gate 3: Cost test (Net Sharpe >= 0.3 and double-cost Net Sharpe > 0)
        gate3_pass = (overall_net_sr >= self.net_sharpe_threshold) and (double_cost_sr > 0.0)

        all_gates_pass = gate1_pass and gate2_pass and gate3_pass and not insufficient_sample

        return {
            "sessions": T,
            "insufficient_sample": insufficient_sample,
            "gross_sharpe": round(overall_gross_sr, 4),
            "net_sharpe": round(overall_net_sr, 4),
            "double_cost_sharpe": round(double_cost_sr, 4),
            "positive_fold_ratio": round(pos_fold_ratio, 4),
            "permutation_p_value": round(p_val, 4),
            "dsr_probability": round(dsr_val, 4),
            "gates": {
                "gate1_oos": gate1_pass,
                "gate2_luck": gate2_pass,
                "gate3_cost": gate3_pass,
            },
            "status": "PASS_RESEARCH" if all_gates_pass else "REJECTED",
            "folds": folds_detail,
        }
