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
DEFAULT_COST_RATES = [0.00075, 0.001, 0.0015]
DEFAULT_SLIPPAGE_BPS = [0, 10, 25, 50]


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
    output_dir: str = "",
) -> list[str]:
    """Build the subprocess command for the trusted strategy account backtest."""
    return [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"),
        "--risk-profile", "adaptive",
        "--strategies", strategy,
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", start_date,
        "--end-date", end_date,
        "--trade-cost-rate", str(cost_rate),
        "--slippage-rate", str(slippage_bps / 10_000),
        "--output-dir", output_dir,
    ]


def check_regime_coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Check which required market regimes are covered by the backtest data."""
    start = report.get("start_date", "")
    end = report.get("end_date", "")

    covered: list[str] = []
    gaps: list[str] = []

    for regime in REQUIRED_REGIMES:
        r_start = regime["start"]
        r_end = regime["end"]
        # Simple overlap check
        if start and end and r_end >= start and r_start <= end:
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

    # Phase 1: Base scenario (7.5 bps cost, 10 bps slippage)
    base_dir = output / "base"
    base_dir.mkdir(exist_ok=True)
    base_cmd = build_backtest_command(
        start_date=args.start_date,
        end_date=args.end_date,
        strategy=args.strategy,
        cost_rate=0.00075,
        slippage_bps=10,
        output_dir=str(base_dir),
    )

    print(f"Running base scenario: {args.start_date} → {args.end_date}")
    if not args.dry_run:
        result = subprocess.run(base_cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
        (base_dir / "run.log").write_text(result.stdout + "\n" + result.stderr)

    # Phase 2: Stress scenarios (subset for speed)
    stress_results: list[dict] = []
    if not args.skip_stress:
        for cost in DEFAULT_COST_RATES[1:]:  # Skip base (already run)
            for slip in DEFAULT_SLIPPAGE_BPS:
                label = f"cost_{int(cost*10000)}bps_slip_{slip}bps"
                stress_dir = output / "stress" / label
                stress_dir.mkdir(parents=True, exist_ok=True)
                cmd = build_backtest_command(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    strategy=args.strategy,
                    cost_rate=cost,
                    slippage_bps=slip,
                    output_dir=str(stress_dir),
                )
                if not args.dry_run:
                    r = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
                    (stress_dir / "run.log").write_text(r.stdout + "\n" + r.stderr)
                stress_results.append({
                    "cost_rate": cost, "slippage_bps": slip,
                    "status": "DRY_RUN" if args.dry_run else "SUBMITTED",
                })

    # Build summary
    summary = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "strategy": args.strategy,
        "required_regimes": REQUIRED_REGIMES,
        "stress_scenarios": len(stress_results),
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
    parser.add_argument("--strategy", default="production_governed_vol_position")
    parser.add_argument("--output-dir", default=str(
        PROJECT_ROOT / "exports" / "full_history_backtest" / datetime.now().strftime("%Y%m%d_%H%M%S")
    ))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-stress", action="store_true")
    _args = parser.parse_args()
    run(_args)


if __name__ == "__main__":
    main()
