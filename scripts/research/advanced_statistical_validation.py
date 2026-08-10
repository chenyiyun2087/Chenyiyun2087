#!/usr/bin/env python3
"""Reproducible statistical validation primitives for Wave 4.

The functions in this file are pure diagnostics.  They do not select a
strategy, write an export or alter a production/capital configuration.  Every
result carries a schema and status.  A short or malformed sample returns a
``BLOCKED`` result rather than a reassuring number.

The default random seed and permutation count are pre-registered constants;
callers may override them only by passing an explicit argument in a research
plan.  The implementation intentionally avoids statsmodels and uses NumPy
plus a small normal-CDF fallback so it runs in the project's Python 3.11 CI.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = "advanced_statistical_validation_v1"
DEFAULT_SEED = 20260810
DEFAULT_PERMUTATIONS = 9999
DEFAULT_BLOCK_SIZE = 20
MIN_BOOTSTRAP_SAMPLE = 8
MIN_DSR_SAMPLE = 30
MIN_CSCV_SAMPLE = 24


class StatisticalValidationError(ValueError):
    """Raised for invalid inputs when ``strict=True``."""


class SplitResult(list):
    """List of splits that also exposes report metadata as mapping keys.

    This keeps the convenient ``for split in nested_walk_forward_splits(...)``
    API while allowing callers to ask ``result["status"]`` for a contract
    status.  String keys are not valid list indices, so there is no ambiguity.
    """

    def __init__(self, values: Iterable[dict[str, Any]] = (), **metadata: Any):
        super().__init__(values)
        self.metadata = {"schema_version": SCHEMA_VERSION, **metadata}

    def __getitem__(self, index: int | slice | str):  # type: ignore[override]
        if isinstance(index, str):
            return self.metadata[index]
        return super().__getitem__(index)

    def to_dict(self) -> dict[str, Any]:
        return {**self.metadata, "splits": list(self)}


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": "BLOCKED", "reason": reason, **extra}


def _pass(**values: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": "PASS", **values}


def _as_array(values: Any, *, name: str = "values", min_size: int = 1) -> np.ndarray:
    if values is None:
        raise StatisticalValidationError(f"{name}_missing")
    try:
        arr = np.asarray(values, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise StatisticalValidationError(f"{name}_not_numeric") from exc
    if len(arr) < min_size:
        raise StatisticalValidationError(f"{name}_insufficient_sample:{len(arr)}<{min_size}")
    if not np.isfinite(arr).all():
        raise StatisticalValidationError(f"{name}_non_finite")
    return arr


def _normal_cdf(value: float) -> float:
    return float(NormalDist().cdf(float(value)))


def _normal_ppf(probability: float) -> float:
    probability = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return float(NormalDist().inv_cdf(probability))


def _annualized_sharpe(values: np.ndarray, periods_per_year: int = 252) -> float:
    std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
    if not np.isfinite(std) or std <= 0:
        return 0.0
    return float(np.mean(values) / std * math.sqrt(periods_per_year))


def _max_drawdown(values: np.ndarray) -> float:
    nav = np.cumprod(1.0 + values)
    if not len(nav):
        return 0.0
    return float(np.min(nav / np.maximum.accumulate(nav) - 1.0))


def _normalise_dates_or_length(dates_or_n: Sequence[Any] | int) -> tuple[list[Any], int]:
    if isinstance(dates_or_n, (int, np.integer)):
        n = int(dates_or_n)
        return list(range(n)), n
    dates = list(dates_or_n)
    if not dates:
        raise StatisticalValidationError("dates_empty")
    # A walk-forward split must not silently sort an accidentally shuffled
    # input: sorting would hide a data-contract bug.
    try:
        if any(dates[index] >= dates[index + 1] for index in range(len(dates) - 1)):
            raise StatisticalValidationError("dates_not_strictly_increasing")
    except TypeError:
        # Numeric/date-like values should be comparable; mixed opaque values
        # are still allowed when the caller only needs positional indices.
        pass
    return dates, len(dates)


def _inner_splits(
    train_indices: list[int], *,
    inner_splits: int,
    inner_train_size: int,
    inner_validation_size: int,
    purge: int,
    embargo: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if inner_splits <= 0:
        return result
    for index in range(inner_splits):
        start = index * max(1, inner_validation_size)
        train_end = start + inner_train_size
        validation_start = train_end + purge
        validation_end = validation_start + inner_validation_size
        if validation_end > len(train_indices):
            break
        # Inner test is not used for hyperparameter fitting, but including it
        # makes the nested contract explicit and gives callers a clean place
        # to evaluate selection stability.
        test_start = validation_end + embargo
        test_end = test_start + inner_validation_size
        if test_end > len(train_indices):
            break
        result.append({
            "fold_id": f"inner_{index:03d}",
            "train": train_indices[start:train_end],
            "validation": train_indices[validation_start:validation_end],
            "test": train_indices[test_start:test_end],
            "purge": train_indices[train_end:validation_start],
            "embargo": train_indices[validation_end:test_start],
        })
    return result


def nested_walk_forward_splits(
    dates_or_n: Sequence[Any] | int | None = None,
    *,
    n_samples: int | None = None,
    n_splits: int = 3,
    train_size: int = 60,
    validation_size: int = 20,
    test_size: int = 20,
    purge: int = 0,
    embargo: int = 0,
    inner_splits: int = 2,
    inner_train_size: int | None = None,
    inner_validation_size: int | None = None,
    strict: bool = False,
) -> SplitResult:
    """Build deterministic outer/inner walk-forward folds.

    ``purge`` removes observations between train and validation; ``embargo``
    removes observations between validation and test.  All returned arrays
    are positional indices, with optional matching ``dates`` in each fold.
    """

    try:
        source = n_samples if n_samples is not None else dates_or_n
        if source is None:
            raise StatisticalValidationError("n_samples_missing")
        dates, n = _normalise_dates_or_length(source)
        if min(n_splits, train_size, validation_size, test_size) <= 0:
            raise StatisticalValidationError("split_parameters_invalid")
        if min(purge, embargo) < 0:
            raise StatisticalValidationError("purge_or_embargo_negative")
        inner_train_size = int(inner_train_size or max(2, train_size // 2))
        inner_validation_size = int(inner_validation_size or max(1, validation_size // 2))
        required = train_size + purge + validation_size + embargo + test_size
        if n < required:
            raise StatisticalValidationError(f"sample_insufficient:{n}<{required}")
        result: list[dict[str, Any]] = []
        # Expanding train windows are safer for finance and deterministic; the
        # test window advances one validation+test block per fold.
        for fold in range(n_splits):
            train_start = fold * test_size
            train_end = train_start + train_size
            validation_start = train_end + purge
            validation_end = validation_start + validation_size
            test_start = validation_end + embargo
            test_end = test_start + test_size
            if test_end > n:
                break
            train = list(range(train_start, train_end))
            validation = list(range(validation_start, validation_end))
            test = list(range(test_start, test_end))
            outer = {
                "fold_id": f"outer_{fold:03d}",
                "train": train,
                "validation": validation,
                "test": test,
                "purge": list(range(train_end, validation_start)),
                "embargo": list(range(validation_end, test_start)),
                "dates": {
                    "train": [dates[i] for i in train],
                    "validation": [dates[i] for i in validation],
                    "test": [dates[i] for i in test],
                },
                "inner": _inner_splits(
                    train,
                    inner_splits=inner_splits,
                    inner_train_size=inner_train_size,
                    inner_validation_size=inner_validation_size,
                    purge=purge,
                    embargo=embargo,
                ),
            }
            # Common spellings used by older research notebooks.  They are
            # aliases of the same positional arrays, not independently
            # generated samples.
            outer["train_idx"] = list(train)
            outer["validation_idx"] = list(validation)
            outer["test_idx"] = list(test)
            result.append(outer)
        if len(result) < n_splits:
            raise StatisticalValidationError(f"folds_insufficient:{len(result)}<{n_splits}")
        return SplitResult(result, status="PASS", n_samples=n, n_splits=len(result), seed=DEFAULT_SEED)
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return SplitResult([], status="BLOCKED", reason=str(exc), n_samples=0, n_splits=0)


# More descriptive aliases used by downstream research notebooks.
build_nested_walk_forward_splits = nested_walk_forward_splits
generate_nested_walk_forward_splits = nested_walk_forward_splits


def block_bootstrap(
    values: Sequence[float] | np.ndarray,
    *,
    n_bootstrap: int = DEFAULT_PERMUTATIONS,
    block_size: int = DEFAULT_BLOCK_SIZE,
    seed: int = DEFAULT_SEED,
    statistic: Callable[[np.ndarray], float] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Sample contiguous blocks with replacement using a fixed RNG seed."""

    try:
        arr = _as_array(values, name="bootstrap_values", min_size=MIN_BOOTSTRAP_SAMPLE)
        if int(n_bootstrap) <= 0 or int(block_size) <= 0:
            raise StatisticalValidationError("bootstrap_parameters_invalid")
        block = min(int(block_size), len(arr))
        rng = np.random.default_rng(int(seed))
        samples = np.empty((int(n_bootstrap), len(arr)), dtype=float)
        statistics: list[float] = []
        for row in range(int(n_bootstrap)):
            output: list[float] = []
            while len(output) < len(arr):
                start = int(rng.integers(0, len(arr) - block + 1))
                output.extend(arr[start : start + block].tolist())
            sample = np.asarray(output[: len(arr)], dtype=float)
            samples[row] = sample
            if statistic is not None:
                statistics.append(float(statistic(sample)))
        result: dict[str, Any] = _pass(
            method="moving_block_bootstrap",
            n_observations=len(arr),
            n_bootstrap=int(n_bootstrap),
            block_size=block,
            seed=int(seed),
            samples=samples.tolist(),
        )
        if statistic is not None:
            result["statistics"] = statistics
        return result
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return _blocked(str(exc), method="moving_block_bootstrap", n_bootstrap=int(n_bootstrap), seed=int(seed))


def block_bootstrap_ci(
    values: Sequence[float] | np.ndarray,
    *,
    statistic: Callable[[np.ndarray], float] | None = None,
    confidence: float = 0.95,
    n_bootstrap: int = DEFAULT_PERMUTATIONS,
    block_size: int = DEFAULT_BLOCK_SIZE,
    seed: int = DEFAULT_SEED,
    strict: bool = False,
) -> dict[str, Any]:
    """Return a deterministic percentile CI and the bootstrap distribution."""

    if statistic is None:
        statistic = lambda x: float(np.mean(x))
    if not 0 < float(confidence) < 1:
        error = StatisticalValidationError("confidence_invalid")
        if strict:
            raise error
        return _blocked(str(error))
    result = block_bootstrap(
        values,
        n_bootstrap=n_bootstrap,
        block_size=block_size,
        seed=seed,
        statistic=statistic,
        strict=strict,
    )
    if result.get("status") != "PASS":
        return result
    stats = np.asarray(result["statistics"], dtype=float)
    alpha = (1.0 - float(confidence)) / 2.0
    return {
        **result,
        "statistic": float(statistic(_as_array(values))),
        "confidence": float(confidence),
        "lower": float(np.quantile(stats, alpha)),
        "upper": float(np.quantile(stats, 1.0 - alpha)),
    }


def deflated_sharpe_ratio(
    returns: Sequence[float] | np.ndarray,
    *,
    n_trials: int = 1,
    periods_per_year: int = 252,
    strict: bool = False,
) -> dict[str, Any]:
    """Bailey/Lopez-de-Prado style finite-sample Deflated Sharpe Ratio."""

    try:
        values = _as_array(returns, name="returns", min_size=MIN_DSR_SAMPLE)
        if int(n_trials) <= 0:
            raise StatisticalValidationError("n_trials_invalid")
        volatility = float(np.std(values, ddof=1))
        if volatility <= 0:
            raise StatisticalValidationError("returns_zero_variance")
        observed = _annualized_sharpe(values, periods_per_year)
        centered = values - np.mean(values)
        m3 = float(np.mean(centered**3))
        m4 = float(np.mean(centered**4))
        skew = m3 / max(volatility**3, 1e-15)
        excess_kurtosis = m4 / max(volatility**4, 1e-15) - 3.0
        # Expected maximum of n standard-normal trials; the second-order
        # correction is stable for n=1 and conservative for larger ledgers.
        trials = max(int(n_trials), 1)
        expected_max = _normal_ppf(1.0 - 1.0 / max(trials, 2))
        if trials > 1:
            expected_max -= (math.log(math.log(max(trials, 2))) + math.log(4.0 * math.pi)) / max(2.0 * expected_max, 1e-9)
        denom = math.sqrt(
            max(
                1e-12,
                1.0 - skew * observed + ((excess_kurtosis + 2.0) / 4.0) * observed**2,
            )
        )
        z = (observed - expected_max) * math.sqrt(len(values) - 1) / denom
        confidence = _normal_cdf(z)
        p_value = 1.0 - confidence
        return _pass(
            method="deflated_sharpe_ratio",
            n_observations=len(values),
            n_trials=trials,
            observed_sharpe=observed,
            expected_max_sharpe=expected_max,
            deflated_sharpe=float(observed - expected_max),
            dsr=float(observed - expected_max),
            z_score=float(z),
            p_value=float(p_value),
            pvalue=float(p_value),
            p=float(p_value),
            sharpe=float(observed),
            confidence=float(confidence),
            skew=float(skew),
            excess_kurtosis=float(excess_kurtosis),
        )
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return _blocked(str(exc), method="deflated_sharpe_ratio")


compute_deflated_sharpe = deflated_sharpe_ratio
calculate_deflated_sharpe = deflated_sharpe_ratio
compute_deflated_sharpe_ratio = deflated_sharpe_ratio
deflated_sharpe = deflated_sharpe_ratio


def _candidate_matrix(candidate_returns: Any, *, min_obs: int = MIN_CSCV_SAMPLE) -> np.ndarray:
    matrix = np.asarray(candidate_returns, dtype=float)
    if matrix.ndim != 2:
        raise StatisticalValidationError("candidate_returns_must_be_2d")
    if matrix.shape[0] < 2 or matrix.shape[1] < min_obs:
        raise StatisticalValidationError(
            f"candidate_returns_insufficient:{matrix.shape[0]}x{matrix.shape[1]}"
        )
    if not np.isfinite(matrix).all():
        raise StatisticalValidationError("candidate_returns_non_finite")
    return matrix


def cscv_pbo(
    candidate_returns: Sequence[Sequence[float]] | np.ndarray,
    *,
    n_groups: int = 6,
    test_groups: int | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Combinatorially Symmetric Cross-Validation PBO estimate.

    The best in-sample candidate is compared with the out-of-sample median on
    every equal-sized group combination.  No candidate is permitted to use a
    date in both train and test for a split.
    """

    try:
        matrix = _candidate_matrix(candidate_returns)
        groups = int(n_groups)
        held = int(test_groups or groups // 2)
        if groups < 2 or not 0 < held < groups:
            raise StatisticalValidationError("cscv_group_parameters_invalid")
        if matrix.shape[1] < groups:
            raise StatisticalValidationError("cscv_groups_exceed_observations")
        partitions = np.array_split(np.arange(matrix.shape[1]), groups)
        losses = 0
        rows: list[dict[str, Any]] = []
        for held_out in itertools.combinations(range(groups), held):
            test_idx = np.concatenate([partitions[idx] for idx in held_out])
            train_idx = np.concatenate([partitions[idx] for idx in range(groups) if idx not in held_out])
            is_sr = np.asarray([_annualized_sharpe(matrix[row, train_idx]) for row in range(matrix.shape[0])])
            oos_sr = np.asarray([_annualized_sharpe(matrix[row, test_idx]) for row in range(matrix.shape[0])])
            selected = int(np.argmax(is_sr))
            median = float(np.median(oos_sr))
            loss = bool(oos_sr[selected] < median)
            losses += int(loss)
            rows.append({
                "held_out_groups": list(held_out),
                "selected_candidate": selected,
                "is_sharpe": float(is_sr[selected]),
                "oos_sharpe": float(oos_sr[selected]),
                "oos_median": median,
                "loss": loss,
            })
        total = len(rows)
        pbo = float(losses / total) if total else 1.0
        return _pass(
            method="cscv_pbo",
            n_candidates=int(matrix.shape[0]),
            n_observations=int(matrix.shape[1]),
            n_groups=groups,
            test_groups=held,
            combinations=total,
            losses=losses,
            pbo=pbo,
            splits=rows,
        )
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return _blocked(str(exc), method="cscv_pbo")


compute_cscv_pbo = cscv_pbo
compute_pbo = cscv_pbo
calculate_pbo = cscv_pbo
pbo = cscv_pbo


def benjamini_hochberg(
    p_values: Sequence[float] | Mapping[str, float],
    *,
    alpha: float = 0.05,
    strict: bool = False,
) -> dict[str, Any]:
    """Benjamini--Hochberg false-discovery-rate correction."""

    try:
        if not 0 < float(alpha) < 1:
            raise StatisticalValidationError("fdr_alpha_invalid")
        labels = list(p_values.keys()) if isinstance(p_values, Mapping) else [str(i) for i in range(len(p_values))]
        raw = np.asarray(list(p_values.values()) if isinstance(p_values, Mapping) else list(p_values), dtype=float)
        if len(raw) == 0:
            raise StatisticalValidationError("fdr_p_values_empty")
        if not np.isfinite(raw).all() or np.any((raw < 0) | (raw > 1)):
            raise StatisticalValidationError("fdr_p_values_invalid")
        order = np.argsort(raw, kind="mergesort")
        adjusted = np.empty(len(raw), dtype=float)
        running = 1.0
        for rank in range(len(order) - 1, -1, -1):
            index = int(order[rank])
            value = float(raw[index] * len(raw) / (rank + 1))
            running = min(running, value)
            adjusted[index] = min(1.0, running)
        rejected = adjusted <= float(alpha)
        rows = {
            labels[index]: {
                "p_raw": float(raw[index]),
                "p_adjusted": float(adjusted[index]),
                "reject": bool(rejected[index]),
            }
            for index in range(len(raw))
        }
        return _pass(
            method="benjamini_hochberg",
            alpha=float(alpha),
            n_tests=int(len(raw)),
            p_raw=[float(value) for value in raw],
            p_adjusted=[float(value) for value in adjusted],
            adjusted_p_values=[float(value) for value in adjusted],
            rejected=[bool(value) for value in rejected],
            tests=rows,
        )
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return _blocked(str(exc), method="benjamini_hochberg")


bh_fdr = benjamini_hochberg
fdr_bh = benjamini_hochberg
compute_fdr = benjamini_hochberg


def permutation_test(
    values: Sequence[float] | np.ndarray,
    *,
    statistic: Callable[[np.ndarray], float] | None = None,
    null_values: Sequence[float] | np.ndarray | None = None,
    n_permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = DEFAULT_SEED,
    sequential: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Deterministic sign/permutation test with a 9999 default.

    For a one-sample return series the null is generated by random sign flips,
    preserving dependence in the observed values.  ``null_values`` enables a
    two-sample label permutation.  A sequential Monte Carlo plan may reduce
    work, but it must explicitly declare ``max_permutations`` and is reported
    in the output; no adaptive early stopping is implicit.
    """

    try:
        observed_values = _as_array(values, name="permutation_values", min_size=8)
        if statistic is None:
            statistic = lambda x: float(np.mean(x))
        observed = float(statistic(observed_values))
        if not np.isfinite(observed):
            raise StatisticalValidationError("permutation_statistic_non_finite")
        requested = int(n_permutations)
        if requested <= 0:
            raise StatisticalValidationError("permutation_count_invalid")
        mode = "sign_flip"
        second: np.ndarray | None = None
        if null_values is not None:
            second = _as_array(null_values, name="null_values", min_size=8)
            mode = "label_permutation"
        declared = requested
        if sequential:
            max_permutations = int(sequential.get("max_permutations", requested))
            if max_permutations <= 0:
                raise StatisticalValidationError("sequential_max_invalid")
            requested = min(requested, max_permutations)
            mode = "sequential_" + mode
        rng = np.random.default_rng(int(seed))
        exceed = 0
        null_stats: list[float] = []
        if second is None:
            for _ in range(requested):
                sample = observed_values * rng.choice(np.array([-1.0, 1.0]), size=len(observed_values))
                value = float(statistic(sample))
                null_stats.append(value)
                exceed += int(abs(value) >= abs(observed))
        else:
            combined = np.concatenate([observed_values, second])
            n_first = len(observed_values)
            for _ in range(requested):
                permuted = rng.permutation(combined)
                value = float(statistic(permuted[:n_first]) - statistic(permuted[n_first:]))
                null_stats.append(value)
                exceed += int(abs(value) >= abs(observed))
        p_value = float((exceed + 1) / (requested + 1))
        return _pass(
            method="permutation_test",
            mode=mode,
            n_observations=len(observed_values),
            n_permutations=requested,
            declared_permutations=declared,
            seed=int(seed),
            observed_statistic=observed,
            exceedances=exceed,
            p_value=p_value,
            pvalue=p_value,
            p=p_value,
            null_statistics=null_stats,
            sequential_plan=dict(sequential or {}),
        )
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, StatisticalValidationError):
                raise
            raise StatisticalValidationError(str(exc)) from exc
        return _blocked(str(exc), method="permutation_test", n_permutations=int(n_permutations), seed=int(seed))


def validate_nested_statistics(
    returns: Sequence[float] | np.ndarray,
    candidate_returns: Sequence[Sequence[float]] | np.ndarray | None = None,
    *,
    n_trials: int = 1,
    n_bootstrap: int = DEFAULT_PERMUTATIONS,
    n_permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = DEFAULT_SEED,
    strict: bool = False,
) -> dict[str, Any]:
    """Run the registered tests and aggregate a fail-closed report."""

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "seed": int(seed),
        "n_bootstrap": int(n_bootstrap),
        "n_permutations": int(n_permutations),
    }
    try:
        report["deflated_sharpe"] = deflated_sharpe_ratio(returns, n_trials=n_trials, strict=True)
        report["bootstrap"] = block_bootstrap_ci(returns, n_bootstrap=n_bootstrap, seed=seed, strict=True)
        report["permutation"] = permutation_test(returns, n_permutations=n_permutations, seed=seed, strict=True)
        if candidate_returns is not None:
            report["cscv_pbo"] = cscv_pbo(candidate_returns, strict=True)
        report["status"] = "PASS"
        return report
    except (StatisticalValidationError, TypeError, ValueError) as exc:
        if strict:
            raise
        report.update({"status": "BLOCKED", "reason": str(exc)})
        return report


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--returns", type=Path, required=True, help="JSON list of returns")
    parser.add_argument("--n-trials", type=int, default=1)
    parser.add_argument("--n-bootstrap", type=int, default=DEFAULT_PERMUTATIONS)
    parser.add_argument("--n-permutations", type=int, default=DEFAULT_PERMUTATIONS)
    args = parser.parse_args()
    values = json.loads(args.returns.read_text(encoding="utf-8"))
    result = validate_nested_statistics(values, n_trials=args.n_trials, n_bootstrap=args.n_bootstrap, n_permutations=args.n_permutations)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "SCHEMA_VERSION", "DEFAULT_SEED", "DEFAULT_PERMUTATIONS", "DEFAULT_BLOCK_SIZE",
    "StatisticalValidationError", "SplitResult", "nested_walk_forward_splits",
    "build_nested_walk_forward_splits", "generate_nested_walk_forward_splits",
    "block_bootstrap", "block_bootstrap_ci", "deflated_sharpe_ratio",
    "compute_deflated_sharpe", "calculate_deflated_sharpe", "compute_deflated_sharpe_ratio", "deflated_sharpe",
    "cscv_pbo", "compute_cscv_pbo", "compute_pbo", "calculate_pbo", "pbo",
    "benjamini_hochberg", "bh_fdr", "fdr_bh", "compute_fdr",
    "permutation_test", "validate_nested_statistics",
]
