"""PR19: Production-grade Oracle Coverage Golden Regression.

Key changes from PR18:
  1. Fixed date ranges — no more "last N trading days"
  2. Regime coverage is a HARD GATE — part of all_passed
  3. Exact date match — n_dates_missing_oracle == 0 required
  4. Exit comparison integrated into golden output
  5. Provenance validation on oracle load
  6. DB exceptions are fail-closed (not fail-open)

Usage:
  python scripts/research/run_pr19_golden_regression.py \\
    --p0-oracle exports/frozen_oracles/P0/ \\
    --c0-oracle exports/frozen_oracles/C0/ \\
    --date-range 2024-01-01:2026-06-30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.frozen_oracle import (
    OracleComparisonResult,
    FrozenOracleState,
    compare_against_oracle,
    load_frozen_oracle_from_db,
    load_frozen_oracle_from_file,
    validate_oracle_provenance,
    check_oracle_independence,
    canonical_sha,
)


# ---------------------------------------------------------------------------
# PR19: Fixed date ranges (explicit, not "last N")
# ---------------------------------------------------------------------------

REQUIRED_DATE_RANGES = [
    ("2024H1", "2024-01-01", "2024-06-30"),
    ("2024H2", "2024-07-01", "2024-12-31"),
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
    ("2026H1", "2026-01-01", "2026-06-30"),
]
MIN_SESSIONS_PER_WINDOW = 15
MIN_TOTAL_SESSIONS = 180


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha_frame(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for col in normalized.select_dtypes(include="category").columns:
        normalized[col] = normalized[col].astype(str)
    date_col = next((c for c in ["trade_date", "signal_date"] if c in normalized.columns), None)
    sort_cols = [date_col, "symbol"] if date_col and "symbol" in normalized.columns else [date_col] if date_col else list(normalized.columns[:2])
    payload = normalized.sort_values(sort_cols).to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _get_git_sha() -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "UNRESOLVED"
    except Exception:
        return "UNRESOLVED"


# ---------------------------------------------------------------------------
# Regime coverage — now a HARD GATE
# ---------------------------------------------------------------------------


def check_regime_coverage_hard(dates: list[Any]) -> dict[str, Any]:
    """Verify dates cover all required six-month windows.

    PR19: This is a hard gate — failure blocks golden regression.
    """
    date_set = {pd.Timestamp(d).date() for d in dates}
    coverage: dict[str, dict[str, Any]] = {}
    all_passed = True

    for window_label, start, end in REQUIRED_DATE_RANGES:
        start_d = pd.Timestamp(start).date()
        end_d = pd.Timestamp(end).date()
        window_dates = sorted(d for d in date_set if start_d <= d <= end_d)
        n_dates = len(window_dates)
        sufficient = n_dates >= MIN_SESSIONS_PER_WINDOW

        if not sufficient:
            all_passed = False

        coverage[window_label] = {
            "n_dates": n_dates,
            "sufficient": sufficient,
            "required_min": MIN_SESSIONS_PER_WINDOW,
            "date_range": (
                (str(min(window_dates)), str(max(window_dates)))
                if window_dates else ("N/A", "N/A")
            ),
        }

    # Also check total trading days
    total_sufficient = len(date_set) >= MIN_TOTAL_SESSIONS
    if not total_sufficient:
        all_passed = False

    return {
        "passed": all_passed,
        "total_dates": len(date_set),
        "total_sufficient": total_sufficient,
        "min_total_required": MIN_TOTAL_SESSIONS,
        "windows": coverage,
        "failures": [
            f"{wl}: {info['n_dates']}/{MIN_SESSIONS_PER_WINDOW} dates"
            for wl, info in coverage.items() if not info["sufficient"]
        ] + (
            [f"Total: {len(date_set)}/{MIN_TOTAL_SESSIONS} dates"]
            if not total_sufficient else []
        ),
    }


# ---------------------------------------------------------------------------
# Exact oracle date matching
# ---------------------------------------------------------------------------


def check_oracle_date_coverage(
    oracle: FrozenOracleState,
    runtime_dates: set[str],
    label: str,
) -> dict[str, Any]:
    """Verify oracle covers exactly the runtime dates — no missing, no extra.

    PR19: Any missing date is a hard failure.
    """
    oracle_dates = set(oracle.dates)
    runtime_set = set(str(d) for d in runtime_dates)

    missing = runtime_set - oracle_dates
    extra = oracle_dates - runtime_set
    common = runtime_set & oracle_dates

    passed = len(missing) == 0 and len(extra) == 0 and len(common) > 0

    return {
        "passed": passed,
        "label": label,
        "n_runtime": len(runtime_set),
        "n_oracle": len(oracle_dates),
        "n_common": len(common),
        "n_missing": len(missing),
        "n_extra": len(extra),
        "missing_dates": sorted(missing)[:10] if missing else [],
        "extra_dates": sorted(extra)[:10] if extra else [],
        "error": (
            None if passed
            else f"Oracle date mismatch: {len(missing)} missing, {len(extra)} extra"
        ),
    }


# ---------------------------------------------------------------------------
# Main PR19 golden regression
# ---------------------------------------------------------------------------


def run_golden_regression(
    date_ranges: list[tuple[str, str]],
    output_dir: Path,
    p0_oracle_path: Path | None = None,
    c0_oracle_path: Path | None = None,
    use_db_oracle: bool = False,
    engine=None,
) -> dict[str, Any]:
    """Run PR19 golden regression with hard gates.

    Parameters
    ----------
    date_ranges: list of (start_date, end_date) tuples defining explicit test windows.
    output_dir: Where to write output files.
    p0_oracle_path: Path to frozen P0 oracle directory.
    c0_oracle_path: Path to frozen C0 oracle directory.
    use_db_oracle: If True, load oracles from production DB.
    engine: SQLAlchemy engine (required if use_db_oracle=True).
    """
    # ------------------------------------------------------------------
    # Load data for all specified date ranges
    # ------------------------------------------------------------------
    if engine is None:
        from sqlalchemy import create_engine, text
        from scoreRank.core.db_config import build_sqlalchemy_url
        engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)

    from sqlalchemy import text

    # Collect all dates from specified explicit ranges
    all_dates: list[Any] = []
    for start_str, end_str in date_ranges:
        query = text("""
            SELECT DISTINCT trade_date
            FROM chenyiyun.score_rank_daily
            WHERE trade_date BETWEEN :start AND :end
            ORDER BY trade_date
        """)
        range_dates = pd.read_sql(query, engine, params={
            "start": start_str, "end": end_str,
        })
        all_dates.extend(pd.to_datetime(range_dates["trade_date"]).dt.date.tolist())

    dates = sorted(set(all_dates))
    if not dates:
        raise RuntimeError(f"No trade dates found in ranges: {date_ranges}")

    date_start = str(min(dates))
    date_end = str(max(dates))

    from scripts.research_full_pool_liquidity_strategies import (
        add_liquidity_derived_features,
        load_prices,
        load_scores,
    )

    scores = load_scores(engine, start_date=date_start, end_date=date_end, min_pool_size=1)
    prices = load_prices(engine, min(dates), max(dates), extra_days=20)
    scores = add_liquidity_derived_features(scores, prices)

    if scores.empty or prices.empty:
        raise RuntimeError("data snapshot is empty")

    # ------------------------------------------------------------------
    # HARD GATE 1: Regime coverage
    # ------------------------------------------------------------------
    regime_check = check_regime_coverage_hard(dates)
    if not regime_check["passed"]:
        failures_str = "; ".join(regime_check["failures"])
        print(f"FATAL: Insufficient regime coverage: {failures_str}")
        # Continue to produce a report, but it will be marked FAIL

    # ------------------------------------------------------------------
    # Load oracles
    # ------------------------------------------------------------------
    from scripts.research.alpha_experiments import build_experiment_specs
    specs = build_experiment_specs()

    p0_oracle: FrozenOracleState | None = None
    c0_oracle: FrozenOracleState | None = None
    provenance_errors: list[str] = []

    if p0_oracle_path:
        p0_oracle = load_frozen_oracle_from_file(p0_oracle_path)
    if c0_oracle_path:
        c0_oracle = load_frozen_oracle_from_file(c0_oracle_path)

    if use_db_oracle and (p0_oracle is None or c0_oracle is None):
        if p0_oracle is None:
            # DB oracle: exceptions now propagate (fail-closed)
            p0_oracle = load_frozen_oracle_from_db(
                engine, "production_governed_vol_position",
                date_start, date_end,
            )
        if c0_oracle is None:
            c0_oracle = load_frozen_oracle_from_db(
                engine, "production_governed_vol_position_v1_2b_dynamic_score",
                date_start, date_end,
            )

    # HARD GATE 2: Oracle must be provided (no adapter fallback)
    if p0_oracle is None:
        raise RuntimeError(
            "PR19 requires a real P0 oracle. Provide --p0-oracle or use --use-db-oracle. "
            "Adapter-generated oracles are NOT accepted for PR19 golden regression."
        )
    if c0_oracle is None:
        raise RuntimeError(
            "PR19 requires a real C0 oracle. Provide --c0-oracle or use --use-db-oracle. "
            "Adapter-generated oracles are NOT accepted for PR19 golden regression."
        )

    # HARD GATE 3: Provenance validation
    for label, oracle in [("P0", p0_oracle), ("C0", c0_oracle)]:
        prov_result = validate_oracle_provenance(oracle)
        if not prov_result["passed"]:
            provenance_errors.append(
                f"{label} provenance invalid: {'; '.join(prov_result['errors'])}"
            )

    if provenance_errors:
        print(f"FATAL: Oracle provenance validation failed:\n" + "\n".join(provenance_errors))

    # ------------------------------------------------------------------
    # HARD GATE 4: Oracle date coverage (exact match)
    # ------------------------------------------------------------------
    runtime_dates_set = {str(d) for d in dates}
    date_checks: dict[str, dict[str, Any]] = {}
    all_dates_match = True

    for label, oracle in [("P0", p0_oracle), ("C0", c0_oracle)]:
        check = check_oracle_date_coverage(oracle, runtime_dates_set, label)
        date_checks[label] = check
        if not check["passed"]:
            all_dates_match = False
            print(f"FATAL: {label} oracle date mismatch: "
                  f"{check['n_missing']} missing, {check['n_extra']} extra dates")

    # ------------------------------------------------------------------
    # Run runtime and compare against oracles
    # ------------------------------------------------------------------
    results: dict[str, OracleComparisonResult] = {}
    exit_results: dict[str, dict[str, Any]] = {}
    runtime_git_sha = _get_git_sha()

    for experiment_id, oracle in [("P0", p0_oracle), ("C0", c0_oracle)]:
        from scripts.research.strategy_runtime import resolve_runtime

        runtime = resolve_runtime(specs[experiment_id])
        state = runtime.fit(scores, prices, None)
        runtime_class = type(runtime).__name__

        # Generate runtime outputs for each date
        runtime_outputs: dict[str, pd.DataFrame] = {}
        for signal_date in dates:
            sd = str(signal_date)
            ranked = runtime.rank_as_of(state, sd, scores, prices)
            if ranked.empty:
                continue
            top5 = ranked.head(5).copy()
            weights = runtime.build_weights(
                state, ranked, sd, prices,
                runtime.target_exposure(state, sd), 5,
            )
            output = top5[["symbol", "rank_score"]].copy()
            output = output.merge(
                weights[["symbol", "final_portfolio_weight"]],
                on="symbol", how="left",
            ).fillna(0.0)
            runtime_outputs[sd] = output

        # Compare against oracle
        result = compare_against_oracle(
            oracle, runtime_outputs, top_n=5,
            test_class_name=runtime_class,
        )
        results[experiment_id] = result

        # HARD GATE 5: Exit comparison
        exit_dates_with_data = 0
        exit_total_symbols = 0
        for sd, frozen in oracle.decisions.items():
            if frozen.exit_decisions:
                exit_dates_with_data += 1
                exit_total_symbols += len(frozen.exit_decisions)

        exit_results[experiment_id] = {
            "oracle_exit_dates": exit_dates_with_data,
            "oracle_exit_symbols": exit_total_symbols,
            "runtime_exit_diff_count": result.exit_diff_count,
        }

    # ------------------------------------------------------------------
    # Determine final ALL_PASSED with all hard gates
    # ------------------------------------------------------------------
    p0_result = results.get("P0")
    c0_result = results.get("C0")

    # PR19: All gates must pass
    all_passed = bool(
        p0_result and p0_result.passed
        and c0_result and c0_result.passed
        and regime_check["passed"]
        and all_dates_match
        and not provenance_errors
        and not (p0_result.is_self_referential or c0_result.is_self_referential)
    )

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------
    failure_reasons: list[str] = []
    if not regime_check["passed"]:
        failure_reasons.append(f"REGIME_COVERAGE: {regime_check['failures']}")
    if not all_dates_match:
        for label, check in date_checks.items():
            if not check["passed"]:
                failure_reasons.append(f"DATE_COVERAGE_{label}: {check['error']}")
    if provenance_errors:
        failure_reasons.extend(provenance_errors)
    if p0_result and p0_result.is_self_referential:
        failure_reasons.append(f"P0: SELF_REFERENTIAL — {p0_result.self_referential_reason}")
    if c0_result and c0_result.is_self_referential:
        failure_reasons.append(f"C0: SELF_REFERENTIAL — {c0_result.self_referential_reason}")
    if p0_result and not p0_result.passed:
        failure_reasons.append(_describe_failure("P0", p0_result))
    if c0_result and not c0_result.passed:
        failure_reasons.append(_describe_failure("C0", c0_result))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pr_version": "PR19",
        "status": "PASS" if all_passed else "FAIL",
        "strategies": ["P0", "C0"],
        "score_snapshot_sha": _sha_frame(scores),
        "price_snapshot_sha": _sha_frame(prices),
        "runtime_git_sha": runtime_git_sha,
        "date_range": {"first": date_start, "last": date_end},
        "n_dates": len(dates),
        "regime_coverage": regime_check,
        "oracle_date_coverage": date_checks,
        "oracle_provenance": {
            "P0": _provenance_report(p0_oracle),
            "C0": _provenance_report(c0_oracle),
        },
        "P0": _result_to_dict(p0_result),
        "C0": _result_to_dict(c0_result),
        "exit_comparison": exit_results,
        "failure_reasons": failure_reasons,
    }

    # Write outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "golden_regression_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Write daily comparison CSV with oracle + runtime data
    _write_daily_diff_csv(output_dir, results, dates, scores, prices, specs,
                          p0_oracle, c0_oracle)

    # Write validation summary
    _write_validation_summary(output_dir, report)

    if not all_passed:
        raise RuntimeError(
            f"PR19 golden regression FAILED: {'; '.join(failure_reasons) if failure_reasons else 'unknown'}"
        )

    return report


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _result_to_dict(result: OracleComparisonResult | None) -> dict[str, Any]:
    if result is None:
        return {"status": "NOT_RUN"}
    return {
        "passed": result.passed,
        "is_self_referential": result.is_self_referential,
        "self_referential_reason": result.self_referential_reason,
        "n_dates": result.n_dates,
        "n_dates_matched": result.n_dates_matched,
        "n_dates_missing_oracle": result.n_dates_missing_oracle,
        "candidate_diff_max": result.candidate_diff_max,
        "top5_diff_max": result.top5_diff_max,
        "weight_diff_max": result.weight_diff_max,
        "exposure_diff_max": result.exposure_diff_max,
        "rank_score_diff_max": result.rank_score_diff_max,
        "exit_diff_count": result.exit_diff_count,
    }


def _describe_failure(label: str, result: OracleComparisonResult) -> str:
    parts = []
    if result.candidate_diff_max > 0:
        parts.append(f"candidate_diff={result.candidate_diff_max}")
    if result.top5_diff_max > 0:
        parts.append(f"top5_diff={result.top5_diff_max}")
    if result.weight_diff_max > 0.0001:
        parts.append(f"weight_diff={result.weight_diff_max:.6f}")
    if result.exposure_diff_max > 0.0001:
        parts.append(f"exposure_diff={result.exposure_diff_max:.6f}")
    if result.rank_score_diff_max > 0.0001:
        parts.append(f"rank_score_diff={result.rank_score_diff_max:.6f}")
    if result.exit_diff_count > 0:
        parts.append(f"exit_diffs={result.exit_diff_count}")
    if result.n_dates_missing_oracle > 0:
        parts.append(f"missing_oracle_dates={result.n_dates_missing_oracle}")
    if result.n_dates_matched == 0:
        parts.append("no_dates_matched")
    return f"{label}: {'; '.join(parts)}"


def _provenance_report(oracle: FrozenOracleState | None) -> dict[str, Any]:
    if oracle is None:
        return {"status": "NOT_LOADED"}
    p = oracle.provenance
    return {
        "source": p.source.value,
        "generated_at": p.generated_at,
        "git_commit_sha": p.git_commit_sha,
        "generating_class": p.generating_class,
        "generating_function": p.generating_function,
        "generator_file_sha": p.generator_file_sha[:16] + "..." if p.generator_file_sha else "",
        "config_sha": p.config_sha[:16] + "..." if p.config_sha else "",
        "data_snapshot_sha": p.data_snapshot_sha[:16] + "..." if p.data_snapshot_sha else "",
        "record_count": p.record_count,
        "approved_by": p.approved_by,
        "approval_sha": p.approval_sha[:16] + "..." if p.approval_sha else "",
    }


# ---------------------------------------------------------------------------
# Daily diff CSV with exit comparison
# ---------------------------------------------------------------------------


def _write_daily_diff_csv(
    output_dir: Path,
    results: dict[str, OracleComparisonResult],
    dates: list[Any],
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    specs,
    p0_oracle: FrozenOracleState,
    c0_oracle: FrozenOracleState,
) -> None:
    """Write daily replication diff CSV with oracle + runtime + exit data."""
    from scripts.research.strategy_runtime import resolve_runtime

    rows: list[dict[str, Any]] = []

    for experiment_id, oracle in [("P0", p0_oracle), ("C0", c0_oracle)]:
        runtime = resolve_runtime(specs[experiment_id])
        state = runtime.fit(scores, prices, None)

        for signal_date in dates:
            sd = str(signal_date)
            ranked = runtime.rank_as_of(state, sd, scores, prices)
            if ranked.empty:
                continue

            rt_symbols = ranked["symbol"].astype(str).head(5).tolist()
            weights = runtime.build_weights(
                state, ranked, sd, prices,
                runtime.target_exposure(state, sd), 5,
            )
            rt_weights = dict(zip(
                weights["symbol"].astype(str),
                weights["final_portfolio_weight"].astype(float),
            ))
            rt_exposure = float(sum(rt_weights.values()))

            # Get oracle data for this date
            frozen = oracle.get_decision(sd)
            oracle_symbols = list(frozen.symbols) if frozen else []
            oracle_weights = dict(zip(
                oracle_symbols,
                list(frozen.final_weights) if frozen else [],
            ))
            oracle_exposure = frozen.total_exposure if frozen else 0.0
            oracle_exits = list(frozen.exit_decisions) if frozen else []

            rows.append({
                "strategy": experiment_id,
                "signal_date": sd,
                "runtime_symbols": json.dumps(rt_symbols),
                "runtime_weights": json.dumps(rt_weights),
                "runtime_exposure": rt_exposure,
                "oracle_symbols": json.dumps(oracle_symbols),
                "oracle_weights": json.dumps(oracle_weights),
                "oracle_exposure": oracle_exposure,
                "oracle_exits": json.dumps(oracle_exits),
                "oracle_has_data": frozen is not None,
            })

    if rows:
        pd.DataFrame(rows).to_csv(output_dir / "daily_replication_diff.csv", index=False)


def _write_validation_summary(output_dir: Path, report: dict[str, Any]) -> None:
    """Write human-readable validation summary."""
    lines = [
        "# PR19 Golden Regression — Validation Summary",
        "",
        f"**Status**: {report['status']}",
        f"**Generated**: {report['generated_at']}",
        f"**Dates**: {report['date_range']['first']} to {report['date_range']['last']}",
        f"**Total sessions**: {report['n_dates']}",
        "",
        "## Hard Gates",
        "",
    ]

    # Regime coverage
    regime = report.get("regime_coverage", {})
    regime_passed = regime.get("passed", False)
    lines.append(f"### Regime Coverage: {'✅ PASS' if regime_passed else '❌ FAIL'}")
    for wl, info in sorted(regime.get("windows", {}).items()):
        status = "✅" if info["sufficient"] else "❌"
        lines.append(
            f"- {status} **{wl}**: {info['n_dates']} dates "
            f"(min {info['required_min']}, range {info['date_range'][0]} to {info['date_range'][1]})"
        )
    lines.append(f"- Total: {regime.get('total_dates', 0)} dates (min {regime.get('min_total_required', 180)})")

    # Oracle date coverage
    lines.append("")
    lines.append("### Oracle Date Coverage")
    for label, check in sorted(report.get("oracle_date_coverage", {}).items()):
        status = "✅" if check["passed"] else "❌"
        lines.append(
            f"- {status} **{label}**: runtime={check['n_runtime']}, oracle={check['n_oracle']}, "
            f"common={check['n_common']}, missing={check['n_missing']}, extra={check['n_extra']}"
        )
        if not check["passed"]:
            lines.append(f"  - Error: {check['error']}")

    # Provenance
    lines.append("")
    lines.append("### Oracle Provenance")
    for label in ["P0", "C0"]:
        prov = report.get("oracle_provenance", {}).get(label, {})
        if prov:
            lines.append(f"- **{label}**: source={prov.get('source')}, "
                        f"git={prov.get('git_commit_sha', '')[:8]}, "
                        f"records={prov.get('record_count', 0)}")

    # Comparison results
    lines.append("")
    lines.append("## Oracle Comparison Results")
    for exp_id in ["P0", "C0"]:
        result = report.get(exp_id, {})
        if not result:
            continue
        status_icon = "✅ PASS" if result.get("passed") else "❌ FAIL"
        lines.append(f"### {exp_id}: {status_icon}")
        if result.get("is_self_referential"):
            lines.append(f"- ⚠️ SELF_REFERENTIAL: {result.get('self_referential_reason', '')}")
        lines.append(f"- Candidates diff max: {result.get('candidate_diff_max', 'N/A')}")
        lines.append(f"- Top5 diff max: {result.get('top5_diff_max', 'N/A')}")
        lines.append(f"- Weight diff max: {result.get('weight_diff_max', 'N/A')}")
        lines.append(f"- Exposure diff max: {result.get('exposure_diff_max', 'N/A')}")
        lines.append(f"- Rank score diff max: {result.get('rank_score_diff_max', 'N/A')}")
        lines.append(f"- Exit diff count: {result.get('exit_diff_count', 'N/A')}")
        lines.append(f"- Dates matched: {result.get('n_dates_matched', 0)}/{result.get('n_dates', 0)}")
        lines.append(f"- Dates missing oracle: {result.get('n_dates_missing_oracle', 0)}")
        lines.append("")

    if report.get("failure_reasons"):
        lines.append("## Failure Reasons")
        for i, reason in enumerate(report["failure_reasons"], 1):
            lines.append(f"{i}. {reason}")

    (output_dir / "validation_summary.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PR19: Production-grade Oracle Coverage Golden Regression"
    )
    parser.add_argument("--p0-oracle", type=Path,
                       help="Path to frozen P0 oracle directory")
    parser.add_argument("--c0-oracle", type=Path,
                       help="Path to frozen C0 oracle directory")
    parser.add_argument("--use-db-oracle", action="store_true",
                       help="Load oracles from production DB tables")
    parser.add_argument("--date-range", type=str, default="2024-01-01:2026-06-30",
                       help="Date range as start:end (default: 2024-01-01:2026-06-30)")
    parser.add_argument("--date-ranges", type=str, nargs="*",
                       help="Multiple date ranges as start:end pairs")
    parser.add_argument("--output-dir", type=Path,
                       help="Output directory for reports")
    args = parser.parse_args()

    # Parse date ranges
    if args.date_ranges:
        parsed_ranges = []
        for dr in args.date_ranges:
            parts = dr.split(":")
            if len(parts) != 2:
                raise ValueError(f"Invalid date range: {dr} (expected start:end)")
            parsed_ranges.append((parts[0], parts[1]))
    else:
        parts = args.date_range.split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid date range: {args.date_range} (expected start:end)")
        parsed_ranges = [(parts[0], parts[1])]

    output = args.output_dir or (
        PROJECT_ROOT / "exports" / "full_strategy_v3_validation"
        / f"pr19_golden_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    report = run_golden_regression(
        date_ranges=parsed_ranges,
        output_dir=output,
        p0_oracle_path=args.p0_oracle,
        c0_oracle_path=args.c0_oracle,
        use_db_oracle=args.use_db_oracle,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
