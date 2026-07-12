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
    tolerance: float = 0.0001,  # 1bp tolerance for dimensionless nav
    yuan_tolerance: float = 1.0,  # 1 yuan tolerance for equity conservation
) -> dict[str, Any]:
    """Check that portfolio NAV is consistent with cash + positions.

    PR26A.8 Cost Accounting Contract (fixed from PR20):
      - cash[t] = cash[t-1] + proceeds[t] - costs[t]
      - market_value[t] = sum(position_shares[s,t] × close_price[s,t])
      - total_equity[t] = cash[t] + market_value[t]            (YUAN)
      - nav[t] = total_equity[t] / initial_cash                 (DIMENSIONLESS)

    Conservation checks:
      1. total_equity[t] ≈ cash[t] + market_value[t]            (≤ 1 yuan)
      2. nav[t] ≈ total_equity[t] / initial_cash                (≤ 1 bp)

    Costs are already deducted from cash at execution time.  Do NOT
    subtract them again — that double-counts.

    Auto-detects units: if nav ≈ 1.0 it's dimensionless; if nav > 100
    it's raw yuan equity (backward compat).  initial_cash is inferred
    from the first row when available.
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

    # PR26A.8: Correct conservation — equity in yuan, nav dimensionless
    # Resolve columns
    cash_col = next((c for c in ["cash", "available_cash", "cash_balance"] if c in nav.columns), None)
    mv_col = next((c for c in ["market_value", "position_value", "holdings_value"] if c in nav.columns), None)
    equity_col = next((c for c in ["total_equity", "equity"] if c in nav.columns), None)
    nav_val_col = next((c for c in ["nav"] if c in nav.columns), None)

    if not (cash_col and mv_col and nav_val_col):
        missing_cols = []
        if not cash_col: missing_cols.append("cash")
        if not mv_col: missing_cols.append("market_value")
        if not nav_val_col: missing_cols.append("nav")
        errors.append(
            f"Ledger-NAV conservation FAILED: missing required columns "
            f"({', '.join(missing_cols)}). Evidence is NON_REPRODUCIBLE."
        )
        details.update({
            "nav_dates": len(nav_dates), "ledger_dates": len(ledger_dates),
            "orphan_trades": len(orphan_trades),
            "conservation_check": "failed_missing_columns",
            "missing_columns": missing_cols,
        })
        return {"passed": False, "errors": errors, "details": details}

    # Auto-detect units: if nav ≈ 1.0 it's dimensionless; if > 100 it's yuan
    first_nav = float(nav[nav_val_col].iloc[0])
    is_dimensionless = abs(first_nav) < 100

    # Infer initial_cash when nav is dimensionless
    initial_cash = None
    if is_dimensionless and equity_col and equity_col in nav.columns:
        first_equity = float(nav[equity_col].iloc[0])
        if first_nav > 1e-6:
            initial_cash = first_equity / first_nav
    elif is_dimensionless and cash_col and mv_col:
        first_cash = float(nav[cash_col].iloc[0])
        first_mv = float(nav[mv_col].iloc[0])
        first_equity = first_cash + first_mv
        if first_nav > 1e-6:
            initial_cash = first_equity / first_nav
    if initial_cash is None:
        initial_cash = 500_000.0  # default

    # Run conservation checks
    eq_violations = 0
    nav_violations = 0
    total_days = 0

    for _, row in nav.iterrows():
        total_days += 1
        cash_val = float(row[cash_col])
        mv_val = float(row[mv_col])

        # Check 1: total_equity ≈ cash + market_value (yuan)
        if equity_col and equity_col in nav.columns:
            reported_equity = float(row[equity_col])
        else:
            reported_equity = cash_val + mv_val  # compute if not stored
        computed_equity = cash_val + mv_val
        eq_diff = abs(reported_equity - computed_equity)
        if eq_diff > yuan_tolerance:
            eq_violations += 1
            if eq_violations <= 3:
                errors.append(
                    f"Equity conservation violated on {row[nav_date_col]}: "
                    f"cash({cash_val:.2f}) + mv({mv_val:.2f}) = "
                    f"{computed_equity:.2f} ≠ reported_equity({reported_equity:.2f}) "
                    f"(diff={eq_diff:.2f} yuan)"
                )

        # Check 2: nav ≈ total_equity / initial_cash (dimensionless)
        nav_val = float(row[nav_val_col])
        computed_nav = reported_equity / initial_cash
        nav_diff = abs(nav_val - computed_nav)
        abs_tol = max(abs(nav_val) * tolerance, tolerance)
        if nav_diff > abs_tol:
            nav_violations += 1
            if nav_violations <= 3:
                errors.append(
                    f"NAV conservation violated on {row[nav_date_col]}: "
                    f"equity({reported_equity:.2f}) / initial_cash({initial_cash:.0f}) "
                    f"= {computed_nav:.6f} ≠ nav({nav_val:.6f}) (diff={nav_diff:.8f})"
                )

    details.update({
        "nav_dates": len(nav_dates),
        "ledger_dates": len(ledger_dates),
        "orphan_trades": len(orphan_trades),
        "conservation_check": "v2_yuan_normalized",
        "total_days": total_days,
        "equity_violations": eq_violations,
        "nav_violations": nav_violations,
        "initial_cash": initial_cash,
        "is_dimensionless_nav": is_dimensionless,
    })

    # Check NAV never goes negative
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
    "2024Q1", "2024Q2", "2024Q3", "2024Q4",
    "2025Q1", "2025Q2", "2025Q3", "2025Q4",
    "2026Q1", "2026Q2",
})
# PR20: Windows still in progress — excluded from promotion statistics
INCOMPLETE_VALIDATION_WINDOWS = frozenset({"2026Q3", "2026Q4"})
# PR26A.8: All strategies that must cover all 10 quarters.
# RND strategies have a separate seed-count check (≥ 95 per quarter).
REQUIRED_EXPERIMENTS_FOR_OOS = frozenset({
    "P0", "C0", "A7", "A8", "A9", "REV_A7",
})
RND_EXPERIMENTS = frozenset({"RND_TOP30", "RND_FULL"})
CARTESIAN_WINDOW_COUNT = 10  # 2024Q1–2026Q2
# PR26A.9: Full quarter coverage — a typical quarter has ~55-65 trading days.
# 15 days is insufficient to represent a complete quarter.
MIN_TRADING_DAYS_PER_WINDOW = 55
MIN_COVERAGE_RATIO = 0.99  # NAV dates must cover ≥99% of calendar trading days
MIN_RND_SEEDS_PER_WINDOW = 95


def validate_evidence_per_experiment_window(output_dir: Path) -> dict[str, Any]:
    """PR26A.8: 10-quarter Cartesian gate — every experiment must cover ALL 10 quarters.

    For each experiment in REQUIRED_EXPERIMENTS_FOR_OOS:
      - Directory must exist (e.g., output_dir/P0/)
      - daily_nav.parquet must have >= MIN_TRADING_DAYS_PER_WINDOW trading days
        in EACH of the 10 FIXED_VALIDATION_WINDOWS (2024Q1-2026Q2)

    For RND experiments (RND_TOP30, RND_FULL):
      - random_seed_results.csv must have >= MIN_RND_SEEDS_PER_WINDOW seeds
      - status.json must exist with status=PASSED

    Returns dict with:
      passed: bool
      experiments_covered: dict[str, dict[str, int]]
      missing: list[str]
      errors: list[str]
    """
    errors: list[str] = []
    missing: list[str] = []
    experiments_covered: dict[str, dict[str, int]] = {}
    all_windows = set(FIXED_VALIDATION_WINDOWS)  # PR26A.9: fixed from {item[0] for item in ...}

    # --- Core strategies: Cartesian gate ---
    for exp_id in sorted(REQUIRED_EXPERIMENTS_FOR_OOS):
        exp_dir = output_dir / exp_id
        if not exp_dir.is_dir():
            missing.append(f"{exp_id}: directory not found")
            experiments_covered[exp_id] = {}
            continue

        nav_path = exp_dir / "daily_nav.parquet"
        if not nav_path.is_file():
            missing.append(f"{exp_id}: daily_nav.parquet missing")
            experiments_covered[exp_id] = {}
            continue

        try:
            nav = pd.read_parquet(nav_path)
        except Exception:
            missing.append(f"{exp_id}: daily_nav.parquet unreadable")
            experiments_covered[exp_id] = {}
            continue

        nav_date_col = next(
            (c for c in ["trade_date", "signal_date", "date"] if c in nav.columns), None
        )
        if nav_date_col is None:
            missing.append(f"{exp_id}: no date column in NAV")
            experiments_covered[exp_id] = {}
            continue

        nav_dates_list = sorted({pd.Timestamp(d).date() for d in nav[nav_date_col] if pd.notna(d)})
        nav_dates = set(nav_dates_list)
        window_days: dict[str, int] = {}
        for wl in sorted(all_windows):
            wd = _get_window_dates(wl)
            if wd:
                start, end = wd
                # PR26A.10: Compute calendar trading days from the frozen
                # SSE calendar (calendar_snapshot.json), not from freq="B".
                # freq="B" counts weekends as "trading days" and misses
                # Chinese holidays like Spring Festival, National Day, etc.
                sse_trading_days = _get_sse_trading_days(output_dir, wl)
                quarter_cal_days = len(sse_trading_days)
                matches = {d for d in nav_dates if start <= d <= end}
                matches_list = sorted(matches)
                n_matches = len(matches)
                window_days[wl] = n_matches
                # Check 1: minimum trading days
                if n_matches < MIN_TRADING_DAYS_PER_WINDOW:
                    missing.append(
                        f"{exp_id}x{wl}: only {n_matches} trading days "
                        f"(need >={MIN_TRADING_DAYS_PER_WINDOW})"
                    )
                # Check 2: calendar coverage ratio
                if quarter_cal_days > 0:
                    coverage = n_matches / quarter_cal_days
                    if coverage < MIN_COVERAGE_RATIO:
                        missing.append(
                            f"{exp_id}x{wl}: coverage {coverage:.2%} "
                            f"(need >={MIN_COVERAGE_RATIO:.0%})"
                        )
                # Check 3: first NAV date must be near quarter start
                if n_matches > 0:
                    quarter_start_buffer = pd.Timestamp(start) + pd.DateOffset(days=5)
                    if matches_list[0] > quarter_start_buffer.date():
                        missing.append(
                            f"{exp_id}x{wl}: first NAV date {matches_list[0]} "
                            f"is after {quarter_start_buffer.date()} (quarter start {start})"
                        )
                    # Check 4: last NAV date must be near quarter end
                    quarter_end_buffer = pd.Timestamp(end) - pd.DateOffset(days=3)
                    if matches_list[-1] < quarter_end_buffer.date():
                        missing.append(
                            f"{exp_id}x{wl}: last NAV date {matches_list[-1]} "
                            f"is before {quarter_end_buffer.date()} (quarter end {end})"
                        )
        experiments_covered[exp_id] = window_days

    # --- RND experiments: per-quarter seed count ---
    # PR26A.9: RND must be validated per-quarter, not globally.
    # Each quarter independently requires ≥95 seeds and ≥95 distinct paths.
    # Prefer per-quarter CSV structure: RND_FULL/2024Q1/random_seed_results.csv
    # Fall back to flat CSV with window column filtering.
    for rnd_id in sorted(RND_EXPERIMENTS):
        rnd_dir = output_dir / rnd_id
        if not rnd_dir.is_dir():
            missing.append(f"{rnd_id}: directory not found")
            continue

        # PR26A.9: Try per-quarter structure first
        per_quarter_ok = True
        for wl in sorted(all_windows):
            qcsv = rnd_dir / wl / "random_seed_results.csv"
            if qcsv.is_file():
                try:
                    qdf = pd.read_csv(qcsv)
                    n_seeds = len(qdf)
                    n_paths = len(set(qdf["path_hash"])) if "path_hash" in qdf.columns else n_seeds
                    if n_seeds < MIN_RND_SEEDS_PER_WINDOW:
                        missing.append(
                            f"{rnd_id}x{wl}: only {n_seeds} seeds "
                            f"(need >={MIN_RND_SEEDS_PER_WINDOW})"
                        )
                        per_quarter_ok = False
                    if n_paths < MIN_RND_SEEDS_PER_WINDOW:
                        missing.append(
                            f"{rnd_id}x{wl}: only {n_paths} paths "
                            f"(need >={MIN_RND_SEEDS_PER_WINDOW})"
                        )
                        per_quarter_ok = False
                except Exception:
                    missing.append(f"{rnd_id}x{wl}: random_seed_results.csv unreadable")
                    per_quarter_ok = False
            else:
                missing.append(f"{rnd_id}x{wl}: random_seed_results.csv missing")
                per_quarter_ok = False

        # Fall back to flat CSV (deprecated but supported with warning)
        if not per_quarter_ok:
            csv_path = rnd_dir / "random_seed_results.csv"
            if csv_path.is_file():
                try:
                    rdf = pd.read_csv(csv_path)
                    # PR26A.9: Filter by window if column exists
                    if "window" in rdf.columns:
                        for wl in sorted(all_windows):
                            wdf = rdf[rdf["window"] == wl]
                            n_seeds = len(wdf)
                            n_paths = len(set(wdf["path_hash"])) if "path_hash" in wdf.columns else n_seeds
                            if n_seeds < MIN_RND_SEEDS_PER_WINDOW:
                                # Only report if not already captured by per-quarter check
                                gap_key = f"{rnd_id}x{wl}: flat_csv only {n_seeds} seeds"
                                if gap_key not in missing:
                                    missing.append(gap_key)
                    else:
                        # No window column — global check only (backward compat)
                        n_seeds = len(rdf)
                        n_paths = len(set(rdf["path_hash"])) if "path_hash" in rdf.columns else n_seeds
                        if n_seeds < MIN_RND_SEEDS_PER_WINDOW or n_paths < MIN_RND_SEEDS_PER_WINDOW:
                            missing.append(
                                f"{rnd_id} (flat): only {n_seeds} seeds / {n_paths} paths "
                                f"(need >={MIN_RND_SEEDS_PER_WINDOW}) — "
                                f"WARNING: flat CSV without per-quarter structure is deprecated"
                            )
                except Exception:
                    missing.append(f"{rnd_id}: random_seed_results.csv unreadable")

        # status.json check
        status_path = rnd_dir / "status.json"
        if status_path.is_file():
            try:
                import json
                status_data = json.loads(status_path.read_text(encoding="utf-8"))
                if status_data.get("status") != "PASSED":
                    missing.append(f"{rnd_id}: status.json != PASSED")
            except Exception:
                missing.append(f"{rnd_id}: status.json unreadable")
        else:
            missing.append(f"{rnd_id}: status.json missing")

    if missing:
        errors.append(
            f"10-quarter Cartesian gate FAILED: {len(missing)} gaps: "
            f"{'; '.join(missing[:15])}"
        )

    passed = len(errors) == 0

    return {
        "passed": passed,
        "experiments_covered": {
            k: dict(v) if isinstance(v, dict) else sorted(v) if isinstance(v, set) else v
            for k, v in experiments_covered.items()
        },
        "missing": missing,
        "errors": errors,
        "required_experiments": sorted(REQUIRED_EXPERIMENTS_FOR_OOS),
        "rnd_experiments": sorted(RND_EXPERIMENTS),
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
        "2024Q1": (pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-03-31").date()),
        "2024Q2": (pd.Timestamp("2024-04-01").date(), pd.Timestamp("2024-06-30").date()),
        "2024Q3": (pd.Timestamp("2024-07-01").date(), pd.Timestamp("2024-09-30").date()),
        "2024Q4": (pd.Timestamp("2024-10-01").date(), pd.Timestamp("2024-12-31").date()),
        "2025Q1": (pd.Timestamp("2025-01-01").date(), pd.Timestamp("2025-03-31").date()),
        "2025Q2": (pd.Timestamp("2025-04-01").date(), pd.Timestamp("2025-06-30").date()),
        "2025Q3": (pd.Timestamp("2025-07-01").date(), pd.Timestamp("2025-09-30").date()),
        "2025Q4": (pd.Timestamp("2025-10-01").date(), pd.Timestamp("2025-12-31").date()),
        "2026Q1": (pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-03-31").date()),
        "2026Q2": (pd.Timestamp("2026-04-01").date(), pd.Timestamp("2026-06-30").date()),
        "2026Q3": (pd.Timestamp("2026-07-01").date(), pd.Timestamp("2026-09-30").date()),
        "2026Q4": (pd.Timestamp("2026-10-01").date(), pd.Timestamp("2026-12-31").date()),
    }
    return mapping.get(window_label)


def _get_sse_trading_days(
    output_dir: Path,
    window_label: str,
) -> set:
    """PR26A.10: Get actual SSE trading days for a quarter from calendar_snapshot.json.

    Reads the frozen calendar_snapshot.json from the evidence package and
    returns the set of dates within the given quarter.  Falls back to
    pd.date_range(freq="B") when calendar_snapshot.json is not available
    (e.g., in test packages that don't include a full calendar).
    """
    calendar_path = output_dir / "calendar_snapshot.json"
    if not calendar_path.is_file():
        wd = _get_window_dates(window_label)
        if wd:
            start, end = wd
            return set(
                d.date() for d in pd.date_range(start, end, freq="B")
            )
        return set()

    import json as _json_mod
    try:
        cal_data = _json_mod.loads(calendar_path.read_text(encoding="utf-8"))
    except Exception:
        wd = _get_window_dates(window_label)
        if wd:
            start, end = wd
            return set(
                d.date() for d in pd.date_range(start, end, freq="B")
            )
        return set()

    # Extract trading dates from calendar snapshot
    trading_dates_raw = cal_data.get("trading_dates", cal_data.get("trading_days", []))
    wd = _get_window_dates(window_label)
    if not wd or not trading_dates_raw:
        if wd:
            start, end = wd
            return set(
                d.date() for d in pd.date_range(start, end, freq="B")
            )
        return set()

    start, end = wd
    return {
        pd.Timestamp(d).date()
        for d in trading_dates_raw
        if start <= pd.Timestamp(d).date() <= end
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

    # 2. NAV non-empty (P0 as primary)
    nav_path = output_dir / "P0" / "daily_nav.parquet"
    if not nav_path.is_file():
        nav_path = output_dir / "daily_nav.parquet"  # backward compat
    nav_check = validate_nav_nonempty(nav_path)
    checks["nav_nonempty"] = nav_check
    all_errors.extend(nav_check.get("errors", []))

    # 3. Trade ledger non-empty (P0 as primary)
    ledger_path = output_dir / "P0" / "trade_ledger.parquet"
    if not ledger_path.is_file():
        ledger_path = output_dir / "trade_ledger.parquet"  # backward compat
    ledger_check = validate_ledger_nonempty(ledger_path)
    checks["ledger_nonempty"] = ledger_check
    all_errors.extend(ledger_check.get("errors", []))

    # 4. Daily candidates/weights non-empty
    candidates_path = output_dir / "daily_candidates.parquet"
    weights_path = output_dir / "daily_weights.parquet"
    decisions_check = validate_daily_decisions_nonempty(candidates_path, weights_path)
    checks["daily_decisions"] = decisions_check
    all_errors.extend(decisions_check.get("errors", []))

    # 5. Random seed results (RND_TOP30 as primary)
    random_path = output_dir / "RND_TOP30" / "random_seed_results.csv"
    if not random_path.is_file():
        random_path = output_dir / "random_seed_results.csv"  # backward compat
    random_check = validate_random_seed_results(random_path)
    checks["random_seeds"] = random_check
    all_errors.extend(random_check.get("errors", []))

    # 6. Source completeness
    ca_path = output_dir / "corporate_action_snapshot.json"
    lc_path = output_dir / "security_lifecycle_snapshot.json"
    source_check = validate_source_completeness(ca_path, lc_path)
    checks["source_completeness"] = source_check
    all_errors.extend(source_check.get("errors", []))

    # 7. Ledger-NAV conservation — run per strategy directory
    conservation_passed = True
    for strat_dir_name in ["P0", "C0", "A7", "A8", "A9"]:
        strat_dir = output_dir / strat_dir_name
        s_nav = strat_dir / "daily_nav.parquet"
        s_ledger = strat_dir / "trade_ledger.parquet"
        if s_nav.is_file() and s_ledger.is_file():
            s_check = validate_ledger_nav_conservation(s_nav, s_ledger)
            checks[f"conservation_{strat_dir_name}"] = s_check
            if not s_check["passed"]:
                conservation_passed = False
                all_errors.extend(s_check.get("errors", []))
    checks["ledger_nav_conservation"] = {
        "passed": conservation_passed,
        "per_strategy": True,
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
