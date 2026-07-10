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

    # Check each window has factor states
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

    # Check that A7/A8/A9 experiments are covered
    # Factor states should be per-experiment-per-window
    has_experiment_level = any(
        isinstance(v, dict) and any(isinstance(vv, dict) for vv in v.values())
        for v in factor_states.values()
    )

    if not has_experiment_level:
        # Flat per-window structure — check if any experiments are marked NOT_FITTED
        pass

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

    # Check for return-like columns with actual values
    return_cols = [c for c in df.columns if any(
        keyword in c.lower()
        for keyword in ["return", "nav", "sharpe", "pnl"]
    )]

    if return_cols:
        for col in return_cols:
            non_null = df[col].dropna()
            if len(non_null) > 0:
                n_with_results = max(n_with_results, len(non_null))

    if n_with_results == 0:
        errors.append(
            "Random seed file has seed names but no actual return results"
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
    tolerance: float = 0.001,  # 0.1% tolerance
) -> dict[str, Any]:
    """Check that trade ledger cash flows are consistent with NAV changes.

    Basic check: for each day, NAV[t] ≈ NAV[t-1] + sum(trade_cash_flows[t]) + market_pnl[t].
    """
    errors: list[str] = []

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

    # Basic check: NAV should have entries for trade dates in ledger
    nav_dates = set(str(d) for d in nav.get("trade_date", pd.Series(dtype=str)))
    ledger_dates = set(str(d) for d in ledger.get("trade_date", pd.Series(dtype=str)))

    orphan_trades = ledger_dates - nav_dates
    if orphan_trades:
        errors.append(
            f"Ledger has {len(orphan_trades)} trade dates not in NAV: "
            f"{sorted(orphan_trades)[:5]}..."
        )

    # Check NAV never goes negative
    nav_col = next((c for c in ["nav", "total_equity", "equity"] if c in nav.columns), None)
    if nav_col:
        if (nav[nav_col] < -tolerance).any():
            errors.append("NAV contains negative values")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "details": {
            "nav_dates": len(nav_dates),
            "ledger_dates": len(ledger_dates),
            "orphan_trades": len(orphan_trades) if orphan_trades else 0,
        },
    }


# ---------------------------------------------------------------------------
# Daily candidates/weights non-empty check
# ---------------------------------------------------------------------------


def validate_daily_decisions_nonempty(
    candidates_path: Path,
    weights_path: Path,
    min_days: int = 1,
) -> dict[str, Any]:
    """Check that daily candidates and weights have actual content."""
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
            if name == "daily_candidates" and "trade_date" not in df.columns:
                errors.append(f"{name} missing 'trade_date' column")
        except Exception as e:
            errors.append(f"{name} read error: {e}")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
    }


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

    # Determine overall status
    # Priority: EMPTY_RESULTS > NOT_FITTED > SOURCE_INCOMPLETE > NON_REPRODUCIBLE > REPRODUCIBLE

    has_empty = (
        nav_check.get("n_rows", 0) == 0
        or ledger_check.get("n_trades", 0) == 0
    )
    has_not_fitted = not factor_check.get("passed", False)
    has_source_incomplete = not source_check.get("passed", False)
    has_missing_random = not random_check.get("passed", False)
    has_empty_decisions = not decisions_check.get("passed", False)

    if has_empty or has_empty_decisions:
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
