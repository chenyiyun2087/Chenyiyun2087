"""Alpha v3.5 proof-layer calculations.

The proof layer turns aligned daily evidence into benchmark-excess and
multi-factor attribution reports.  It is deliberately input-only: it never
queries mutable production data, changes a strategy, or authorizes capital.
Incomplete benchmarks, factor columns, coverage, or regression rank fail
closed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd


def _blocked_attribution(
    blockers: list[str], *, evidence_version: str | None = None, **details: Any
) -> dict[str, Any]:
    return {
        "schema_version": (
            "alpha_v3_2_attribution_v1"
            if str(evidence_version or "").startswith("alpha_v3_2")
            else "alpha_v3_5_attribution_v1"
        ),
        "status": "BLOCKED",
        "blockers": blockers,
        "factor_contributions": {},
        "residual_label": "regression_alpha",
        "residual": None,
        "regression_alpha": None,
        "stock_selection_alpha": None,
        "total_return": None,
        **details,
    }


def audit_factor_availability(
    panel: pd.DataFrame,
    profile: dict[str, Any],
    *,
    panel_name: str,
) -> dict[str, Any]:
    """Fail closed unless every factor value was available by its signal time."""
    factors = [str(value) for value in profile["attribution"]["required_factors"]]
    required_columns = ["signal_time", *[f"{name}_available_at" for name in factors]]
    blockers: list[str] = []
    if panel.empty:
        blockers.append(f"{panel_name}_missing")
        return {
            "schema_version": (
                "alpha_v3_2_factor_availability_v1"
                if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
                else "alpha_v3_5_factor_availability_v1"
            ),
            "panel": panel_name,
            "status": "BLOCKED",
            "blockers": blockers,
            "checked_rows": 0,
            "violations": [],
        }
    missing = [column for column in required_columns if column not in panel.columns]
    blockers.extend(f"{panel_name}_missing_column:{column}" for column in missing)
    if missing:
        return {
            "schema_version": (
                "alpha_v3_2_factor_availability_v1"
                if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
                else "alpha_v3_5_factor_availability_v1"
            ),
            "panel": panel_name,
            "status": "BLOCKED",
            "blockers": blockers,
            "checked_rows": int(len(panel)),
            "violations": [],
        }

    parsed: dict[str, pd.Series] = {}
    invalid_counts: dict[str, int] = {}
    naive_counts: dict[str, int] = {}
    timezone_mismatch_counts: dict[str, int] = {}
    allow_absolute_timezones = str(profile.get("evidence_version") or "") == "alpha_v3_2_evidence_v1"
    for column in required_columns:
        values: list[pd.Timestamp | pd.NaT] = []
        invalid = 0
        naive = 0
        timezone_mismatch = 0
        for raw in panel[column]:
            try:
                value = pd.Timestamp(raw)
            except (TypeError, ValueError):
                value = pd.NaT
            if pd.isna(value):
                invalid += 1
            elif value.tzinfo is None or value.utcoffset() is None:
                naive += 1
                value = pd.NaT
            else:
                if value.utcoffset() != timedelta(hours=8) and not allow_absolute_timezones:
                    timezone_mismatch += 1
                value = value.tz_convert("UTC")
            values.append(value)
        parsed[column] = pd.Series(values, index=panel.index, dtype="datetime64[ns, UTC]")
        invalid_counts[column] = invalid
        naive_counts[column] = naive
        timezone_mismatch_counts[column] = timezone_mismatch
        if invalid:
            blockers.append(f"{panel_name}_invalid_timestamp:{column}:{invalid}")
        if naive:
            blockers.append(f"{panel_name}_timezone_missing:{column}:{naive}")
        if timezone_mismatch:
            blockers.append(
                f"{panel_name}_timezone_mismatch:{column}:{timezone_mismatch}"
            )

    violations: list[dict[str, Any]] = []
    signal = parsed["signal_time"]
    for factor in factors:
        column = f"{factor}_available_at"
        late = parsed[column].notna() & signal.notna() & parsed[column].gt(signal)
        count = int(late.sum())
        if count:
            blockers.append(f"{panel_name}_future_factor:{factor}:{count}")
            for index in panel.index[late][:20]:
                violations.append(
                    {
                        "row": str(index),
                        "factor": factor,
                        "available_at": parsed[column].loc[index].isoformat(),
                        "signal_time": signal.loc[index].isoformat(),
                    }
                )
    return {
        "schema_version": (
            "alpha_v3_2_factor_availability_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_factor_availability_v1"
        ),
        "panel": panel_name,
        "status": "PASS" if not blockers else "BLOCKED",
        "timezone_policy": str(profile["alpha_proof"]["timezone"]),
        "timezone_comparison": (
            "absolute_instants_after_explicit_timezone_check"
            if allow_absolute_timezones
            else "Asia/Shanghai_offset_required"
        ),
        "comparison": "factor_available_at <= signal_time",
        "checked_rows": int(len(panel)),
        "blockers": blockers,
        "invalid_timestamp_counts": invalid_counts,
        "timezone_missing_counts": naive_counts,
        "timezone_mismatch_counts": timezone_mismatch_counts,
        "violations": violations,
    }


def _annualized(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    if values.empty or values.le(-1.0).any():
        return -1.0
    compounded = float((1.0 + values).prod())
    return compounded ** (252.0 / len(values)) - 1.0 if compounded > 0 else -1.0


def _daily_returns(nav: pd.DataFrame) -> pd.Series:
    if nav.empty or "trade_date" not in nav.columns:
        return pd.Series(dtype=float)
    value_column = "nav" if "nav" in nav.columns else "total_equity"
    if value_column not in nav.columns:
        return pd.Series(dtype=float)
    frame = nav[["trade_date", value_column]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame[value_column] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame.dropna().sort_values("trade_date")
    if frame["trade_date"].duplicated().any() or frame[value_column].le(0).any():
        return pd.Series(dtype=float)
    return frame.set_index("trade_date")[value_column].pct_change().dropna()


def _hac_mean_tstat(values: np.ndarray, max_lag: int = 5) -> float | None:
    """Newey-West/HAC t-statistic for a mean of autocorrelated returns."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    if n < 2:
        return None
    mean = float(values.mean())
    centered = values - mean
    lag = min(max(int(max_lag), 0), n - 1)
    long_run = float(np.mean(centered * centered))
    for k in range(1, lag + 1):
        gamma = float(np.mean(centered[k:] * centered[:-k]))
        long_run += 2.0 * (1.0 - k / (lag + 1.0)) * gamma
    se = float(np.sqrt(max(long_run, 0.0) / n))
    return float(mean / se) if se > 0 else None


def _hac_intercept_tstat(
    design: np.ndarray,
    residual: np.ndarray,
    intercept: float,
    max_lag: int = 5,
) -> float | None:
    """HAC sandwich t-statistic for the regression intercept."""
    n = len(residual)
    if n < 2:
        return None
    xtx_inv = np.linalg.pinv(design.T @ design)
    scores = design * residual[:, None]
    centered = scores - scores.mean(axis=0)
    lag = min(max(int(max_lag), 0), n - 1)
    long_run = centered.T @ centered / n
    for k in range(1, lag + 1):
        gamma = centered[k:].T @ centered[:-k] / n
        weight = 1.0 - k / (lag + 1.0)
        long_run += weight * (gamma + gamma.T)
    covariance = xtx_inv @ long_run @ xtx_inv * n
    se = float(np.sqrt(max(covariance[0, 0], 0.0)))
    return float(intercept / se) if se > 0 else None


def _benchmark_nav(
    benchmark_nav: pd.DataFrame,
    benchmark_id: str,
) -> pd.DataFrame:
    if benchmark_nav.empty or "trade_date" not in benchmark_nav.columns:
        return pd.DataFrame()
    for id_column in ("benchmark", "strategy", "symbol", "ts_code"):
        if id_column in benchmark_nav.columns:
            return benchmark_nav[
                benchmark_nav[id_column].astype(str).eq(benchmark_id)
            ].copy()
    if benchmark_id in benchmark_nav.columns:
        return benchmark_nav[["trade_date", benchmark_id]].rename(
            columns={benchmark_id: "nav"}
        )
    return pd.DataFrame()


def build_benchmark_excess_report(
    strategy_nav: pd.DataFrame,
    benchmark_nav: pd.DataFrame,
    profile: dict[str, Any],
    *,
    analysis_asof: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Compare strategy returns with every required benchmark."""
    strategy_returns = _daily_returns(strategy_nav)
    spec = profile["alpha_proof"]
    allow_absolute_timezones = str(profile.get("evidence_version") or "") == "alpha_v3_2_evidence_v1"
    benchmarks = profile["benchmarks"]
    required = [str(value) for value in benchmarks["required"]]
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    if analysis_asof is None and not strategy_returns.empty:
        analysis_asof = strategy_returns.index.max().tz_localize(
            "Asia/Shanghai"
        ) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    elif analysis_asof is not None:
        analysis_asof = pd.Timestamp(analysis_asof)
        if analysis_asof.tzinfo is None:
            analysis_asof = analysis_asof.tz_localize("Asia/Shanghai")
        analysis_asof = analysis_asof.tz_convert("UTC")
    for benchmark_id in required:
        raw = _benchmark_nav(benchmark_nav, benchmark_id)
        row_blockers: list[str] = []
        if raw.empty:
            row_blockers.append("series_missing")
        if "trade_date" not in raw.columns:
            row_blockers.append("trade_date_missing")
        else:
            dates = pd.to_datetime(raw["trade_date"], errors="coerce")
            if dates.isna().any():
                row_blockers.append("invalid_trade_date")
            if dates.dropna().duplicated().any():
                row_blockers.append("duplicate_trade_date")
        value_column = "nav" if "nav" in raw.columns else "total_equity"
        if value_column not in raw.columns:
            row_blockers.append("nav_missing")
        elif pd.to_numeric(raw[value_column], errors="coerce").isna().any() or pd.to_numeric(
            raw[value_column], errors="coerce"
        ).le(0).any():
            row_blockers.append("invalid_nav")
        if "available_at" not in raw.columns:
            row_blockers.append("available_at_missing")
        else:
            raw_available = []
            unavailable = False
            for value in raw["available_at"]:
                try:
                    timestamp = pd.Timestamp(value)
                except (TypeError, ValueError):
                    timestamp = pd.NaT
                if (
                    pd.isna(timestamp)
                    or timestamp.tzinfo is None
                    or timestamp.utcoffset() is None
                ):
                    unavailable = True
                    raw_available.append(pd.NaT)
                else:
                    if timestamp.utcoffset() != timedelta(hours=8) and not allow_absolute_timezones:
                        unavailable = True
                    raw_available.append(timestamp.tz_convert("UTC"))
            available = pd.Series(
                raw_available, index=raw.index, dtype="datetime64[ns, UTC]"
            )
            if unavailable:
                row_blockers.append("available_at_invalid_or_timezone_missing")
            elif analysis_asof is not None and available.gt(analysis_asof).any():
                row_blockers.append("available_after_analysis_asof")

        benchmark_returns = _daily_returns(raw)
        aligned = pd.concat(
            [
                strategy_returns.rename("strategy"),
                benchmark_returns.rename("benchmark"),
            ],
            axis=1,
            join="inner",
        ).dropna()
        coverage = float(len(aligned) / max(len(strategy_returns), 1))
        missing_dates = strategy_returns.index.difference(benchmark_returns.index)
        missing_spans = [value.date().isoformat() for value in missing_dates[:20]]
        present_mask = strategy_returns.index.isin(benchmark_returns.index)
        max_gap = 0
        current_gap = 0
        for present in present_mask:
            current_gap = 0 if present else current_gap + 1
            max_gap = max(max_gap, current_gap)
        strategy_end = (
            strategy_returns.index.max().date().isoformat()
            if not strategy_returns.empty
            else None
        )
        benchmark_end = (
            benchmark_returns.index.max().date().isoformat()
            if not benchmark_returns.empty
            else None
        )
        if strategy_end != benchmark_end:
            row_blockers.append("end_date_mismatch")
        complete = bool(
            not row_blockers
            and
            len(aligned) >= int(spec["min_aligned_trading_days"])
            and coverage >= float(spec["min_daily_coverage"])
        )
        if len(aligned) < int(spec["min_aligned_trading_days"]):
            row_blockers.append("aligned_history_insufficient")
        if coverage < float(spec["min_daily_coverage"]):
            row_blockers.append("coverage_insufficient")
        if not complete:
            blockers.append(f"benchmark_incomplete:{benchmark_id}")
            rows.append(
                {
                    "benchmark": benchmark_id,
                    "status": "BLOCKED",
                    "aligned_trading_days": int(len(aligned)),
                    "coverage": coverage,
                    "blockers": sorted(set(row_blockers)),
                    "aligned_start": (
                        aligned.index.min().date().isoformat()
                        if not aligned.empty
                        else None
                    ),
                    "aligned_end": (
                        aligned.index.max().date().isoformat()
                        if not aligned.empty
                        else None
                    ),
                    "missing_date_count": int(len(missing_dates)),
                    "missing_date_sample": missing_spans,
                    "max_consecutive_missing_trading_days": int(max_gap),
                    "strategy_annualized_return": None,
                    "benchmark_annualized_return": None,
                    "annualized_excess_return": None,
                    "information_ratio": None,
                    "beta": None,
                    "annualized_alpha": None,
                }
            )
            continue
        strategy_values = aligned["strategy"]
        benchmark_values = aligned["benchmark"]
        active = strategy_values - benchmark_values
        tracking_error = float(active.std(ddof=1) * np.sqrt(252.0))
        variance = float(benchmark_values.var(ddof=1))
        beta = (
            float(strategy_values.cov(benchmark_values) / variance)
            if variance > 0
            else None
        )
        alpha_daily = (
            float((strategy_values - float(beta) * benchmark_values).mean())
            if beta is not None
            else None
        )
        strategy_annualized = _annualized(strategy_values)
        benchmark_annualized = _annualized(benchmark_values)
        rows.append(
            {
                "benchmark": benchmark_id,
                "status": "PASS",
                "aligned_trading_days": int(len(aligned)),
                "coverage": coverage,
                "blockers": [],
                "aligned_start": aligned.index.min().date().isoformat(),
                "aligned_end": aligned.index.max().date().isoformat(),
                "missing_date_count": int(len(missing_dates)),
                "missing_date_sample": missing_spans,
                "max_consecutive_missing_trading_days": int(max_gap),
                "strategy_annualized_return": strategy_annualized,
                "benchmark_annualized_return": benchmark_annualized,
                "annualized_excess_return": strategy_annualized
                - benchmark_annualized,
                "information_ratio": (
                    float(active.mean() * 252.0 / tracking_error)
                    if tracking_error > 0
                    else None
                ),
                "beta": beta,
                "annualized_alpha": (
                    float((1.0 + alpha_daily) ** 252 - 1.0)
                    if alpha_daily is not None and alpha_daily > -1.0
                    else None
                ),
            }
        )
    primary = next(
        (row for row in rows if row["benchmark"] == str(benchmarks["primary"])),
        None,
    )
    primary_excess = primary.get("annualized_excess_return") if primary else None
    economic_pass = bool(
        not blockers
        and primary_excess is not None
        and float(primary_excess)
        > float(profile["performance"]["min_annualized_excess_return"])
    )
    return {
        "schema_version": (
            "alpha_v3_2_benchmark_excess_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_benchmark_excess_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "economic_status": "PASS" if economic_pass else "FAIL",
        "primary_benchmark": str(benchmarks["primary"]),
        "required_benchmarks": required,
        "fallback_policy": "FORBIDDEN",
        "analysis_asof": analysis_asof.isoformat() if analysis_asof is not None else None,
        "blockers": blockers,
        "rows": rows,
    }


def build_daily_factor_attribution(
    strategy_nav: pd.DataFrame,
    factor_returns: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Fit a full-rank daily factor model and close arithmetic return exactly."""
    required = [str(value) for value in profile["attribution"]["required_factors"]]
    spec = profile["alpha_proof"]
    strategy_returns = _daily_returns(strategy_nav)
    if factor_returns.empty or "trade_date" not in factor_returns.columns:
        return _blocked_attribution(
            ["factor_return_panel_missing"],
            evidence_version=str(profile.get("evidence_version") or ""),
        )
    missing = [name for name in required if name not in factor_returns.columns]
    if missing:
        return _blocked_attribution(
            [f"factor_return_missing:{name}" for name in missing],
            evidence_version=str(profile.get("evidence_version") or ""),
        )
    availability = audit_factor_availability(
        factor_returns, profile, panel_name="factor_returns"
    )
    if availability["status"] != "PASS":
        return _blocked_attribution(
            list(availability["blockers"]),
            evidence_version=str(profile.get("evidence_version") or ""),
            factor_availability=availability,
        )
    factors = factor_returns[["trade_date", *required]].copy()
    factors["trade_date"] = pd.to_datetime(factors["trade_date"], errors="coerce")
    for name in required:
        factors[name] = pd.to_numeric(factors[name], errors="coerce")
    factors = factors.dropna().sort_values("trade_date")
    if factors["trade_date"].duplicated().any():
        return _blocked_attribution(
            ["factor_return_duplicate_dates"],
            evidence_version=str(profile.get("evidence_version") or ""),
            factor_availability=availability,
        )
    aligned = pd.concat(
        [
            strategy_returns.rename("strategy_return"),
            factors.set_index("trade_date")[required],
        ],
        axis=1,
        join="inner",
    ).dropna()
    coverage = float(len(aligned) / max(len(strategy_returns), 1))
    blockers: list[str] = []
    if len(aligned) < int(spec["min_aligned_trading_days"]):
        blockers.append("factor_return_history_insufficient")
    if coverage < float(spec["min_daily_coverage"]):
        blockers.append("factor_return_coverage_insufficient")
    x = aligned[required].to_numpy(dtype=float)
    y = aligned["strategy_return"].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(x)), x]) if len(x) else np.empty((0, 0))
    rank = int(np.linalg.matrix_rank(design)) if len(x) else 0
    if bool(spec["require_full_rank_regression"]) and rank != len(required) + 1:
        blockers.append("factor_regression_not_full_rank")
    if blockers:
        return _blocked_attribution(
            blockers,
            evidence_version=str(profile.get("evidence_version") or ""),
            aligned_trading_days=int(len(aligned)),
            coverage=coverage,
            regression_rank=rank,
            factor_availability=availability,
        )
    coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    intercept = float(coefficients[0])
    exposures = {
        name: float(coefficients[index + 1])
        for index, name in enumerate(required)
    }
    contributions = {
        name: float((aligned[name] * exposures[name]).sum())
        for name in required
    }
    fitted_factors = sum(
        aligned[name].to_numpy(dtype=float) * exposures[name] for name in required
    )
    regression_residual = y - intercept - fitted_factors
    regression_alpha = float(intercept * len(y))
    unexplained_residual = float(regression_residual.sum())
    residual_mean = float(regression_residual.mean())
    residual_std = float(regression_residual.std(ddof=1))
    residual_tstat = _hac_mean_tstat(regression_residual)
    total_arithmetic = float(y.sum())
    closure_error = abs(
        total_arithmetic
        - sum(contributions.values())
        - regression_alpha
        - unexplained_residual
    )
    tolerance = float(profile["attribution"]["closure_tolerance"])
    if closure_error > tolerance:
        blockers.append(f"attribution_not_closed:{closure_error:.10f}")
    total_ss = float(((y - y.mean()) ** 2).sum())
    residual_ss = float((regression_residual**2).sum())
    unexplained_variance_ratio = residual_ss / total_ss if total_ss > 0 else 1.0
    warnings: list[str] = []
    if unexplained_variance_ratio > float(spec["max_unexplained_variance_ratio"]):
        blockers.append(
            f"unexplained_variance_ratio_exceeded:{unexplained_variance_ratio:.10f}"
        )
    elif unexplained_variance_ratio > float(
        spec["unexplained_variance_warning_ratio"]
    ):
        warnings.append(
            f"unexplained_variance_ratio_review:{unexplained_variance_ratio:.10f}"
        )
    dof = len(y) - len(required) - 1
    alpha_tstat = None
    if dof > 0:
        alpha_tstat = _hac_intercept_tstat(
            design, regression_residual, intercept
        )
    if alpha_tstat is None or alpha_tstat < float(spec["min_alpha_tstat"]):
        blockers.append(
            "alpha_tstat_insufficient:"
            + ("missing" if alpha_tstat is None else f"{alpha_tstat:.10f}")
        )
    return {
        "schema_version": (
            "alpha_v3_2_attribution_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_attribution_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "warnings": warnings,
        "return_basis": "arithmetic_sum_of_aligned_daily_returns",
        "aligned_trading_days": int(len(aligned)),
        "coverage": coverage,
        "regression_rank": rank,
        "factor_exposures": exposures,
        "factor_contributions": contributions,
        "residual_label": str(spec["residual_label"]),
        "residual": regression_alpha,
        "regression_alpha": regression_alpha,
        "stock_selection_alpha": None,
        "stock_selection_evidence_status": "NOT_PROVIDED",
        "unexplained_residual_return": unexplained_residual,
        "residual_cumulative_return": unexplained_residual,
        "residual_mean": residual_mean,
        "residual_std": residual_std,
        "residual_tstat": residual_tstat,
        "unexplained_variance_ratio": unexplained_variance_ratio,
        "max_unexplained_variance_ratio": float(
            spec["max_unexplained_variance_ratio"]
        ),
        "unexplained_variance_warning_ratio": float(
            spec["unexplained_variance_warning_ratio"]
        ),
        "min_alpha_tstat": float(spec["min_alpha_tstat"]),
        "total_return": total_arithmetic,
        "compounded_total_return": float((1.0 + aligned["strategy_return"]).prod() - 1.0),
        "closure_error": closure_error,
        "alpha_daily": intercept,
        "alpha_tstat": alpha_tstat,
        "alpha_annualized": (
            float((1.0 + intercept) ** 252 - 1.0) if intercept > -1.0 else -1.0
        ),
        "r_squared": (
            1.0 - residual_ss / total_ss if total_ss > 0 else 0.0
        ),
        "factor_availability": availability,
    }


def build_alpha_stability_report(
    strategy_nav: pd.DataFrame,
    factor_returns: pd.DataFrame,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Measure whether regression alpha persists across configured years."""
    spec = profile["alpha_proof"]
    required = [str(value) for value in profile["attribution"]["required_factors"]]
    availability = audit_factor_availability(
        factor_returns, profile, panel_name="factor_returns"
    )
    blockers: list[str] = []
    if availability["status"] != "PASS":
        blockers.extend(availability["blockers"])
    strategy_returns = _daily_returns(strategy_nav)
    if factor_returns.empty or any(
        column not in factor_returns.columns for column in ["trade_date", *required]
    ):
        blockers.append("factor_return_panel_missing_or_incomplete")
        return {
            "schema_version": (
                "alpha_v3_2_alpha_stability_v1"
                if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
                else "alpha_v3_5_alpha_stability_v1"
            ),
            "status": "BLOCKED",
            "blockers": sorted(set(blockers)),
            "rows": [],
            "score": 0.0,
        }
    factors = factor_returns[["trade_date", *required]].copy()
    factors["trade_date"] = pd.to_datetime(factors["trade_date"], errors="coerce")
    for name in required:
        factors[name] = pd.to_numeric(factors[name], errors="coerce")
    aligned = pd.concat(
        [
            strategy_returns.rename("strategy_return"),
            factors.set_index("trade_date")[required],
        ],
        axis=1,
        join="inner",
    ).dropna()
    rows: list[dict[str, Any]] = []
    min_days = int(spec["min_stability_year_trading_days"])
    for year in [int(value) for value in spec["stability_years"]]:
        sample = aligned[aligned.index.year == year]
        row: dict[str, Any] = {
            "year": year,
            "aligned_trading_days": int(len(sample)),
            "covered_months": int(sample.index.to_period("M").nunique()),
            "status": "INSUFFICIENT",
            "alpha_daily": None,
            "alpha_annualized": None,
            "alpha_contribution": None,
            "unexplained_variance_ratio": None,
        }
        if (
            len(sample) < min_days
            or row["covered_months"] < int(spec["min_stability_year_months"])
        ):
            rows.append(row)
            continue
        x = sample[required].to_numpy(dtype=float)
        y = sample["strategy_return"].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(x)), x])
        rank = int(np.linalg.matrix_rank(design))
        if rank != len(required) + 1:
            row["status"] = "BLOCKED"
            row["blocker"] = "factor_regression_not_full_rank"
            rows.append(row)
            continue
        coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        # Row-wise multiplication avoids platform BLAS overflow warnings seen
        # for otherwise finite, well-conditioned small regression matrices.
        residual = y - np.sum(design * coefficients, axis=1)
        total_ss = float(((y - y.mean()) ** 2).sum())
        residual_ss = float((residual**2).sum())
        ratio = residual_ss / total_ss if total_ss > 0 else 1.0
        alpha_daily = float(coefficients[0])
        row.update(
            {
                "status": (
                    "VALID"
                    if ratio <= float(spec["max_unexplained_variance_ratio"])
                    else "BLOCKED"
                ),
                "alpha_daily": alpha_daily,
                "alpha_annualized": (
                    float((1.0 + alpha_daily) ** 252 - 1.0)
                    if alpha_daily > -1.0
                    else -1.0
                ),
                "alpha_contribution": float(alpha_daily * len(sample)),
                "unexplained_variance_ratio": ratio,
            }
        )
        if row["status"] == "BLOCKED":
            row["blocker"] = "unexplained_variance_ratio_exceeded"
        rows.append(row)

    valid = [row for row in rows if row["status"] == "VALID"]
    positive = [
        row for row in valid if float(row["alpha_contribution"] or 0.0) > 0.0
    ]
    positive_ratio = float(len(positive) / len(valid)) if valid else 0.0
    total_positive = sum(float(row["alpha_contribution"]) for row in positive)
    max_share = (
        max(float(row["alpha_contribution"]) for row in positive) / total_positive
        if total_positive > 0
        else 1.0
    )
    score = 100.0 * (
        0.5 * positive_ratio + 0.5 * max(0.0, 1.0 - max_share)
    )
    if len(valid) < int(spec["min_valid_stability_years"]):
        blockers.append("valid_stability_years_insufficient")
    if positive_ratio < float(spec["min_positive_alpha_year_ratio"]):
        blockers.append("positive_alpha_year_ratio_insufficient")
    if max_share > float(spec["max_single_positive_year_alpha_contribution"]):
        blockers.append("single_year_alpha_contribution_excessive")
    valid_annualized = [
        float(row["alpha_annualized"])
        for row in valid
        if row["alpha_annualized"] is not None
    ]
    worst_year_alpha = min(valid_annualized) if valid_annualized else None
    if (
        worst_year_alpha is None
        or worst_year_alpha <= float(spec["worst_year_alpha_floor"])
    ):
        blockers.append("worst_year_alpha_below_floor")
    return {
        "schema_version": (
            "alpha_v3_2_alpha_stability_v1"
            if str(profile.get("evidence_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_alpha_stability_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "rows": rows,
        "valid_years": len(valid),
        "positive_alpha_year_ratio": positive_ratio,
        "max_single_positive_year_alpha_contribution": max_share,
        "worst_year_alpha": worst_year_alpha,
        "worst_year_alpha_floor": float(spec["worst_year_alpha_floor"]),
        "score": score,
        "score_formula": (
            "100 * (0.5 * positive_alpha_year_ratio + "
            "0.5 * (1 - max_single_positive_year_alpha_contribution))"
        ),
    }


def build_alpha_proof_guard_report(
    benchmark: dict[str, Any],
    factor_returns_availability: dict[str, Any],
    factor_panel_availability: dict[str, Any],
    attribution: dict[str, Any],
    stability: dict[str, Any],
    factor_lineage: dict[str, Any] | None = None,
    factor_effectiveness: dict[str, Any] | None = None,
    regime_attribution: dict[str, Any] | None = None,
    evidence_version: str | None = None,
) -> dict[str, Any]:
    benchmark_economic_status = str(
        benchmark.get("economic_status", benchmark.get("status") or "BLOCKED")
    )
    components = {
        "benchmark_safeguard": (
            "PASS"
            if benchmark.get("status") == "PASS"
            and benchmark_economic_status == "PASS"
            else "BLOCKED"
        ),
        "factor_returns_availability": str(
            factor_returns_availability.get("status") or "BLOCKED"
        ),
        "factor_panel_availability": str(
            factor_panel_availability.get("status") or "BLOCKED"
        ),
        "residual_audit": (
            "PASS"
            if attribution.get("status") == "PASS"
            and attribution.get("unexplained_variance_ratio") is not None
            and float(attribution.get("unexplained_variance_ratio")) <= float(
                attribution.get("max_unexplained_variance_ratio", 0.05)
            )
            else "BLOCKED"
        ),
        "alpha_stability": str(stability.get("status") or "BLOCKED"),
        "factor_lineage": str((factor_lineage or {}).get("status") or "BLOCKED"),
        "factor_effectiveness": str(
            (factor_effectiveness or {}).get("status") or "BLOCKED"
        ),
        "regime_conditional_attribution": str(
            (regime_attribution or {}).get("status") or "BLOCKED"
        ),
    }
    blockers = [name for name, status in components.items() if status != "PASS"]
    return {
        "schema_version": (
            "alpha_v3_2_guard_v1"
            if str(evidence_version or "").startswith("alpha_v3_2")
            else "alpha_v3_5_guard_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "components": components,
        "blockers": blockers,
        "factor_returns_availability": factor_returns_availability,
        "factor_panel_availability": factor_panel_availability,
        "residual_audit": {
            key: attribution.get(key)
            for key in (
                "residual_mean",
                "residual_std",
                "residual_tstat",
                "unexplained_residual_return",
                "residual_cumulative_return",
                "unexplained_variance_ratio",
                "max_unexplained_variance_ratio",
            )
        },
        "alpha_stability": stability,
        "factor_lineage": factor_lineage or {},
        "factor_effectiveness": factor_effectiveness or {},
        "regime_conditional_attribution": regime_attribution or {},
        "capital_authorized": False,
    }


def build_alpha_proof_summary(
    benchmark: dict[str, Any],
    attribution: dict[str, Any],
    factor_ic: dict[str, Any],
    guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    benchmark_economic_status = str(
        benchmark.get("economic_status", benchmark.get("status") or "BLOCKED")
    )
    components = {
        "benchmark_excess": (
            "PASS"
            if benchmark.get("status") == "PASS"
            and benchmark_economic_status == "PASS"
            else "BLOCKED"
        ),
        "alpha_attribution": str(attribution.get("status") or "BLOCKED"),
        "factor_ic": str(factor_ic.get("status") or "BLOCKED"),
        "alpha_proof_guard": str((guard or {}).get("status") or "BLOCKED"),
    }
    blockers = [name for name, status in components.items() if status != "PASS"]
    return {
        "schema_version": (
            "alpha_v3_2_proof_summary_v1"
            if str((guard or {}).get("schema_version") or "").startswith("alpha_v3_2")
            else "alpha_v3_5_proof_summary_v1"
        ),
        "status": "PASS" if not blockers else "BLOCKED",
        "components": components,
        "blockers": blockers,
        "capital_authorized": False,
    }
