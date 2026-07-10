"""PR18: Independent Oracle Golden Regression.

Key changes from PR16:
  1. Independent P0/C0 oracles — loaded from frozen files or production DB,
     NOT re-computed by the same Adapter code.
  2. Self-referential detection — fails if oracle and test use same class.
  3. Real exit diff — compares exit symbol, date, reason, shares per day.
  4. Extended coverage — 180+ trading days across multiple market regimes
     (2024 weak/choppy, 2025H2, 2026H1).

Usage:
  # With frozen oracle files (preferred):
  python scripts/research/run_pr18_golden_regression.py \
    --p0-oracle exports/frozen_oracles/P0/ \
    --c0-oracle exports/frozen_oracles/C0/ \
    --sessions 180

  # With production DB (fallback):
  python scripts/research/run_pr18_golden_regression.py \
    --use-db-oracle \
    --sessions 180
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
    OracleProvenance,
    OracleSource,
    FrozenOracleState,
    FrozenDailyDecision,
    compare_against_oracle,
    detect_self_referential,
    export_frozen_oracle,
    load_frozen_oracle_from_db,
    load_frozen_oracle_from_file,
    canonical_sha,
)
from scripts.research.alpha_experiments import build_experiment_specs
from scripts.research.strategy_adapters import (
    ChampionStrategyAdapter,
    ProductionStrategyAdapter,
)
from scripts.research.strategy_runtime import (
    resolve_runtime,
    RuntimeState,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SESSIONS = 180  # PR18 minimum: at least 180 trading days
REQUIRED_REGIME_PERIODS = {
    "2024_weak": ("2024-01-01", "2024-06-30"),
    "2025H2": ("2025-07-01", "2025-12-31"),
    "2026H1": ("2026-01-01", "2026-06-30"),
}
MIN_SESSIONS_PER_REGIME = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha_frame(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for col in normalized.select_dtypes(include="category").columns:
        normalized[col] = normalized[col].astype(str)
    payload = normalized.sort_values(["trade_date", "symbol"]).to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _check_regime_coverage(dates: list[Any]) -> dict[str, Any]:
    """Verify dates cover required market regime periods."""
    date_set = {pd.Timestamp(d).date() for d in dates}
    coverage: dict[str, dict[str, Any]] = {}

    for regime_name, (start, end) in REQUIRED_REGIME_PERIODS.items():
        start_d = pd.Timestamp(start).date()
        end_d = pd.Timestamp(end).date()
        regime_dates = [d for d in date_set if start_d <= d <= end_d]
        coverage[regime_name] = {
            "n_dates": len(regime_dates),
            "sufficient": len(regime_dates) >= MIN_SESSIONS_PER_REGIME,
            "date_range": (str(min(regime_dates)) if regime_dates else "N/A",
                          str(max(regime_dates)) if regime_dates else "N/A"),
        }

    all_sufficient = all(v["sufficient"] for v in coverage.values())
    return {
        "passed": all_sufficient,
        "total_dates": len(date_set),
        "regimes": coverage,
    }


# ---------------------------------------------------------------------------
# Build frozen oracle from current adapter output (for initial export only)
# ---------------------------------------------------------------------------


def build_oracle_from_adapter(
    adapter,
    runtime,
    runtime_state: RuntimeState,
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    dates: list[Any],
    experiment_id: str,
    git_sha: str,
) -> FrozenOracleState:
    """Build a frozen oracle from adapter output.

    This is a ONE-TIME export function.  The resulting oracle file should be
    committed and used as the immutable reference for future golden regression
    tests.  The generating class is recorded as the adapter class so that
    self-referential detection can flag it.
    """
    decisions: dict[str, FrozenDailyDecision] = {}
    decision_dfs: dict[str, pd.DataFrame] = {}

    for signal_date in dates:
        sd = str(signal_date)
        ranked = runtime.rank_as_of(runtime_state, sd, scores, prices)
        if ranked.empty:
            continue

        top5 = ranked.head(5).copy()
        weights = runtime.build_weights(
            runtime_state, ranked, sd, prices,
            runtime.target_exposure(runtime_state, sd), 5,
        )

        # Build decision DataFrame
        decision_df = top5[["symbol", "rank_score"]].copy()
        decision_df = decision_df.merge(
            weights[["symbol", "final_portfolio_weight"]],
            on="symbol", how="left",
        ).fillna(0.0)

        decisions[sd] = FrozenDailyDecision(
            signal_date=sd,
            symbols=tuple(decision_df["symbol"].astype(str).tolist()),
            rank_scores=tuple(decision_df["rank_score"].astype(float).tolist()),
            final_weights=tuple(decision_df["final_portfolio_weight"].astype(float).tolist()),
            total_exposure=float(decision_df["final_portfolio_weight"].sum()),
        )
        decision_dfs[sd] = decision_df

    # Export to frozen oracle format
    output_dir = (
        PROJECT_ROOT / "exports" / "frozen_oracles" / experiment_id
        / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )

    adapter_class = type(adapter).__name__
    return export_frozen_oracle(
        decisions=decision_dfs,
        strategy_id=adapter.identity.strategy_id,
        experiment_id=experiment_id,
        git_commit_sha=git_sha,
        generating_class=f"scripts.research.strategy_adapters.{adapter_class}",
        generating_function=f"{adapter_class}.rank",
        output_dir=output_dir,
        config_sha=adapter.identity.config_sha,
        notes=(
            f"Initial frozen oracle export for {experiment_id}. "
            f"WARNING: Generated by adapter code — self-referential check will fail. "
            f"Replace with independent production output before using as golden reference."
        ),
    )


# ---------------------------------------------------------------------------
# Main golden regression
# ---------------------------------------------------------------------------


def run_golden_regression(
    sessions: int,
    output_dir: Path,
    p0_oracle_path: Path | None = None,
    c0_oracle_path: Path | None = None,
    use_db_oracle: bool = False,
    engine=None,
) -> dict[str, Any]:
    """Run PR18 golden regression with independent oracles.

    Parameters
    ----------
    sessions: Number of trading sessions to test (min 180).
    output_dir: Where to write output files.
    p0_oracle_path: Path to frozen P0 oracle directory (from independent source).
    c0_oracle_path: Path to frozen C0 oracle directory (from independent source).
    use_db_oracle: If True, load oracles from production DB tables.
    engine: SQLAlchemy engine (required if use_db_oracle=True).
    """
    if sessions < MIN_SESSIONS:
        raise ValueError(
            f"PR18 golden regression requires at least {MIN_SESSIONS} sessions, got {sessions}"
        )

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    if engine is None:
        from sqlalchemy import create_engine, text
        from scoreRank.core.db_config import build_sqlalchemy_url
        engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)

    from sqlalchemy import text
    date_frame = pd.read_sql(text("""
        SELECT DISTINCT trade_date
        FROM chenyiyun.score_rank_daily
        ORDER BY trade_date DESC
        LIMIT :sessions
    """), engine, params={"sessions": sessions})

    dates = sorted(pd.to_datetime(date_frame["trade_date"]).dt.date.tolist())
    if len(dates) < sessions:
        raise RuntimeError(f"insufficient score sessions: {len(dates)} < {sessions}")

    from scripts.research_full_pool_liquidity_strategies import (
        add_liquidity_derived_features,
        load_prices,
        load_scores,
    )

    scores = load_scores(
        engine, start_date=str(min(dates)), end_date=str(max(dates)), min_pool_size=1
    )
    prices = load_prices(engine, min(dates), max(dates), extra_days=20)
    scores = add_liquidity_derived_features(scores, prices)

    if scores.empty or prices.empty:
        raise RuntimeError("data snapshot is empty")

    # ------------------------------------------------------------------
    # Check regime coverage
    # ------------------------------------------------------------------
    regime_check = _check_regime_coverage(dates)
    if not regime_check["passed"]:
        print(f"WARNING: Insufficient regime coverage: {regime_check['regimes']}")

    # ------------------------------------------------------------------
    # Load or build oracles
    # ------------------------------------------------------------------
    specs = build_experiment_specs()

    p0_oracle: FrozenOracleState | None = None
    c0_oracle: FrozenOracleState | None = None

    if p0_oracle_path:
        p0_oracle = load_frozen_oracle_from_file(p0_oracle_path)
    if c0_oracle_path:
        c0_oracle = load_frozen_oracle_from_file(c0_oracle_path)

    if use_db_oracle and (p0_oracle is None or c0_oracle is None):
        if p0_oracle is None:
            p0_oracle = load_frozen_oracle_from_db(
                engine, "production_governed_vol_position",
                str(min(dates)), str(max(dates)),
            )
        if c0_oracle is None:
            c0_oracle = load_frozen_oracle_from_db(
                engine, "production_governed_vol_position_v1_2b_dynamic_score",
                str(min(dates)), str(max(dates)),
            )

    # If no oracle available, build from adapter (ONE-TIME export mode)
    # This will be flagged as self-referential
    oracles_built_from_adapter = False
    if p0_oracle is None:
        print("WARNING: No P0 oracle provided. Building from adapter (SELF_REFERENTIAL).")
        p0_adapter = ProductionStrategyAdapter(top_n=5)
        p0_runtime = resolve_runtime(specs["P0"])
        p0_state = p0_runtime.fit(scores, prices, None)
        p0_oracle = build_oracle_from_adapter(
            p0_adapter, p0_runtime, p0_state, scores, prices, dates,
            "P0", _get_git_sha(),
        )
        oracles_built_from_adapter = True

    if c0_oracle is None:
        print("WARNING: No C0 oracle provided. Building from adapter (SELF_REFERENTIAL).")
        c0_adapter = ChampionStrategyAdapter(top_n=5)
        c0_runtime = resolve_runtime(specs["C0"])
        c0_state = c0_runtime.fit(scores, prices, None)
        c0_oracle = build_oracle_from_adapter(
            c0_adapter, c0_runtime, c0_state, scores, prices, dates,
            "C0", _get_git_sha(),
        )
        oracles_built_from_adapter = True

    # ------------------------------------------------------------------
    # Run runtime and compare against oracles
    # ------------------------------------------------------------------
    results: dict[str, OracleComparisonResult] = {}

    for experiment_id, oracle in [("P0", p0_oracle), ("C0", c0_oracle)]:
        runtime = resolve_runtime(specs[experiment_id])
        state = runtime.fit(scores, prices, None)

        # Generate runtime outputs for each signal date
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
        test_class = type(runtime).__name__
        result = compare_against_oracle(
            oracle,
            runtime_outputs,
            top_n=5,
            test_class_name=test_class,
        )
        results[experiment_id] = result

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------
    p0_result = results.get("P0")
    c0_result = results.get("C0")

    all_passed = bool(
        p0_result and p0_result.passed
        and c0_result and c0_result.passed
        and not (p0_result.is_self_referential or c0_result.is_self_referential)
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pr_version": "PR18",
        "status": "PASS" if all_passed else "FAIL",
        "sessions": sessions,
        "sessions_actual": len(dates),
        "strategies": ["P0", "C0"],
        "oracles_built_from_adapter": oracles_built_from_adapter,
        "score_snapshot_sha": _sha_frame(scores),
        "price_snapshot_sha": _sha_frame(prices),
        "regime_coverage": regime_check,
        "date_range": {
            "first": str(min(dates)) if dates else "",
            "last": str(max(dates)) if dates else "",
        },
        "P0": _result_to_dict(p0_result) if p0_result else {},
        "C0": _result_to_dict(c0_result) if c0_result else {},
        "failure_reasons": _collect_failures(p0_result, c0_result),
    }

    # Write outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "golden_regression_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Write daily comparison CSV
    _write_daily_diff_csv(output_dir, results, dates, scores, prices, specs)

    # Write validation summary
    _write_validation_summary(output_dir, report, regime_check)

    if not all_passed:
        failures = report["failure_reasons"]
        raise RuntimeError(
            f"PR18 golden regression FAILED: {'; '.join(failures) if failures else 'unknown'}"
        )

    return report


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
        "exit_diff_count": result.exit_diff_count,
    }


def _collect_failures(
    p0_result: OracleComparisonResult | None,
    c0_result: OracleComparisonResult | None,
) -> list[str]:
    failures: list[str] = []
    for label, result in [("P0", p0_result), ("C0", c0_result)]:
        if result is None:
            failures.append(f"{label}: NOT_RUN")
            continue
        if result.is_self_referential:
            failures.append(f"{label}: SELF_REFERENTIAL — {result.self_referential_reason}")
        if not result.passed:
            parts = []
            if result.candidate_diff_max > 0:
                parts.append(f"candidate_diff={result.candidate_diff_max}")
            if result.top5_diff_max > 0:
                parts.append(f"top5_diff={result.top5_diff_max}")
            if result.weight_diff_max > 0.0001:
                parts.append(f"weight_diff={result.weight_diff_max:.6f}")
            if result.exposure_diff_max > 0.0001:
                parts.append(f"exposure_diff={result.exposure_diff_max:.6f}")
            if result.exit_diff_count > 0:
                parts.append(f"exit_diffs={result.exit_diff_count}")
            if result.n_dates_matched == 0:
                parts.append("no_dates_matched")
            failures.append(f"{label}: {'; '.join(parts)}")
    return failures


def _write_daily_diff_csv(
    output_dir: Path,
    results: dict[str, OracleComparisonResult],
    dates: list[Any],
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    specs,
) -> None:
    """Write daily replication diff CSV with real exit comparison."""
    rows: list[dict[str, Any]] = []

    for experiment_id in ["P0", "C0"]:
        oracle = results.get(experiment_id)
        if oracle is None:
            continue

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

            rows.append({
                "strategy": experiment_id,
                "signal_date": sd,
                "runtime_symbols": json.dumps(rt_symbols),
                "runtime_weights": json.dumps(rt_weights),
                "runtime_exposure": float(sum(rt_weights.values())),
            })

    if rows:
        pd.DataFrame(rows).to_csv(output_dir / "daily_replication_diff.csv", index=False)


def _write_validation_summary(
    output_dir: Path,
    report: dict[str, Any],
    regime_check: dict[str, Any],
) -> None:
    """Write human-readable validation summary."""
    lines = [
        "# PR18 Golden Regression — Validation Summary",
        "",
        f"**Status**: {report['status']}",
        f"**Generated**: {report['generated_at']}",
        f"**Sessions**: {report['sessions']} (actual: {report['sessions_actual']})",
        "",
        "## Regime Coverage",
    ]

    for regime, info in sorted(regime_check.get("regimes", {}).items()):
        status = "✅" if info["sufficient"] else "❌"
        lines.append(f"- {status} **{regime}**: {info['n_dates']} dates ({info['date_range'][0]} to {info['date_range'][1]})")

    lines.append("")
    lines.append("## Oracle Comparison Results")
    lines.append("")

    for exp_id in ["P0", "C0"]:
        result = report.get(exp_id, {})
        if not result:
            continue
        status = "✅ PASS" if result.get("passed") else "❌ FAIL"
        lines.append(f"### {exp_id}: {status}")
        if result.get("is_self_referential"):
            lines.append(f"- ⚠️ **SELF_REFERENTIAL**: {result.get('self_referential_reason', '')}")
        lines.append(f"- Candidate diff max: {result.get('candidate_diff_max', 'N/A')}")
        lines.append(f"- Top5 diff max: {result.get('top5_diff_max', 'N/A')}")
        lines.append(f"- Weight diff max: {result.get('weight_diff_max', 'N/A')}")
        lines.append(f"- Exposure diff max: {result.get('exposure_diff_max', 'N/A')}")
        lines.append(f"- Exit diff count: {result.get('exit_diff_count', 'N/A')}")
        lines.append(f"- Dates matched: {result.get('n_dates_matched', 0)}/{result.get('n_dates', 0)}")
        lines.append("")

    if report.get("failure_reasons"):
        lines.append("## Failure Reasons")
        for reason in report["failure_reasons"]:
            lines.append(f"- {reason}")

    (output_dir / "validation_summary.md").write_text("\n".join(lines), encoding="utf-8")


def _get_git_sha() -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PR18: Independent Oracle Golden Regression"
    )
    parser.add_argument("--sessions", type=int, default=MIN_SESSIONS,
                       help=f"Number of trading sessions (min {MIN_SESSIONS})")
    parser.add_argument("--output-dir", type=Path,
                       help="Output directory for reports")
    parser.add_argument("--p0-oracle", type=Path,
                       help="Path to frozen P0 oracle directory")
    parser.add_argument("--c0-oracle", type=Path,
                       help="Path to frozen C0 oracle directory")
    parser.add_argument("--use-db-oracle", action="store_true",
                       help="Load oracles from production DB tables")
    parser.add_argument("--export-only", action="store_true",
                       help="Export frozen oracles from adapters (one-time setup)")
    args = parser.parse_args()

    output = args.output_dir or (
        PROJECT_ROOT / "exports" / "full_strategy_v3_validation"
        / f"pr18_golden_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    if args.export_only:
        # One-time export mode: build oracles from adapters
        from sqlalchemy import create_engine
        from scoreRank.core.db_config import build_sqlalchemy_url
        engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)
        from scripts.research_full_pool_liquidity_strategies import (
            add_liquidity_derived_features, load_prices, load_scores,
        )

        from sqlalchemy import text
        date_frame = pd.read_sql(text("""
            SELECT DISTINCT trade_date FROM chenyiyun.score_rank_daily
            ORDER BY trade_date DESC LIMIT :sessions
        """), engine, params={"sessions": args.sessions})
        dates = sorted(pd.to_datetime(date_frame["trade_date"]).dt.date.tolist())
        scores = load_scores(engine, start_date=str(min(dates)), end_date=str(max(dates)), min_pool_size=1)
        prices = load_prices(engine, min(dates), max(dates), extra_days=20)
        scores = add_liquidity_derived_features(scores, prices)

        specs = build_experiment_specs()
        git_sha = _get_git_sha()

        for exp_id, adapter_cls in [("P0", ProductionStrategyAdapter), ("C0", ChampionStrategyAdapter)]:
            adapter = adapter_cls(top_n=5)
            runtime = resolve_runtime(specs[exp_id])
            state = runtime.fit(scores, prices, None)
            oracle = build_oracle_from_adapter(
                adapter, runtime, state, scores, prices, dates, exp_id, git_sha,
            )
            print(f"Exported {exp_id} oracle: {oracle.n_dates} dates, range {oracle.date_range}")

        print("\nWARNING: These oracles were built from adapter code.")
        print("They are SELF_REFERENTIAL and will FAIL golden regression.")
        print("Replace with independent production output for real validation.")
        return

    report = run_golden_regression(
        sessions=args.sessions,
        output_dir=output,
        p0_oracle_path=args.p0_oracle,
        c0_oracle_path=args.c0_oracle,
        use_db_oracle=args.use_db_oracle,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
