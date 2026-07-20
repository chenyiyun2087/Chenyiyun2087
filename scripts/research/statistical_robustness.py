"""Statistical robustness analysis for strategy backtest results.

Computes:
  - Deflated Sharpe Ratio (DSR) with multiple trials correction
  - Probability of Backtest Overfitting (PBO)
  - Block bootstrap confidence intervals
  - Return distribution percentiles
  - Factor exposure regression

All methods are self-contained (no DB dependency) — input is a list of
daily return series from backtest output.
"""

from __future__ import annotations

import math
import random
from itertools import combinations
from dataclasses import dataclass
from typing import Any


@dataclass
class RobustnessReport:
    """Statistical robustness assessment for a single strategy."""

    strategy: str
    n_days: int
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float
    calmar_ratio: float

    # Deflated Sharpe
    deflated_sharpe: float
    deflated_sharpe_pvalue: float
    deflated_sharpe_confidence: float    # 1 - pvalue

    # PBO
    pbo: float
    pbo_passed: bool                      # ≤ 0.20

    # Bootstrap
    bootstrap_return_5th: float
    bootstrap_return_95th: float
    bootstrap_max_dd_5th: float
    bootstrap_max_dd_95th: float

    # Single-source concentration
    max_single_month_return: float
    max_single_month_pct_total: float
    positive_month_ratio: float

    passed: bool
    failures: list[str]


def _annualized_return(daily_returns: list[float], trading_days: int = 252) -> float:
    if not daily_returns:
        return 0.0
    total = 1.0
    for r in daily_returns:
        total *= (1.0 + r)
    years = len(daily_returns) / trading_days
    return total ** (1.0 / max(years, 0.25)) - 1.0 if years > 0 else 0.0


def _annualized_vol(daily_returns: list[float], trading_days: int = 252) -> float:
    if len(daily_returns) < 5:
        return 0.0
    mean_r = sum(daily_returns) / len(daily_returns)
    var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    return math.sqrt(var) * math.sqrt(trading_days)


def _max_drawdown(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    peak = 1.0
    nav = 1.0
    max_dd = 0.0
    for r in daily_returns:
        nav *= (1.0 + r)
        if nav > peak:
            peak = nav
        dd = nav / peak - 1.0
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _sharpe(daily_returns: list[float]) -> float:
    vol = _annualized_vol(daily_returns)
    if vol == 0:
        return 0.0
    return _annualized_return(daily_returns) / vol


def _block_bootstrap(
    daily_returns: list[float],
    n_simulations: int = 1000,
    block_size: int = 20,
    seed: int = 42,
) -> list[list[float]]:
    """Generate bootstrapped return series using block sampling."""
    rng = random.Random(seed)
    n = len(daily_returns)
    if n < block_size:
        block_size = max(1, n // 4)

    n_blocks = n // block_size
    simulated_series: list[list[float]] = []

    for _ in range(n_simulations):
        sim: list[float] = []
        while len(sim) < n:
            start = rng.randint(0, n - block_size)
            sim.extend(daily_returns[start:start + block_size])
        simulated_series.append(sim[:n])

    return simulated_series


def compute_deflated_sharpe(
    daily_returns: list[float],
    n_trials: int = 20,          # estimated number of strategy variations tested
    n_simulations: int = 1000,
) -> tuple[float, float, float]:
    """Compute Deflated Sharpe Ratio (DSR) with multiple testing correction.

    DSR = Prob(max SR among n_trials independent trials ≤ observed SR | H0: true SR=0)

    Returns (deflated_sharpe, pvalue, confidence).
    """
    observed_sr = _sharpe(daily_returns)
    if observed_sr <= 0:
        return 0.0, 1.0, 0.0

    # Under H0: SR = 0, max SR among n_trials follows extreme value distribution
    # Approximate using: DSR ≈ Φ((observed_SR - E[max]) / std[max])
    # where E[max] and std[max] are the mean and std of max SR under null
    # For simplicity, use Monte Carlo under null (random walk)
    n = len(daily_returns)
    rng = random.Random(42)
    null_max_srs: list[float] = []

    for _ in range(n_simulations):
        max_sr = 0.0
        for _ in range(n_trials):
            # Generate random walk under null
            null_returns = [rng.gauss(0, 0.02) for _ in range(n)]  # ~2% daily vol
            sr = _sharpe(null_returns)
            if sr > max_sr:
                max_sr = sr
        null_max_srs.append(max_sr)

    null_max_srs.sort()
    # Fraction of null max SRs ≤ observed SR
    rank = sum(1 for s in null_max_srs if s <= observed_sr)
    confidence = rank / n_simulations
    pvalue = 1.0 - confidence

    # Deflated Sharpe: penalize by the expected max under null
    mean_null_max = sum(null_max_srs) / n_simulations
    deflated = max(0.0, observed_sr - mean_null_max)

    return deflated, round(pvalue, 4), round(confidence, 4)


def compute_pbo(
    daily_returns: list[float],
    n_simulations: int = 500,
) -> float:
    """Compute Probability of Backtest Overfitting (PBO).

    PBO = fraction of in-sample optimal configurations that underperform
    the median in out-of-sample.

    For a single return series without explicit IS/OOS split, we use
    a rolling cross-validation approximation: split into first 60% (IS)
    and last 40% (OOS), then bootstrap to estimate PBO.
    """
    n = len(daily_returns)
    if n < 60:
        return 1.0  # Not enough data

    split = int(n * 0.6)
    is_returns = daily_returns[:split]
    oos_returns = daily_returns[split:]

    is_sharpe = _sharpe(is_returns)
    oos_sharpe = _sharpe(oos_returns)

    # If OOS Sharpe is close to IS, low overfitting risk
    # PBO ≈ probability that IS rank exceeds OOS rank
    if is_sharpe <= 0:
        return 1.0

    # Bootstrap the IS/OOS comparison
    rng = random.Random(42)
    n_better_in_is = 0
    for _ in range(n_simulations):
        # Resample with replacement
        is_boot = [rng.choice(is_returns) for _ in range(len(is_returns))]
        oos_boot = [rng.choice(oos_returns) for _ in range(len(oos_returns))]
        if _sharpe(is_boot) <= _sharpe(oos_boot):
            n_better_in_is += 1

    pbo = 1.0 - n_better_in_is / n_simulations
    return round(pbo, 4)


def combinatorial_purged_splits(n_samples: int, *, n_groups: int = 6, test_groups: int = 2,
                                purge: int = 10, embargo: int = 5) -> list[tuple[list[int], list[int]]]:
    """Return CPCV train/test indices with purge and embargo around test blocks."""
    if n_samples < n_groups or not 0 < test_groups < n_groups:
        raise ValueError("cpcv_invalid_shape")
    groups = [list(map(int, block)) for block in __import__("numpy").array_split(range(n_samples), n_groups)]
    splits: list[tuple[list[int], list[int]]] = []
    for selected in combinations(range(n_groups), test_groups):
        test = sorted(index for group in selected for index in groups[group])
        excluded = set(test)
        for index in test:
            excluded.update(range(max(0, index - purge), min(n_samples, index + embargo + 1)))
        train = [index for index in range(n_samples) if index not in excluded]
        if train and test:
            splits.append((train, test))
    return splits


def compute_cpcv_pbo(configuration_returns: list[list[float]], *, n_groups: int = 6,
                     test_groups: int = 2, purge: int = 10, embargo: int = 5) -> float:
    if len(configuration_returns) < 2:
        return 1.0
    n = min(len(values) for values in configuration_returns)
    losses = 0
    splits = combinatorial_purged_splits(n, n_groups=n_groups, test_groups=test_groups, purge=purge, embargo=embargo)
    for train, test in splits:
        in_sample = [_sharpe([values[i] for i in train]) for values in configuration_returns]
        selected = max(range(len(in_sample)), key=in_sample.__getitem__)
        out_sample = [_sharpe([values[i] for i in test]) for values in configuration_returns]
        median = sorted(out_sample)[len(out_sample) // 2]
        losses += out_sample[selected] < median
    return round(losses / max(len(splits), 1), 4)


def whites_reality_check(candidate_returns: list[list[float]], benchmark_returns: list[float],
                         *, n_bootstrap: int = 1000, block_size: int = 20, seed: int = 42) -> float:
    """Block-bootstrap p-value for the best candidate's mean excess return."""
    if not candidate_returns or not benchmark_returns:
        return 1.0
    n = min(len(benchmark_returns), *(len(values) for values in candidate_returns))
    excess = [[candidate[i] - benchmark_returns[i] for i in range(n)] for candidate in candidate_returns]
    observed = max(sum(values) / n for values in excess)
    centered = [[value - sum(values) / n for value in values] for values in excess]
    rng = random.Random(seed)
    exceed = 0
    for _ in range(n_bootstrap):
        sample_indices: list[int] = []
        while len(sample_indices) < n:
            start = rng.randint(0, max(0, n - min(block_size, n)))
            sample_indices.extend(range(start, min(n, start + block_size)))
        statistic = max(sum(values[i] for i in sample_indices[:n]) / n for values in centered)
        exceed += statistic >= observed
    return round((exceed + 1) / (n_bootstrap + 1), 4)


def compute_concentration_metrics(
    monthly_returns: list[float],
) -> dict[str, Any]:
    """Compute single-period and single-source concentration metrics."""
    if not monthly_returns:
        return {"max_month_pct": 0, "positive_month_ratio": 0}

    total_return = sum(monthly_returns)
    max_month = max(monthly_returns)
    max_pct = max_month / total_return if total_return > 0 else 1.0
    positive_ratio = sum(1 for r in monthly_returns if r > 0) / len(monthly_returns)

    return {
        "max_single_month_return": round(max_month, 6),
        "max_single_month_pct_total": round(max_pct, 4),
        "positive_month_ratio": round(positive_ratio, 4),
    }


def analyze_strategy_robustness(
    daily_returns: list[float],
    monthly_returns: list[float] | None = None,
    strategy_name: str = "unknown",
    n_trials: int = 20,
) -> RobustnessReport:
    """Run full statistical robustness analysis on a strategy return series.

    Args:
        daily_returns: List of daily returns (sorted chronologically).
        monthly_returns: Optional monthly returns for concentration analysis.
        strategy_name: Strategy identifier for the report.
        n_trials: Estimated number of strategy variations tested (for DSR).

    Returns:
        RobustnessReport with all metrics and pass/fail status.
    """
    ann_ret = _annualized_return(daily_returns)
    ann_vol = _annualized_vol(daily_returns)
    sr = _sharpe(daily_returns)
    mdd = _max_drawdown(daily_returns)
    calmar = ann_ret / abs(mdd) if mdd != 0 else 0.0
    n_days = len(daily_returns)

    # Deflated Sharpe
    dsr, dsr_pval, dsr_conf = compute_deflated_sharpe(daily_returns, n_trials=n_trials)

    # PBO
    pbo = compute_pbo(daily_returns)

    # Bootstrap
    boot_series = _block_bootstrap(daily_returns, n_simulations=500)
    boot_ann_rets = [_annualized_return(s) for s in boot_series]
    boot_ann_rets.sort()
    boot_mdds = [_max_drawdown(s) for s in boot_series]
    boot_mdds.sort()

    # Concentration
    monthly = monthly_returns or _daily_to_monthly(daily_returns)
    conc = compute_concentration_metrics(monthly)

    # Acceptance thresholds
    failures: list[str] = []
    if dsr_conf < 0.95:
        failures.append(f"DSR confidence={dsr_conf:.2f} < 0.95")
    if pbo > 0.20:
        failures.append(f"PBO={pbo:.2f} > 0.20")
    if boot_ann_rets[int(len(boot_ann_rets) * 0.05)] < 0:
        failures.append("Bootstrap 5th percentile annual return < 0")
    if boot_mdds[int(len(boot_mdds) * 0.95)] < -0.40:
        failures.append("Bootstrap 95th percentile max DD < -40%")
    if conc["max_single_month_pct_total"] > 0.20:
        failures.append(f"Single month profit contribution={conc['max_single_month_pct_total']:.2f} > 0.20")

    return RobustnessReport(
        strategy=strategy_name,
        n_days=n_days,
        annualized_return=round(ann_ret, 6),
        annualized_volatility=round(ann_vol, 6),
        sharpe_ratio=round(sr, 4),
        max_drawdown=round(mdd, 6),
        calmar_ratio=round(calmar, 4),
        deflated_sharpe=round(dsr, 4),
        deflated_sharpe_pvalue=dsr_pval,
        deflated_sharpe_confidence=dsr_conf,
        pbo=pbo,
        pbo_passed=pbo <= 0.20,
        bootstrap_return_5th=round(boot_ann_rets[int(len(boot_ann_rets) * 0.05)], 6) if boot_ann_rets else 0,
        bootstrap_return_95th=round(boot_ann_rets[int(len(boot_ann_rets) * 0.95)], 6) if boot_ann_rets else 0,
        bootstrap_max_dd_5th=round(boot_mdds[int(len(boot_mdds) * 0.05)], 6) if boot_mdds else 0,
        bootstrap_max_dd_95th=round(boot_mdds[int(len(boot_mdds) * 0.95)], 6) if boot_mdds else 0,
        max_single_month_return=conc["max_single_month_return"],
        max_single_month_pct_total=conc["max_single_month_pct_total"],
        positive_month_ratio=conc["positive_month_ratio"],
        passed=len(failures) == 0,
        failures=failures,
    )


def _daily_to_monthly(daily_returns: list[float]) -> list[float]:
    """Approximate monthly returns from daily returns (22 trading days per month)."""
    monthly: list[float] = []
    for i in range(0, len(daily_returns), 22):
        chunk = daily_returns[i:i + 22]
        if chunk:
            m_ret = 1.0
            for r in chunk:
                m_ret *= (1.0 + r)
            monthly.append(m_ret - 1.0)
    return monthly
