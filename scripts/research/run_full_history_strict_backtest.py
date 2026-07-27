"""Full-history strict T+1 backtest launcher — 2013 to present.

Runs the production strategy against the longest available history with
strict execution rules (corporate actions, T+1 fills, ledger reconciliation).

Output:
  exports/full_history_backtest/{timestamp}/
    full_history_report.json
    full_history_summary.csv
    equity_curve.csv
    strict_ledger_verification.json

The report MUST cover all required market regimes defined in
config/production_acceptance.yaml.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Required market regimes per acceptance criteria
REQUIRED_REGIMES: list[dict[str, str]] = [
    {"name": "2015 bull + deleveraging", "start": "2014-07-01", "end": "2016-02-29"},
    {"name": "2016-2018 bear + style rotation", "start": "2016-03-01", "end": "2018-12-31"},
    {"name": "2020 extreme volatility", "start": "2020-01-01", "end": "2020-12-31"},
    {"name": "2021 sector concentration", "start": "2021-01-01", "end": "2021-12-31"},
    {"name": "2022 growth compression", "start": "2022-01-01", "end": "2022-12-31"},
    {"name": "2023-2024 micro-cap + quant crowding", "start": "2023-01-01", "end": "2024-12-31"},
    {"name": "2025-2026 current regime", "start": "2025-01-01", "end": "2026-12-31"},
]

DEFAULT_START_DATE = "2013-01-01"
ACCOUNT_SIZES = [500_000, 1_500_000, 3_000_000, 5_000_000, 10_000_000]
EXECUTION_SCENARIOS = (
    ("BASE_7P5_10", 0.00075, 10),
    ("CONSERVATIVE_15_25", 0.0015, 25),
    ("CONSERVATIVE_15_50", 0.0015, 50),
    ("EXTREME_30_100", 0.0030, 100),
    ("EXTREME_50_100", 0.0050, 100),
)


def _load_acceptance() -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    path = PROJECT_ROOT / "config" / "production_acceptance.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text()).get("acceptance", {})
    return {}


def build_backtest_command(
    start_date: str,
    end_date: str,
    strategy: str = "production_governed_vol_position",
    cost_rate: float = 0.00075,
    slippage_bps: int = 10,
    initial_cash: int = 500_000,
    output_dir: str = "",
    scores_snapshot: str | None = None,
    prices_snapshot: str | None = None,
    corporate_action_snapshot: str | None = None,
    corporate_action_manifest: str | None = None,
    security_lifecycle_snapshot: str | None = None,
    security_lifecycle_manifest: str | None = None,
    trade_calendar_snapshot: str | None = None,
    require_verified_evidence: bool = True,
) -> list[str]:
    """Build the subprocess command for the trusted strategy account backtest."""
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"),
        "--risk-profile", "adaptive",
        "--strategies", strategy,
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", start_date,
        "--end-date", end_date,
        "--trade-cost-rate", str(cost_rate),
        "--slippage-rate", str(slippage_bps / 10_000),
        "--initial-cash", str(initial_cash),
        "--output-dir", output_dir,
    ]
    optional_paths = {
        "--scores-snapshot": scores_snapshot,
        "--prices-snapshot": prices_snapshot,
        "--corporate-action-snapshot": corporate_action_snapshot,
        "--corporate-action-manifest": corporate_action_manifest,
        "--security-lifecycle-snapshot": security_lifecycle_snapshot,
        "--security-lifecycle-manifest": security_lifecycle_manifest,
        "--trade-calendar-snapshot": trade_calendar_snapshot,
    }
    for flag, value in optional_paths.items():
        if value:
            command.extend([flag, value])
    if require_verified_evidence:
        command.append("--require-verified-evidence")
    return command


def check_regime_coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Check which required market regimes are covered by the backtest data."""
    start = report.get("start_date", "")
    end = report.get("end_date", "")

    covered: list[str] = []
    gaps: list[str] = []

    for regime in REQUIRED_REGIMES:
        r_start = regime["start"]
        r_end = regime["end"]
        # A regime counts only when the backtest begins no later than the
        # regime start and reaches the portion observable by the run end.
        effective_end = min(r_end, end) if end else r_end
        if (
            start
            and end
            and start <= r_start
            and end >= r_start
            and end >= effective_end
        ):
            covered.append(regime["name"])
        else:
            gaps.append(regime["name"])

    acceptance = _load_acceptance()
    full_history = acceptance.get("full_history", {})
    min_coverage = full_history.get("min_trade_day_coverage", 0.98)

    return {
        "total_regimes": len(REQUIRED_REGIMES),
        "covered": covered,
        "gaps": gaps,
        "coverage_ratio": len(covered) / len(REQUIRED_REGIMES) if REQUIRED_REGIMES else 1.0,
        "min_coverage_threshold": min_coverage,
        "passed": len(covered) / len(REQUIRED_REGIMES) >= min_coverage if REQUIRED_REGIMES else True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    snapshot_values = {
        "scores_snapshot": getattr(args, "scores_snapshot", None),
        "prices_snapshot": getattr(args, "prices_snapshot", None),
        "corporate_action_snapshot": getattr(args, "corporate_action_snapshot", None),
        "corporate_action_manifest": getattr(args, "corporate_action_manifest", None),
        "security_lifecycle_snapshot": getattr(args, "security_lifecycle_snapshot", None),
        "security_lifecycle_manifest": getattr(args, "security_lifecycle_manifest", None),
        "trade_calendar_snapshot": getattr(args, "trade_calendar_snapshot", None),
    }
    missing_snapshots = [name for name, value in snapshot_values.items() if not value]
    if missing_snapshots and not args.dry_run:
        raise RuntimeError(
            "verified_full_history_snapshots_required:"
            + ",".join(missing_snapshots)
        )

    scenario_results: list[dict[str, Any]] = []
    scenarios = EXECUTION_SCENARIOS if not args.skip_stress else EXECUTION_SCENARIOS[:1]
    for account_size in ACCOUNT_SIZES:
        for scenario, cost, slip in scenarios:
            scenario_dir = output / f"capital_{account_size}" / scenario
            scenario_dir.parent.mkdir(parents=True, exist_ok=True)
            cmd = build_backtest_command(
                start_date=args.start_date, end_date=args.end_date,
                strategy=args.strategy, cost_rate=cost, slippage_bps=slip,
                initial_cash=account_size, output_dir=str(scenario_dir),
                **snapshot_values,
            )
            status = "DRY_RUN"
            return_code = None
            if not args.dry_run:
                completed = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
                scenario_dir.mkdir(parents=True, exist_ok=True)
                (scenario_dir / "run.log").write_text(completed.stdout + "\n" + completed.stderr)
                return_code = completed.returncode
                status = "COMPLETE" if completed.returncode == 0 else "FAILED"
            scenario_results.append({
                "account_size": account_size, "scenario": scenario,
                "cost_rate": cost, "slippage_bps": slip,
                "command": cmd, "return_code": return_code, "status": status,
            })

    # Build summary
    summary = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "strategy": args.strategy,
        "required_regimes": REQUIRED_REGIMES,
        "scenario_count": len(scenario_results),
        "scenarios": scenario_results,
        "status": (
            "DRY_RUN" if args.dry_run
            else "BLOCKED" if any(item["status"] == "FAILED" for item in scenario_results)
            else "COMPLETE"
        ),
        "dry_run": args.dry_run,
        "output_dir": str(output),
    }

    (output / "full_history_summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run full-history strict T+1 backtest (2013–present)."
    )
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument(
        "--strategy",
        default="production_governed_vol_position_v1_2b_dynamic_score",
    )
    parser.add_argument("--output-dir", default=str(
        PROJECT_ROOT / "exports" / "full_history_backtest" / datetime.now().strftime("%Y%m%d_%H%M%S")
    ))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-stress", action="store_true")
    parser.add_argument("--scores-snapshot")
    parser.add_argument("--prices-snapshot")
    parser.add_argument("--corporate-action-snapshot")
    parser.add_argument("--corporate-action-manifest")
    parser.add_argument("--security-lifecycle-snapshot")
    parser.add_argument("--security-lifecycle-manifest")
    parser.add_argument("--trade-calendar-snapshot")
    _args = parser.parse_args()
    run(_args)


if __name__ == "__main__":
    main()
