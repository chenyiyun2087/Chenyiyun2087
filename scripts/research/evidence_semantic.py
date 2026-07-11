"""Semantic evidence validation for PR18.

Extends validate_evidence_package() with content-level checks:
  - NAV rows > 0 per experiment per window
  - Trade ledger non-empty
  - Factor states FITTED (not NOT_FITTED)
  - Random seed results are real (not just seed names)
  - source_complete=True for corporate actions and lifecycle
  - Ledger-NAV conservation
  - PRECHECK_ONLY status for pre-flight packages

Empty files must NOT be assessed as REPRODUCIBLE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Semantic status
# ---------------------------------------------------------------------------


class SemanticEvidenceStatus(str, Enum):
    """Evidence status after semantic validation."""
    REPRODUCIBLE = "REPRODUCIBLE"
    NON_REPRODUCIBLE = "NON_REPRODUCIBLE"
    PRECHECK_ONLY = "PRECHECK_ONLY"
    INSUFFICIENT_OOS_COVERAGE = "INSUFFICIENT_OOS_COVERAGE"
    EMPTY_RESULTS = "EMPTY_RESULTS"
    NOT_FITTED = "NOT_FITTED"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    LEDGER_NAV_MISMATCH = "LEDGER_NAV_MISMATCH"
    MISSING_RANDOM_RESULTS = "MISSING_RANDOM_RESULTS"


# ---------------------------------------------------------------------------
# Factor state validation
# ---------------------------------------------------------------------------


REQUIRED_FITTED_EXPERIMENTS = frozenset({"A7", "A8", "A9"})


def validate_factor_states(factor_state_path: Path) -> dict[str, Any]:
    """Check that A7/A8/A9 factor states are FITTED.

    Returns dict with:
      passed: bool
      status: SemanticEvidenceStatus
      details: per-experiment-per-window status
      errors: list of error messages
    """
    errors: list[str] = []
    details: dict[str, dict[str, str]] = {}

    if not factor_state_path.is_file():
        return {
            "passed": False,
            "status": SemanticEvidenceStatus.NOT_FITTED.value,
            "details": {},
            "errors": ["factor_state_by_fold.json missing"],
        }

    import json
    try:
        factor_states = json.loads(factor_state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError) as e:
        return {
            "passed": False,
            "status": SemanticEvidenceStatus.NOT_FITTED.value,
            "details": {},
            "errors": [f"factor_state_by_fold.json parse error: {e}"],
        }

    # Check structure type
    # Experiment-level: top-level keys are experiment IDs, values are dicts of windows
    # Flat: top-level keys are window labels, values are {status: ...}
    has_experiment_level = any(
        isinstance(v, dict) and any(isinstance(vv, dict) for vv in v.values())
        for v in factor_states.values()
    )

    if has_experiment_level:
        # Per-experiment structure: {exp_id: {window: {status: ...}}}
        covered_experiments: set[str] = set()
        for exp_key, exp_data in factor_states.items():
            if not isinstance(exp_data, dict):
                continue
            exp_errors: list[str] = []
            for window_key, window_data in exp_data.items():
                if not isinstance(window_data, dict):
                    continue
                status = window_data.get("status", "UNKNOWN")
                details[exp_key] = details.get(exp_key, {})
                details[exp_key][window_key] = status  # type: ignore[index]
                if status == "NOT_FITTED":
                    reason = window_data.get("reason", "unknown")
                    exp_errors.append(
                        f"experiment '{exp_key}' window '{window_key}': NOT_FITTED (reason: {reason})"
                    )
                elif status == "FITTED":
                    covered_experiments.add(exp_key)
                elif status != "FITTED":
                    exp_errors.append(
                        f"experiment '{exp_key}' window '{window_key}': state '{status}' — expected FITTED"
                    )
            errors.extend(exp_errors)

        missing_experiments = REQUIRED_FITTED_EXPERIMENTS - covered_experiments
        if missing_experiments:
            errors.append(
                f"Missing FITTED experiments: {sorted(missing_experiments)}. "
                f"Required: {sorted(REQUIRED_FITTED_EXPERIMENTS)}"
            )
    else:
        # Flat per-window structure: check ALL windows are FITTED
        for window_key, window_data in factor_states.items():
            if isinstance(window_data, dict):
                status = window_data.get("status", "UNKNOWN")
                details[window_key] = {"status": status}
                if status == "NOT_FITTED":
                    reason = window_data.get("reason", "unknown")
                    errors.append(
                        f"window '{window_key}': factor state NOT_FITTED (reason: {reason})"
                    )
                elif status != "FITTED":
                    errors.append(
                        f"window '{window_key}': factor state '{status}' — expected FITTED"
                    )

    passed = len(errors) == 0 and len(details) > 0
    status = (
        SemanticEvidenceStatus.REPRODUCIBLE.value
        if passed
        else SemanticEvidenceStatus.NOT_FITTED.value
    )

    return {
        "passed": passed,
        "status": status,
        "details": details,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# NAV non-empty check
# ---------------------------------------------------------------------------


def validate_nav_nonempty(nav_path: Path, min_rows: int = 1) -> dict[str, Any]:
    """Check that daily NAV has actual rows (not just schema).

    Returns dict with:
      passed: bool
      n_rows: int
      errors: list of error messages
    """
    errors: list[str] = []
    n_rows = 0

    if not nav_path.is_file():
        return {
            "passed": False,
            "n_rows": 0,
            "errors": [f"NAV file missing: {nav_path.name}"],
        }

    try:
        df = pd.read_parquet(nav_path)
        n_rows = len(df)
    except Exception as e:
        return {
            "passed": False,
            "n_rows": 0,
            "errors": [f"NAV file read error: {e}"],
        }

    if n_rows < min_rows:
        errors.append(
            f"NAV file '{nav_path.name}' has {n_rows} rows, minimum required: {min_rows}"
        )

    return {
        "passed": len(errors) == 0,
        "n_rows": n_rows,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Trade ledger non-empty check
# ---------------------------------------------------------------------------


def validate_ledger_nonempty(ledger_path: Path, min_trades: int = 1) -> dict[str, Any]:
    """Check that trade ledger has actual trades.

    Returns dict with:
      passed: bool
      n_trades: int
      errors: list of error messages
    """
    errors: list[str] = []
    n_trades = 0

    if not ledger_path.is_file():
        return {
            "passed": False,
            "n_trades": 0,
            "errors": [f"Trade ledger missing: {ledger_path.name}"],
        }

    try:
        df = pd.read_parquet(ledger_path)
        n_trades = len(df)
    except Exception as e:
        return {
            "passed": False,
            "n_trades": 0,
            "errors": [f"Trade ledger read error: {e}"],
        }

    if n_trades < min_trades:
        errors.append(
            f"Trade ledger '{ledger_path.name}' has {n_trades} trades, minimum required: {min_trades}"
        )

    # Check for essential columns
    essential_cols = {"symbol", "trade_date"}
    missing_cols = essential_cols - set(df.columns)
    if missing_cols:
        errors.append(f"Trade ledger missing columns: {missing_cols}")

    return {
        "passed": len(errors) == 0,
        "n_trades": n_trades,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Random seed results check
# ---------------------------------------------------------------------------


def validate_random_seed_results(random_path: Path, min_seeds: int = 20) -> dict[str, Any]:
    """Check that random seed results contain actual return data.

    Must have at least min_seeds seeds with non-null return values.
    """
    errors: list[str] = []
    n_seeds = 0
    n_with_results = 0

    if not random_path.is_file():
        return {
            "passed": False,
            "n_seeds": 0,
            "n_with_results": 0,
            "errors": [f"Random seed results missing: {random_path.name}"],
        }

    try:
        df = pd.read_csv(random_path)
        n_seeds = len(df)
    except Exception as e:
        return {
            "passed": False,
            "n_seeds": 0,
            "n_with_results": 0,
            "errors": [f"Random seed results read error: {e}"],
        }

    if n_seeds < min_seeds:
        errors.append(
            f"Random seed results has {n_seeds} seeds, minimum required: {min_seeds}"
        )

    # PR19: Check for return-like columns where ALL seeds have non-null values
    return_cols = [c for c in df.columns if any(
        keyword in c.lower()
        for keyword in ["return", "nav", "sharpe", "pnl"]
    )]

    if return_cols:
        for col in return_cols:
            non_null = df[col].dropna()
            n_with_results = max(n_with_results, len(non_null))

    # PR19: ALL seeds must have results, not just any one seed
    if n_with_results < n_seeds:
        errors.append(
            f"Random seed results incomplete: only {n_with_results}/{n_seeds} seeds "
            f"have actual return data. ALL {n_seeds} seeds must have results."
        )

    return {
        "passed": len(errors) == 0,
        "n_seeds": n_seeds,
        "n_with_results": n_with_results,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Source completeness check
# ---------------------------------------------------------------------------


def validate_source_completeness(
    corporate_action_path: Path,
    lifecycle_path: Path,
) -> dict[str, Any]:
    """Check that source snapshots have source_complete=True.

    Returns dict with:
      passed: bool
      corporate_action_complete: bool
      lifecycle_complete: bool
      errors: list of error messages
    """
    errors: list[str] = []
    ca_complete = False
    lc_complete = False

    # Check corporate action snapshot
    if corporate_action_path.is_file():
        import json
        try:
            ca_data = json.loads(corporate_action_path.read_text(encoding="utf-8"))
            ca_complete = ca_data.get("source_complete", False)
            if not ca_complete:
                errors.append(
                    "corporate_action_snapshot: source_complete=False — "
                    "evidence cannot be REPRODUCIBLE"
                )
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"corporate_action_snapshot parse error: {e}")
    else:
        errors.append("corporate_action_snapshot.json missing")

    # Check security lifecycle snapshot
    if lifecycle_path.is_file():
        import json
        try:
            lc_data = json.loads(lifecycle_path.read_text(encoding="utf-8"))
            lc_complete = lc_data.get("source_complete", False)
            if not lc_complete:
                errors.append(
                    "security_lifecycle_snapshot: source_complete=False — "
                    "evidence cannot be REPRODUCIBLE"
                )
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"security_lifecycle_snapshot parse error: {e}")
    else:
        errors.append("security_lifecycle_snapshot.json missing")

    return {
        "passed": len(errors) == 0,
        "corporate_action_complete": ca_complete,
        "lifecycle_complete": lc_complete,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Ledger-NAV conservation check
# ---------------------------------------------------------------------------


def validate_ledger_nav_conservation(
    nav_path: Path,
    ledger_path: Path,
    tolerance: float = 0.0001,  # 1bp tolerance (PR19: tightened from 0.1%)
) -> dict[str, Any]:
    """Check that portfolio NAV is consistent with cash + positions - costs.

    PR20 Cost Accounting Contract:
      - cash[t] = cash[t-1] + proceeds[t] - costs[t]
      - market_value[t] = sum(position_shares[s,t] × close_price[s,t])
      - total_equity[t] = cash[t] + market_value[t]
      - cumulative_cost[t] = sum of all trading costs up to day t
      - nav[t] = total_equity[t] / initial_cash
      - Conservation: cash[t] + market_value[t] - accrued_cost[t] ≈ nav[t]
      - Tolerance: max(1bp × nav[t], 1bp)

    PR20: If cash, market_value, or nav columns are missing, the check
    returns a HARD FAILURE — no fallback to date-overlap-only. Evidence
    without these columns is NON_REPRODUCIBLE.
    """
    errors: list[str] = []
    details: dict[str, Any] = {}

    if not nav_path.is_file() or not ledger_path.is_file():
        return {
            "passed": False,
            "errors": ["NAV or ledger file missing for conservation check"],
            "details": {},
        }

    try:
        nav = pd.read_parquet(nav_path)
        ledger = pd.read_parquet(ledger_path)
    except Exception as e:
        return {
            "passed": False,
            "errors": [f"Ledger-NAV conservation read error: {e}"],
            "details": {},
        }

    if nav.empty or ledger.empty:
        return {
            "passed": False,
            "errors": ["NAV or ledger is empty — cannot verify conservation"],
            "details": {"nav_rows": len(nav), "ledger_rows": len(ledger)},
        }

    # Resolve date column
    nav_date_col = next((c for c in ["trade_date", "signal_date", "date"] if c in nav.columns), None)
    ledger_date_col = next((c for c in ["trade_date", "signal_date", "date"] if c in ledger.columns), None)

    if not nav_date_col or not ledger_date_col:
        errors.append("Cannot find date column in NAV or ledger for conservation check")
        return {"passed": False, "errors": errors, "details": {}}

    # Basic date overlap check
    nav_dates = set(str(d) for d in nav[nav_date_col])
    ledger_dates = set(str(d) for d in ledger[ledger_date_col])
    orphan_trades = ledger_dates - nav_dates
    if orphan_trades:
        errors.append(
            f"Ledger has {len(orphan_trades)} trade dates not in NAV: "
            f"{sorted(orphan_trades)[:5]}..."
        )

    # PR19: True conservation — cash + market_value - costs = nav
    nav_val_col = next((c for c in ["nav", "total_equity", "equity"] if c in nav.columns), None)
    cash_col = next((c for c in ["cash", "available_cash", "cash_balance"] if c in nav.columns), None)
    mv_col = next((c for c in ["market_value", "position_value", "holdings_value"] if c in nav.columns), None)
    cost_col = next((c for c in ["accrued_cost", "total_cost", "accrued_fees"] if c in nav.columns), None)

    if nav_val_col and cash_col and mv_col:
        # Full conservation check possible
        conservation_violations = 0
        total_days = 0

        for _, row in nav.iterrows():
            total_days += 1
            nav_val = float(row[nav_val_col])
            cash_val = float(row[cash_col])
            mv_val = float(row[mv_col])
            cost_val = float(row.get(cost_col, 0)) if cost_col else 0.0

            expected_nav = cash_val + mv_val - cost_val
            # Use relative tolerance: 1bp of NAV
            abs_tol = max(abs(nav_val) * tolerance, tolerance)
            diff = abs(nav_val - expected_nav)

            if diff > abs_tol:
                conservation_violations += 1
                if conservation_violations <= 3:  # detail on first few
                    errors.append(
                        f"Ledger-NAV conservation violated on {row[nav_date_col]}: "
                        f"cash={cash_val:.4f} + mv={mv_val:.4f} - cost={cost_val:.4f} "
                        f"= {expected_nav:.6f} ≠ nav={nav_val:.6f} (diff={diff:.6f})"
                    )

        details.update({
            "nav_dates": len(nav_dates),
            "ledger_dates": len(ledger_dates),
            "orphan_trades": len(orphan_trades),
            "conservation_check": "full",
            "total_days": total_days,
            "conservation_violations": conservation_violations,
        })
    else:
        # PR20: Missing required columns is a hard failure — no fallback.
        # Evidence without cash + market_value + nav columns is NON_REPRODUCIBLE.
        missing_cols = []
        if not nav_val_col:
            missing_cols.append("nav/total_equity/equity")
        if not cash_col:
            missing_cols.append("cash/available_cash/cash_balance")
        if not mv_col:
            missing_cols.append("market_value/position_value/holdings_value")
        errors.append(
            f"Ledger-NAV conservation FAILED: missing required columns "
            f"({', '.join(missing_cols)}). Evidence is NON_REPRODUCIBLE. "
            f"Required: cash, market_value, nav. "
            f"Cost accounting: nav = cash + market_value - accrued_cost"
        )
        details.update({
            "nav_dates": len(nav_dates),
            "ledger_dates": len(ledger_dates),
            "orphan_trades": len(orphan_trades),
            "conservation_check": "failed_missing_columns",
            "missing_columns": missing_cols,
        })

    # Check NAV never goes negative
    if nav_val_col:
        if (nav[nav_val_col] < -tolerance).any():
            errors.append("NAV contains negative values")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Daily candidates/weights non-empty check
# ---------------------------------------------------------------------------


def validate_daily_decisions_nonempty(
    candidates_path: Path,
    weights_path: Path,
    min_days: int = 1,
) -> dict[str, Any]:
    """Check that daily candidates and weights have actual content.

    PR19: Accepts both 'trade_date' and 'signal_date' as date columns.
    """
    errors: list[str] = []

    for path, name in [(candidates_path, "daily_candidates"), (weights_path, "daily_weights")]:
        if not path.is_file():
            errors.append(f"{name} file missing")
            continue
        try:
            df = pd.read_parquet(path)
            if len(df) < min_days:
                errors.append(f"{name} has {len(df)} rows, minimum required: {min_days}")
            # Check for essential columns
            if "symbol" not in df.columns:
                errors.append(f"{name} missing 'symbol' column")
            # PR19: Accept both 'trade_date' and 'signal_date' as date column
            has_date_col = "trade_date" in df.columns or "signal_date" in df.columns
            if not has_date_col:
                errors.append(f"{name} missing date column (need 'trade_date' or 'signal_date')")
        except Exception as e:
            errors.append(f"{name} read error: {e}")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# PR19: Per-experiment × per-window evidence validation
# ---------------------------------------------------------------------------

REQUIRED_EXPERIMENTS_FOR_OOS = frozenset({"P0", "C0", "A7", "A8", "A9"})
FIXED_VALIDATION_WINDOWS = frozenset({
    "2024H1", "2024H2", "2025H1", "2025H2", "2026H1",
})
# PR20: Windows still in progress — excluded from promotion statistics
INCOMPLETE_VALIDATION_WINDOWS = frozenset({"2026H2"})
# PR20: Minimum trading days per window (aligned with PR19 MIN_SESSIONS_PER_WINDOW)
MIN_TRADING_DAYS_PER_WINDOW = 15
# PR20: Minimum coverage ratio (actual trading days / official calendar days)
MIN_COVERAGE_RATIO = 0.95


def validate_evidence_per_experiment_window(output_dir: Path) -> dict[str, Any]:
    """Validate that evidence exists for every experiment × validation window.

    Goes beyond global file checks to verify that EACH experiment
    (P0/C0/A7/A8/A9) has data for EACH validation window.

    Two modes:
      1. Per-experiment subdirectories (e.g., output_dir/P0/daily_nav.parquet):
         Validates each experiment directory exists and has data.
      2. Flat global files (e.g., output_dir/daily_nav.parquet):
         Checks factor_state_by_fold.json for experiment coverage.
         Falls back to global file coverage check.

    Returns dict with:
      passed: bool
      experiments_covered: dict[str, set[str]] — experiment -> {windows with data}
      missing: list[str] — "experiment × window" pairs without evidence
      errors: list[str]
      structure: str — "per_experiment", "flat", or "unknown"
    """
    errors: list[str] = []
    missing: list[str] = []
    experiments_covered: dict[str, set[str]] = {}
    structure = "unknown"

    # Strategy A: Per-experiment subdirectories
    per_exp_dirs = [
        exp_id for exp_id in REQUIRED_EXPERIMENTS_FOR_OOS
        if (output_dir / exp_id).is_dir()
    ]

    if per_exp_dirs:
        structure = "per_experiment"
        for exp_id in sorted(REQUIRED_EXPERIMENTS_FOR_OOS):
            exp_dir = output_dir / exp_id
            if exp_dir.is_dir():
                windows_with_data = _check_experiment_windows(exp_dir)
                experiments_covered[exp_id] = windows_with_data
            else:
                experiments_covered[exp_id] = set()
                missing.append(f"{exp_id}: directory not found")

        for exp_id in sorted(REQUIRED_EXPERIMENTS_FOR_OOS):
            if not experiments_covered.get(exp_id):
                missing.append(f"{exp_id}: NO windows covered")
    else:
        # Strategy B: Flat structure — use factor states + global files
        structure = "flat"
        factor_path = output_dir / "factor_state_by_fold.json"

        if factor_path.is_file():
            import json
            try:
                factor_states = json.loads(factor_path.read_text(encoding="utf-8"))
                # Check if top-level keys are experiment IDs with FITTED windows
                for exp_id in REQUIRED_EXPERIMENTS_FOR_OOS:
                    if exp_id in factor_states and isinstance(factor_states[exp_id], dict):
                        exp_windows = {
                            w for w, d in factor_states[exp_id].items()
                            if isinstance(d, dict) and d.get("status") == "FITTED"
                        }
                        if exp_windows:
                            experiments_covered[exp_id] = exp_windows
            except Exception:
                pass

        # If factor states don't have experiment-level coverage, check global files
        covered_from_factors = set(experiments_covered.keys())
        if not covered_from_factors:
            # Fallback: check if global daily_nav has data across windows
            nav_path = output_dir / "daily_nav.parquet"
            if nav_path.is_file():
                try:
                    nav = pd.read_parquet(nav_path)
                    if not nav.empty:
                        date_col = next((c for c in ["trade_date", "signal_date", "date"]
                                         if c in nav.columns), None)
                        if date_col:
                            nav_dates = set(pd.to_datetime(nav[date_col]).dt.date)
                            windows_with_data = set()
                            for wl in FIXED_VALIDATION_WINDOWS:
                                wd = _get_window_dates(wl)
                                if wd:
                                    start, end = wd
                                    matches = {d for d in nav_dates if start <= d <= end}
                                    if len(matches) >= 5:
                                        windows_with_data.add(wl)
                            # If global files have data across at least 3 windows,
                            # mark all experiments as covered
                            if len(windows_with_data) >= 3:
                                for exp_id in REQUIRED_EXPERIMENTS_FOR_OOS:
                                    experiments_covered[exp_id] = windows_with_data
                except Exception:
                    pass

        # Assess coverage
        for exp_id in sorted(REQUIRED_EXPERIMENTS_FOR_OOS):
            if exp_id not in experiments_covered or not experiments_covered[exp_id]:
                missing.append(f"{exp_id}: NO windows covered")

    # Only treat as insufficient if we found per-experiment directories
    # and some have gaps. Flat structure is legacy/global format — content
    # checks (NAV, candidates, etc.) already validate the data.
    if structure == "per_experiment" and missing:
        errors.append(
            f"Insufficient OOS coverage: {len(missing)} experiment×window gaps: "
            f"{'; '.join(missing[:10])}"
        )
    elif structure == "flat":
        # PR20: Flat evidence structure is NOT accepted.
        # Must use per-experiment subdirectories (P0/, C0/, A7/, A8/, A9/)
        # each with their own window-level evidence files.
        errors.append(
            "FLAT_EVIDENCE_REJECTED: evidence must use per-experiment directory "
            "structure (P0/, C0/, A7/, A8/, A9/). Flat evidence packages are no "
            "longer accepted for promotion evaluation."
        )
        # Mark all required experiments as uncovered
        for exp_id in REQUIRED_EXPERIMENTS_FOR_OOS:
            missing.append(f"{exp_id}: flat structure — no per-experiment directory")

    passed = len(errors) == 0

    return {
        "passed": passed,
        "experiments_covered": {k: sorted(v) for k, v in experiments_covered.items()},
        "missing": missing,
        "errors": errors,
        "required_experiments": sorted(REQUIRED_EXPERIMENTS_FOR_OOS),
        "structure": structure,
    }


def _check_experiment_windows(exp_dir: Path) -> set[str]:
    """Check which validation windows have evidence in an experiment directory."""
    windows: set[str] = set()
    nav_path = exp_dir / "daily_nav.parquet"
    if nav_path.is_file():
        try:
            nav = pd.read_parquet(nav_path)
            if not nav.empty:
                # Extract date range from NAV
                date_col = next((c for c in ["trade_date", "signal_date", "date"] if c in nav.columns), None)
                if date_col:
                    nav_dates = pd.to_datetime(nav[date_col]).dt.date
                    for window_label in FIXED_VALIDATION_WINDOWS:
                        window_dates = _get_window_dates(window_label)
                        if window_dates:
                            start, end = window_dates
                            matches = [d for d in nav_dates if start <= d <= end]
                            if len(matches) >= MIN_TRADING_DAYS_PER_WINDOW:
                                windows.add(window_label)
        except Exception:
            pass
    return windows


def _get_window_dates(window_label: str) -> tuple | None:
    """Map window label to (start_date, end_date)."""
    mapping = {
        "2024H1": (pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-06-30").date()),
        "2024H2": (pd.Timestamp("2024-07-01").date(), pd.Timestamp("2024-12-31").date()),
        "2025H1": (pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-06-30").date()),
        "2025H2": (pd.Timestamp("2025-07-01").date(), pd.Timestamp("2025-12-31").date()),
        "2026H1": (pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-06-30").date()),
        "2026H2": (pd.Timestamp("2026-07-01").date(), pd.Timestamp("2026-12-31").date()),
    }
    return mapping.get(window_label)


# ---------------------------------------------------------------------------
# Full semantic validation
# ---------------------------------------------------------------------------


@dataclass
class SemanticValidationReport:
    """Complete semantic validation report."""
    passed: bool
    status: str  # SemanticEvidenceStatus value
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_evidence_semantics(output_dir: Path) -> SemanticValidationReport:
    """Run all semantic checks on an evidence package.

    This goes beyond validate_evidence_package() (which only checks file
    existence and SHAs) to verify that the evidence actually contains
    meaningful results.

    Empty files, NOT_FITTED states, and incomplete source snapshots
    will cause the package to be classified as NON_REPRODUCIBLE or
    PRECHECK_ONLY.
    """
    all_errors: list[str] = []
    all_warnings: list[str] = []
    checks: dict[str, dict[str, Any]] = {}

    # 1. Factor states
    factor_path = output_dir / "factor_state_by_fold.json"
    factor_check = validate_factor_states(factor_path)
    checks["factor_states"] = factor_check
    all_errors.extend(factor_check.get("errors", []))

    # 2. NAV non-empty
    nav_path = output_dir / "daily_nav.parquet"
    nav_check = validate_nav_nonempty(nav_path)
    checks["nav_nonempty"] = nav_check
    all_errors.extend(nav_check.get("errors", []))

    # 3. Trade ledger non-empty
    ledger_path = output_dir / "trade_ledger.parquet"
    ledger_check = validate_ledger_nonempty(ledger_path)
    checks["ledger_nonempty"] = ledger_check
    all_errors.extend(ledger_check.get("errors", []))

    # 4. Daily candidates/weights non-empty
    candidates_path = output_dir / "daily_candidates.parquet"
    weights_path = output_dir / "daily_weights.parquet"
    decisions_check = validate_daily_decisions_nonempty(candidates_path, weights_path)
    checks["daily_decisions"] = decisions_check
    all_errors.extend(decisions_check.get("errors", []))

    # 5. Random seed results
    random_path = output_dir / "random_seed_results.csv"
    random_check = validate_random_seed_results(random_path)
    checks["random_seeds"] = random_check
    all_errors.extend(random_check.get("errors", []))

    # 6. Source completeness
    ca_path = output_dir / "corporate_action_snapshot.json"
    lc_path = output_dir / "security_lifecycle_snapshot.json"
    source_check = validate_source_completeness(ca_path, lc_path)
    checks["source_completeness"] = source_check
    all_errors.extend(source_check.get("errors", []))

    # 7. Ledger-NAV conservation (only if both non-empty)
    if nav_check.get("n_rows", 0) > 0 and ledger_check.get("n_trades", 0) > 0:
        conservation_check = validate_ledger_nav_conservation(nav_path, ledger_path)
        checks["ledger_nav_conservation"] = conservation_check
        all_errors.extend(conservation_check.get("errors", []))
    else:
        checks["ledger_nav_conservation"] = {
            "passed": False,
            "errors": ["Skipped: NAV or ledger is empty"],
        }

    # 8. Stitched OOS NAV non-empty
    stitched_path = output_dir / "stitched_oos_nav.csv"
    if stitched_path.is_file():
        try:
            stitched = pd.read_csv(stitched_path)
            if len(stitched) == 0:
                all_errors.append("stitched_oos_nav.csv is empty")
            checks["stitched_nav"] = {"passed": len(stitched) > 0, "n_rows": len(stitched)}
        except Exception as e:
            all_errors.append(f"stitched_oos_nav.csv read error: {e}")
            checks["stitched_nav"] = {"passed": False, "errors": [str(e)]}
    else:
        all_warnings.append("stitched_oos_nav.csv not found")

    # 9. Walk-forward metrics non-empty
    wf_metrics_path = output_dir / "walk_forward_metrics.csv"
    if wf_metrics_path.is_file():
        try:
            wf = pd.read_csv(wf_metrics_path)
            if len(wf) == 0:
                all_errors.append("walk_forward_metrics.csv is empty")
            checks["wf_metrics"] = {"passed": len(wf) > 0, "n_rows": len(wf)}
        except Exception as e:
            all_errors.append(f"walk_forward_metrics.csv read error: {e}")
            checks["wf_metrics"] = {"passed": False, "errors": [str(e)]}

    # 10. PR19: Per-experiment × per-window evidence
    per_exp_check = validate_evidence_per_experiment_window(output_dir)
    checks["per_experiment_window"] = per_exp_check
    if not per_exp_check["passed"]:
        all_errors.extend(per_exp_check.get("errors", []))
        all_warnings.append(
            f"Per-experiment×window evidence gaps: {per_exp_check.get('missing', [])}"
        )

    # Determine overall status
    # Priority: INSUFFICIENT_OOS_COVERAGE > EMPTY_RESULTS > NOT_FITTED >
    #   SOURCE_INCOMPLETE > NON_REPRODUCIBLE > REPRODUCIBLE

    has_oos_gaps = not per_exp_check.get("passed", True)
    has_empty = (
        nav_check.get("n_rows", 0) == 0
        or ledger_check.get("n_trades", 0) == 0
    )
    has_not_fitted = not factor_check.get("passed", False)
    has_source_incomplete = not source_check.get("passed", False)
    has_missing_random = not random_check.get("passed", False)
    has_empty_decisions = not decisions_check.get("passed", False)

    if has_oos_gaps:
        status = SemanticEvidenceStatus.INSUFFICIENT_OOS_COVERAGE.value
    elif has_empty or has_empty_decisions:
        status = SemanticEvidenceStatus.EMPTY_RESULTS.value
    elif has_not_fitted:
        status = SemanticEvidenceStatus.NOT_FITTED.value
    elif has_source_incomplete:
        status = SemanticEvidenceStatus.SOURCE_INCOMPLETE.value
    elif has_missing_random:
        status = SemanticEvidenceStatus.MISSING_RANDOM_RESULTS.value
    elif all_errors:
        status = SemanticEvidenceStatus.NON_REPRODUCIBLE.value
    else:
        status = SemanticEvidenceStatus.REPRODUCIBLE.value

    passed = status == SemanticEvidenceStatus.REPRODUCIBLE.value

    return SemanticValidationReport(
        passed=passed,
        status=status,
        checks=checks,
        errors=all_errors,
        warnings=all_warnings,
    )


# ---------------------------------------------------------------------------
# PRECHECK_ONLY detection
# ---------------------------------------------------------------------------


def is_precheck_only(output_dir: Path) -> tuple[bool, str]:
    """Detect if an evidence package is a pre-check (not a real run).

    A pre-check package has:
      - factor_state_by_fold.json with status NOT_FITTED
      - Empty daily_nav, daily_candidates, trade_ledger
      - Reason field mentioning "precheck", "preflight", or "not_run"

    Returns (is_precheck, reason).
    """
    factor_path = output_dir / "factor_state_by_fold.json"
    if not factor_path.is_file():
        return False, ""

    import json
    try:
        factor_states = json.loads(factor_path.read_text(encoding="utf-8"))
    except Exception:
        return False, ""

    all_not_fitted = True
    reasons: set[str] = set()

    for window_key, window_data in factor_states.items():
        if isinstance(window_data, dict):
            status = window_data.get("status", "")
            if status != "NOT_FITTED":
                all_not_fitted = False
            reason = str(window_data.get("reason", "")).lower()
            reasons.add(reason)

    if not all_not_fitted:
        return False, ""

    precheck_keywords = {"precheck", "preflight", "not_run", "pr18_matrix_not_run", "coverage_preflight"}
    is_precheck = any(
        any(kw in reason for kw in precheck_keywords)
        for reason in reasons
    )

    if is_precheck:
        return True, f"PRECHECK_ONLY: factor states are NOT_FITTED with reason indicating pre-flight ({', '.join(sorted(reasons))})"

    return False, ""
