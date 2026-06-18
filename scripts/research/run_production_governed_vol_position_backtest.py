"""Run the production-governed strategy validation bundle.

The governed candidate is validated against the existing account-level trusted
strategy simulator and then passed to worst-case attribution. The current
simulator entrypoint supplies the T+1 account mechanics and adaptive v2.2 risk
anchor; the risk-governor audit table remains the online source of truth.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_SCRIPT = PROJECT_ROOT / "scripts/research_trusted_strategy_account_backtest.py"
WORST_CASE_SCRIPT = PROJECT_ROOT / "scripts/research/analyze_production_worst_cases.py"
DEFAULT_STRATEGIES = ",".join(
    [
        "production_governed_vol_position",
        "baseline_full_liquidity_detail_vol_position",
        "adaptive_market_style",
        "baseline_full_liquidity_detail",
        "baseline_full_liquidity",
        "tiered_liquidity_then_bs_v2",
    ]
)


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True, check=True)
    return result.stdout


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        return {"raw_stdout": stdout}
    return json.loads(stdout[start : end + 1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production_governed_vol_position validation bundle.")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--position-ratio", type=float, default=0.7)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-total-positions", type=int, default=5)
    parser.add_argument("--strategies", default=DEFAULT_STRATEGIES)
    parser.add_argument("--skip-db-check", action="store_true")
    args = parser.parse_args()

    if not args.skip_db_check:
        _run([sys.executable, str(PROJECT_ROOT / "scripts/ops/check_db_connection.py")])

    cmd = [
        sys.executable,
        str(BACKTEST_SCRIPT),
        "--risk-profile",
        "adaptive",
        "--start-date",
        args.start_date,
        "--strategies",
        args.strategies,
        "--initial-cash",
        str(args.initial_cash),
        "--position-ratio",
        str(args.position_ratio),
        "--top-n",
        str(args.top_n),
        "--hold-days",
        str(args.hold_days),
        "--max-total-positions",
        str(args.max_total_positions),
    ]
    if args.end_date:
        cmd.extend(["--end-date", args.end_date])
    backtest_report = _extract_json(_run(cmd))
    files = backtest_report.get("files") if isinstance(backtest_report.get("files"), dict) else {}
    output_dir = backtest_report.get("output_dir") or (str(Path(files["json"]).parent) if files.get("json") else None)
    worst_report = None
    if output_dir:
        worst_report = _extract_json(
            _run(
                [
                    sys.executable,
                    str(WORST_CASE_SCRIPT),
                    "--backtest-dir",
                    str(output_dir),
                    "--strategy",
                    "production_governed_vol_position",
                ]
            )
        )
    print(
        json.dumps(
            {
                "strategy": "production_governed_vol_position",
                "definition": "baseline_full_liquidity_detail_vol_position + adaptive_market_style v2.2 risk anchor + production risk-governor audit validation",
                "backtest": backtest_report,
                "worst_case_analysis": worst_report,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
