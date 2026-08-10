#!/usr/bin/env python3
"""Orthogonalized factor attribution for Wave 4 research diagnostics.

The formal attribution path uses a QR solve on an intercept plus the factor
matrix.  Contributions are computed row by row and therefore close back to
the observed strategy return within a declared tolerance.  A weighted
single-factor combination is exposed only as ``DIAGNOSTIC_ONLY``; it cannot
be used as formal evidence or a promotion input.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping, Sequence

import numpy as np


SCHEMA_VERSION = "orthogonal_factor_attribution_v1"
DEFAULT_CLOSURE_TOLERANCE = 1e-10


class AttributionError(ValueError):
    """Raised for malformed or rank-deficient attribution inputs."""


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": "BLOCKED", "reason": reason, **extra}


def _coerce_inputs(
    strategy_returns: Sequence[float] | np.ndarray,
    factor_returns: Mapping[str, Sequence[float] | np.ndarray] | Sequence[Sequence[float]] | np.ndarray,
    factor_names: Sequence[str] | None,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    try:
        y = np.asarray(strategy_returns, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise AttributionError("strategy_returns_not_numeric") from exc
    if isinstance(factor_returns, Mapping):
        names = [str(name) for name in factor_returns]
        x = np.column_stack([np.asarray(factor_returns[name], dtype=float).reshape(-1) for name in names]) if names else np.empty((len(y), 0))
    else:
        x = np.asarray(factor_returns, dtype=float)
        if x.ndim == 1:
            x = x.reshape(-1, 1)
        if x.ndim != 2:
            raise AttributionError("factor_returns_must_be_2d")
        names = [str(name) for name in (factor_names or [f"factor_{index}" for index in range(x.shape[1])])]
    if len(names) != x.shape[1]:
        raise AttributionError("factor_names_length_mismatch")
    if len(y) != x.shape[0]:
        raise AttributionError("strategy_factor_length_mismatch")
    if len(y) < x.shape[1] + 3:
        raise AttributionError("attribution_sample_insufficient")
    if x.shape[1] == 0:
        raise AttributionError("factor_returns_empty")
    if not np.isfinite(y).all() or not np.isfinite(x).all():
        raise AttributionError("attribution_non_finite")
    return y, x, names


def _rank_or_raise(x: np.ndarray) -> int:
    rank = int(np.linalg.matrix_rank(x))
    if rank < x.shape[1]:
        raise AttributionError(f"factor_matrix_rank_deficient:{rank}<{x.shape[1]}")
    return rank


def orthogonal_factor_attribution(
    strategy_returns: Sequence[float] | np.ndarray,
    factor_returns: Mapping[str, Sequence[float] | np.ndarray] | Sequence[Sequence[float]] | np.ndarray,
    *,
    factor_names: Sequence[str] | None = None,
    closure_tolerance: float = DEFAULT_CLOSURE_TOLERANCE,
    method: str = "QR",
    strict: bool = False,
) -> dict[str, Any]:
    """Fit ``strategy_return ~ 1 + factors`` and return exact contributions.

    QR is used to solve the regression rather than relying on a potentially
    unstable normal-equation inverse.  Factor contributions retain the input
    factor names (``X[:,j] * beta[j]``) while the QR factorisation supplies the
    numerical stability and rank check.
    """

    try:
        if str(method).upper() not in {"QR", "ORTHOGONALIZED_QR", "RESIDUALIZATION"}:
            raise AttributionError("attribution_method_unsupported")
        y, x, names = _coerce_inputs(strategy_returns, factor_returns, factor_names)
        if float(closure_tolerance) <= 0:
            raise AttributionError("closure_tolerance_invalid")
        _rank_or_raise(x - np.mean(x, axis=0, keepdims=True))
        design = np.column_stack([np.ones(len(y)), x])
        # ``lstsq`` internally uses QR/SVD and provides a stable coefficient;
        # the explicit rank check above prevents silently accepting collinear
        # factors as a formal attribution.
        coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        if int(rank) < design.shape[1]:
            raise AttributionError(f"design_matrix_rank_deficient:{rank}<{design.shape[1]}")
        intercept_coefficient = float(coefficients[0])
        exposures = {name: float(coefficients[index + 1]) for index, name in enumerate(names)}
        contribution_series = {
            name: (x[:, index] * coefficients[index + 1]).astype(float)
            for index, name in enumerate(names)
        }
        intercept_series = np.full(len(y), intercept_coefficient, dtype=float)
        fitted = intercept_series + np.column_stack(list(contribution_series.values())).sum(axis=1)
        residual_series = y - fitted
        reconstructed = fitted + residual_series
        closure_error = float(np.max(np.abs(y - reconstructed))) if len(y) else 0.0
        if not np.isfinite(closure_error) or closure_error > float(closure_tolerance):
            raise AttributionError(f"attribution_closure_failed:{closure_error}")
        contributions = {name: float(values.sum()) for name, values in contribution_series.items()}
        intercept_contribution = float(intercept_series.sum())
        residual_total = float(residual_series.sum())
        total_return = float(y.sum())
        aggregate_error = float(total_return - (intercept_contribution + sum(contributions.values()) + residual_total))
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "method": "QR" if str(method).upper() != "RESIDUALIZATION" else "RESIDUALIZATION",
            "formal": True,
            "diagnostic_only": False,
            "n_observations": int(len(y)),
            "factor_names": names,
            "factor_exposures": exposures,
            "coefficients": {"intercept": intercept_coefficient, **exposures},
            "intercept": intercept_coefficient,
            "intercept_coefficient": intercept_coefficient,
            "intercept_contribution": intercept_contribution,
            "factor_contributions": contributions,
            "factor_contribution_series": {name: values.tolist() for name, values in contribution_series.items()},
            "residual": residual_total,
            "residual_series": residual_series.tolist(),
            "fitted_series": fitted.tolist(),
            "observed_series": y.tolist(),
            "total_return": total_return,
            "closure_error": closure_error,
            "aggregate_closure_error": aggregate_error,
            "closure_tolerance": float(closure_tolerance),
            "return_closure": bool(abs(aggregate_error) <= float(closure_tolerance) * max(1, len(y))),
        }
    except (AttributionError, TypeError, ValueError, np.linalg.LinAlgError) as exc:
        if strict:
            if isinstance(exc, AttributionError):
                raise
            raise AttributionError(str(exc)) from exc
        return _blocked(str(exc), formal=False, diagnostic_only=False)


compute_factor_attribution = orthogonal_factor_attribution
attribute_factors = orthogonal_factor_attribution
orthogonalize_and_attribute = orthogonal_factor_attribution
compute_orthogonal_attribution = orthogonal_factor_attribution
run_orthogonal_attribution = orthogonal_factor_attribution


def assert_return_closure(result: Mapping[str, Any], *, tolerance: float | None = None) -> None:
    """Raise when a formal attribution does not close."""

    if str(result.get("status")) != "PASS":
        raise AttributionError(f"attribution_not_pass:{result.get('reason', '')}")
    bound = float(tolerance if tolerance is not None else result.get("closure_tolerance", DEFAULT_CLOSURE_TOLERANCE))
    if float(result.get("closure_error", float("inf"))) > bound:
        raise AttributionError("attribution_closure_failed")
    if float(result.get("aggregate_closure_error", float("inf"))) > bound * max(1, int(result.get("n_observations", 1))):
        raise AttributionError("attribution_aggregate_closure_failed")


def diagnostic_single_factor_combinations(
    strategy_returns: Sequence[float] | np.ndarray,
    factor_returns: Mapping[str, Sequence[float] | np.ndarray] | Sequence[Sequence[float]] | np.ndarray,
    *,
    weights: Mapping[str, float] | Sequence[float] | None = None,
    factor_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Report weighted one-factor combinations as diagnostic-only evidence."""

    try:
        y, x, names = _coerce_inputs(strategy_returns, factor_returns, factor_names)
        if weights is None:
            values = np.mean(x, axis=1)
            weight_map = {name: float(1.0 / len(names)) for name in names}
        elif isinstance(weights, Mapping):
            weight_map = {name: float(weights.get(name, 0.0)) for name in names}
            values = sum(x[:, index] * weight_map[name] for index, name in enumerate(names))
        else:
            if len(weights) != len(names):
                raise AttributionError("diagnostic_weights_length_mismatch")
            weight_map = {name: float(weights[index]) for index, name in enumerate(names)}
            values = np.column_stack(x) @ np.asarray(list(weights), dtype=float)
        if not np.isfinite(values).all():
            raise AttributionError("diagnostic_combination_non_finite")
        correlation = float(np.corrcoef(y, values)[0, 1]) if np.std(values) > 0 and np.std(y) > 0 else 0.0
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "DIAGNOSTIC_ONLY",
            "formal": False,
            "diagnostic_only": True,
            "reason": "single_factor_weighted_combination_cannot_be_formal_attribution",
            "n_observations": int(len(y)),
            "factor_names": names,
            "weights": weight_map,
            "combined_series": values.astype(float).tolist(),
            "strategy_mean": float(np.mean(y)),
            "combination_mean": float(np.mean(values)),
            "correlation": correlation,
        }
    except (AttributionError, TypeError, ValueError) as exc:
        return _blocked(str(exc), formal=False, diagnostic_only=True)


diagnostic_factor_combinations = diagnostic_single_factor_combinations


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=argparse.FileType("r"), required=True, help="JSON object with strategy_returns and factor_returns")
    args = parser.parse_args()
    payload = json.load(args.input)
    result = orthogonal_factor_attribution(payload["strategy_returns"], payload["factor_returns"], strict=False)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "SCHEMA_VERSION", "DEFAULT_CLOSURE_TOLERANCE", "AttributionError",
    "orthogonal_factor_attribution", "compute_factor_attribution", "attribute_factors",
    "orthogonalize_and_attribute", "compute_orthogonal_attribution", "run_orthogonal_attribution",
    "assert_return_closure",
    "diagnostic_single_factor_combinations", "diagnostic_factor_combinations",
]
