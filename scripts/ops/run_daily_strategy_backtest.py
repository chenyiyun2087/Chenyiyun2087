"""Daily trusted strategy account backtest runner.

Runs the production account-level backtest for the active strategy suite
and writes results into the signal-research directory where the downstream
performance-review task can discover and report on them.

Scheduled: nightly at 21:15 (after scoring pipeline, before performance review).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BACKTEST_SCRIPT = PROJECT_ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"

# Fixed start date providing enough history for risk-governor lookback windows
# (252-day champion-score percentile, 63-day recent-champion window).
FIXED_START_DATE = "2025-09-01"

# Strategies that do NOT require corporate-action / lifecycle snapshots.
# The raw-ledger production strategy is reviewed through the fallback chain until
# strict snapshots are available to the daily runner.
DAILY_STRATEGIES = (
    "production_governed_vol_position_v1_2b_dynamic_score,"
    "production_governed_vol_position_v1_2b_execution_safe_uplift,"
    "adaptive_market_style"
)


def _ensure_db_password() -> str:
    """Return the database password from the environment, failing early if unset."""
    password = os.environ.get("CHENYIYUN_DB_PASSWORD", "")
    if not password:
        print("FATAL: CHENYIYUN_DB_PASSWORD environment variable is not set", file=sys.stderr)
        sys.exit(1)
    return password


def _build_env() -> dict[str, str]:
    """Build a subprocess environment with direct-network settings and DB credentials."""
    env = os.environ.copy()
    env.setdefault("CHENYIYUN_DB_PASSWORD", _ensure_db_password())
    # Strip proxy variables so the subprocess connects directly to local MySQL.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    return env


def run(args: argparse.Namespace) -> int:
    """Execute the daily backtest and return the subprocess exit code."""
    password = _ensure_db_password()
    end_date = args.date
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    cmd = [
        sys.executable,
        str(BACKTEST_SCRIPT),
        "--risk-profile", "adaptive",
        "--start-date", FIXED_START_DATE,
        "--end-date", end_date,
        "--strategies", DAILY_STRATEGIES,
        "--initial-cash", "500000.0",
        "--position-ratio", "0.7",
        "--top-n", "5",
        "--hold-days", "10",
        "--max-total-positions", "5",
        "--trade-cost-rate", "0.00075",
        "--slippage-rate", "0.0",
        "--min-pool-size", "5000",
    ]

    print(f"[{datetime.now().isoformat()}] Starting daily strategy backtest: {FIXED_START_DATE} → {end_date}")
    print(f"[cmd] {' '.join(cmd)}")

    env = _build_env()
    # Ensure the password is available to the subprocess.
    env["CHENYIYUN_DB_PASSWORD"] = password

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=False,
        text=True,
    )
    exit_code = result.returncode
    status = "SUCCESS" if exit_code == 0 else f"FAILED (exit {exit_code})"
    print(f"[{datetime.now().isoformat()}] Daily strategy backtest: {status}")
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily trusted strategy account backtest.")
    parser.add_argument(
        "--date",
        default=None,
        help="End date for the backtest window in YYYYMMDD or YYYY-MM-DD format (default: today).",
    )
    args = parser.parse_args()
    exit_code = run(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
