"""Multiple-testing corrections for the alpha challenger comparison.

Pre-registered 2026-08-04 (alpha_rebuild_202608): 12 registered objects
(11 historical strategy candidates + 1 independent B-sleeve) compete;
uncorrected p-values would inflate the false-discovery rate.  This module
provides Benjamini-Hochberg FDR, Bonferroni, Holm, and the Deflated Sharpe
Ratio (Bailey/López de Prado) used by the unified registry.

v5.4.1 evidence-repair policy:
    Approximate p-values (normal-on-Sharpe, e.g. the `p_approx` pattern
    that rank_alpha_challengers.py previously built with a fixed n=60) are
    BANNED from the formal registry.  Family corrections must be applied
    to REAL permutation p-values (see scripts/research/formal_significance.py
    `permutation_p` / `holm_family`).  The BH/Bonferroni/Holm functions here
    remain valid — they are distribution-agnostic — but the values fed to
    them must come from permutation nulls, never from normal approximations.

Usage (called by rank_alpha_challengers.py):
    from scripts.research.multiple_testing_correction import (
        benjamini_hochberg, bonferroni, holm, deflated_sharpe_ratio)
    from scripts.research.formal_significance import (
        permutation_p, holm_family, load_permutation_null)
"""

from __future__ import annotations

import math

import numpy as np


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """FDR control: reject the largest k such that p_(k) <= alpha * k / m."""
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    if m == 0:
        return []
    order = np.argsort(p)
    sorted_p = p[order]
    thresholds = np.arange(1, m + 1) * alpha / m
    # Largest index where sorted_p <= threshold (monotone pass from the end).
    k = 0
    for i in range(m - 1, -1, -1):
        if sorted_p[i] <= thresholds[i]:
            k = i + 1
            break
    rejected = np.zeros(m, dtype=bool)
    rejected[order[:k]] = True
    return rejected.tolist()


def bonferroni(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Family-wise control: reject when p <= alpha / m."""
    m = max(1, len(p_values))
    return [bool(p <= alpha / m) for p in p_values]


def holm(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Holm step-down FWER control (uniformly more powerful than Bonferroni)."""
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    order = np.argsort(p)
    sorted_p = p[order]
    rejected = np.zeros(m, dtype=bool)
    for i, p_i in enumerate(sorted_p):
        if p_i <= alpha / (m - i):
            rejected[order[i]] = True
        else:
            break
    return rejected.tolist()


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    sample_skewness: float = 0.0,
    sample_kurtosis: float = 3.0,
    sample_size: int = 250,
    annualization: float = math.sqrt(250),
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    Adjusts an observed Sharpe for the number of independent trials that
    produced it (challenger count = multiple-testing population) and for
    non-normal returns.  Returns the probability that the true strategy
    Sharpe exceeds zero given the trials.

    Sharpe must be passed in annualized form (matching annualization).
    """
    if n_trials <= 1:
        return 1.0  # no deflation needed for a single candidate
    if sharpe <= 0:
        return 0.0
    # Euler-Mascheroni constant (math.euler_gamma is 3.11+; literal for portability).
    EULER_GAMMA = 0.5772156649015329
    # Expected max Sharpe under the null (standard normal, n trials).
    max_sharpe_null_annual = (
        (1.0 - EULER_GAMMA) * _phi_inv(1.0 - 1.0 / n_trials)
        + EULER_GAMMA * _phi_inv(1.0 - 1.0 / (n_trials * math.e))
    )
    # Work in per-period Sharpe units (Lo 2002 standard error, which reduces
    # to 1/sqrt(T) for normal returns):
    #   SE(SR) = sqrt((1 - skew*SR + (kurt-1)/4*SR^2) / T)
    sr_period = sharpe / annualization
    max_sr_null_period = max_sharpe_null_annual / annualization
    variance = (
        1.0
        - sample_skewness * sr_period
        + (sample_kurtosis - 1.0) / 4.0 * sr_period ** 2
    ) / max(sample_size, 1)
    sigma_sharpe = math.sqrt(max(variance, 1e-12))
    z = (sr_period - max_sr_null_period) / sigma_sharpe
    # One-sided CDF of the standard normal.
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Standard normal quantile (Acklam's approximation)."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = (-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00)
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
