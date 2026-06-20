r"""Execute a rolling out-of-sample reliability matrix across multiple strategy variants.

Each cell runs the trusted strategy account backtest with fixed
development -> holdout evaluation, then advances the window.

Grid dimensions (per window):
  - Strategies: production_governed_vol_position, v1_2b_gate_tuned, v1_2b_strict_precommit_uplift
  - Trade cost rates: 0.00075, 0.001, 0.0015
  - Slippage bps: 0, 10, 25
  - Cap profiles: no_cap, extreme_only, high_v1_plus_5pct, strict_cap

Rolling windows: 4 positions, each (12-month dev → 3-month holdout, step 3 months)

Total: 4 windows × 3 strategies × 3 costs × 3 slippage × 4 caps = 432 cells (max)

Output:
  exports/rolling_oos_matrix/{timestamp}/
    windows.json
    rolling_oos_matrix_result.json
    rolling_oos_dashboard.md
    001_.../run.log ...
"""

from __future__ import annotations

import argparse
import json as json_module
import subprocess
import sys
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STRATEGIES: tuple[str, ...] = (
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
)
COSTS: tuple[float, ...] = (0.00075, 0.001, 0.0015)
SLIPPAGE_BPS: tuple[int, ...] = (0, 10, 25)
CAPS: tuple[str, ...] = ("no_cap", "extreme_only", "high_v1_plus_5pct", "strict_cap")

DEV_MONTHS: int = 12
HOLDOUT_MONTHS: int = 3
STEP_MONTHS: int = 3


def _add_months(dt: datetime, months: int) -> datetime:
    """Add months to a datetime, handling year rollover and day clamping."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(dt.day, days_in_month[month - 1])
    return dt.replace(year=year, month=month, day=day)


def build_rolling_windows(base_end: datetime, count: int = 4) -> list[dict[str, str]]:
    """Build rolling dev/holdout window definitions.

    Each window:
      dev_period: [base_end - HOLDOUT - STEP*pos - DEV, base_end - HOLDOUT - STEP*pos]
      holdout:    [dev_end + 1d, dev_end + HOLDOUT]
    """
    windows: list[dict[str, str]] = []
    for pos in range(count):
        dev_end_raw = _add_months(base_end, -(HOLDOUT_MONTHS + STEP_MONTHS * pos))
        dev_start_raw = _add_months(dev_end_raw, -DEV_MONTHS)
        holdout_end_raw = _add_months(dev_end_raw, HOLDOUT_MONTHS)

        windows.append({
            "position": str(pos),
            "label": (
                f"W{pos}_dev{dev_start_raw.strftime('%Y%m%d')}"
                f"_hold{holdout_end_raw.strftime('%Y%m%d')}"
            ),
            "dev_start": dev_start_raw.strftime("%Y-%m-%d"),
            "dev_end": dev_end_raw.strftime("%Y-%m-%d"),
            "holdout_start": dev_end_raw.strftime("%Y-%m-%d"),
            "holdout_end": holdout_end_raw.strftime("%Y-%m-%d"),
        })
    return windows


def generate_run_configs(windows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Generate all run configurations (windows × strategies × costs × slippage × caps)."""
    configs: list[dict[str, Any]] = []
    for window in windows:
        for strategy, cost, slip, cap in product(STRATEGIES, COSTS, SLIPPAGE_BPS, CAPS):
            configs.append({
                "window_label": window["label"],
                "dev_start": window["dev_start"],
                "dev_end": window["dev_end"],
                "holdout_start": window["holdout_start"],
                "holdout_end": window["holdout_end"],
                "strategy": strategy,
                "trade_cost_rate": cost,
                "additional_open_slippage_bps": slip,
                "cap_profile": cap,
            })
    return configs


def build_command(run_config: dict[str, Any]) -> list[str]:
    """Build the subprocess command for a single backtest cell."""
    return [
        sys.executable,
        str(ROOT / "scripts" / "research_trusted_strategy_account_backtest.py"),
        "--risk-profile", "adaptive",
        "--strategies", run_config["strategy"],
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", run_config["dev_start"],
        "--end-date", run_config["dev_end"],
        "--trade-cost-rate", str(run_config["trade_cost_rate"]),
        "--slippage-rate", str(run_config["additional_open_slippage_bps"] / 10_000),
        "--strict-cap-profile", run_config["cap_profile"],
        "--holdout-start", run_config["holdout_start"],
        "--holdout-end", run_config["holdout_end"],
    ]


def build_dashboard_md(
    summary: dict[str, Any],
    run_configs: list[dict[str, Any]],
) -> str:
    """Build a Markdown dashboard from rolling OOS results."""
    lines = [
        "# Rolling OOS Reliability Matrix",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total cells: {summary['total_runs']}",
        f"Success: {summary['success_count']}",
        f"Failed: {summary['failed_count']}",
        "",
        "## Grid Dimensions",
        "",
        f"- Strategies: {', '.join(STRATEGIES)}",
        f"- Cost rates: {COSTS}",
        f"- Slippage (bps): {SLIPPAGE_BPS}",
        f"- Cap profiles: {CAPS}",
        f"- Windows: {len(set(r['window_label'] for r in run_configs))}",
        "",
        "## Results Summary",
        "",
        "| Cell | Window | Strategy | Cost | Slip | Cap | Status |",
        "|------|--------|----------|------|------|-----|--------|",
    ]

    results: list[dict[str, Any]] = summary.get("results", [])
    for i, r in enumerate(results):
        lines.append(
            f"| {i + 1:03d} | {r.get('window_label', '')[:20]} "
            f"| {r.get('strategy', '')[:30]} "
            f"| {r.get('trade_cost_rate', '')} "
            f"| {r.get('additional_open_slippage_bps', '')} "
            f"| {r.get('cap_profile', '')} "
            f"| {r.get('status', '')} |"
        )

    failed = [r for r in results if r.get("status") == "FAILED"]
    if failed:
        lines.extend([
            "",
            "## Failed Cells",
            "",
        ])
        for f in failed:
            lines.append(
                f"- Cell {f.get('cell_dir', '?')}: "
                f"{f.get('window_label', '')} / {f.get('strategy', '')}"
            )

    lines.extend([
        "",
        "---",
        "",
        "⚠️ This report is research-only and does not modify production parameters.",
    ])

    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the rolling OOS matrix (or dry-run)."""
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    base_end = (
        datetime.strptime(args.base_end_date, "%Y-%m-%d")
        if args.base_end_date
        else datetime.now()
    )
    windows = build_rolling_windows(base_end, count=args.window_count)
    (output / "windows.json").write_text(
        json_module.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    all_configs = generate_run_configs(windows)
    if args.max_runs:
        all_configs = all_configs[: args.max_runs]

    print(
        f"Rolling OOS matrix: {len(all_configs)} cells "
        f"({len(windows)} windows × {len(STRATEGIES)} strategies "
        f"× {len(COSTS)} costs × {len(SLIPPAGE_BPS)} slippage × {len(CAPS)} caps)"
    )

    results: list[dict[str, Any]] = []
    for number, run_config in enumerate(all_configs, 1):
        cell_dir = output / f"{number:03d}_{run_config['window_label']}_{run_config['cap_profile']}"
        record: dict[str, Any] = {
            **run_config,
            "cell_number": number,
            "cell_dir": str(cell_dir),
            "command": build_command(run_config),
            "status": "NOT_RUN",
        }

        if args.dry_run:
            record["status"] = "DRY_RUN"
            results.append(record)
            continue

        cell_dir.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                record["command"],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=args.timeout_seconds,
            )
            record["returncode"] = proc.returncode
            record["status"] = "SUCCESS" if proc.returncode == 0 else "FAILED"
            (cell_dir / "run.log").write_text(
                proc.stdout + "\n" + proc.stderr, encoding="utf-8"
            )
        except subprocess.TimeoutExpired:
            record["status"] = "TIMEOUT"
            (cell_dir / "run.log").write_text(f"TIMEOUT after {args.timeout_seconds}s", encoding="utf-8")
        except Exception as exc:
            record["status"] = "ERROR"
            record["error"] = str(exc)
            (cell_dir / "run.log").write_text(f"ERROR: {exc}", encoding="utf-8")

        results.append(record)
        print(f"  [{number}/{len(all_configs)}] {record['status']} — {run_config['window_label']} {run_config['strategy'][:40]}")

    summary: dict[str, Any] = {
        "total_runs": len(results),
        "success_count": sum(1 for r in results if r["status"] == "SUCCESS"),
        "failed_count": sum(1 for r in results if r["status"] == "FAILED"),
        "timeout_count": sum(1 for r in results if r["status"] == "TIMEOUT"),
        "error_count": sum(1 for r in results if r["status"] == "ERROR"),
        "dry_run": args.dry_run,
        "window_count": len(windows),
        "grid_params": {
            "strategies": list(STRATEGIES),
            "costs": list(COSTS),
            "slippage_bps": list(SLIPPAGE_BPS),
            "caps": list(CAPS),
            "window_count": len(windows),
        },
        "results": results,
    }

    (output / "rolling_oos_matrix_result.json").write_text(
        json_module.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    dashboard_md = build_dashboard_md(summary, all_configs)
    (output / "rolling_oos_dashboard.md").write_text(dashboard_md, encoding="utf-8")

    print(f"\nDone. Results: {output}")
    print(f"  Success: {summary['success_count']}/{summary['total_runs']}")
    print(f"  Failed: {summary['failed_count']}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run rolling out-of-sample reliability matrix."
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            ROOT / "exports" / "rolling_oos_matrix" / datetime.now().strftime("%Y%m%d_%H%M%S")
        ),
        help="Output directory for matrix results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview commands without executing backtests.",
    )
    parser.add_argument(
        "--base-end-date",
        default=None,
        help="End date for the newest holdout window (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--window-count",
        type=int,
        default=4,
        help="Number of rolling windows (default: 4).",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Limit to first N cells (for testing).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
        help="Per-cell timeout in seconds (default: 7200 = 2 hours).",
    )
    _args = parser.parse_args()

    if _args.dry_run:
        print("[DRY RUN] No backtests will be executed.")
    else:
        print("[LIVE RUN] Backtests will execute with subprocess calls.")

    summary = run(_args)
    print(
        json_module.dumps(
            {
                "status": "DONE" if not _args.dry_run else "DRY_RUN",
                "total_runs": summary["total_runs"],
                "success_count": summary["success_count"],
                "failed_count": summary["failed_count"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
