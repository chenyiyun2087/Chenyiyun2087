from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_pymysql_config


DB_CONFIG = build_pymysql_config()
RUN_ROOT = PROJECT_ROOT / "exports" / "bs_signal_cycles"


def _parse_date(raw: str | None) -> date:
    if not raw:
        return datetime.now().date()
    text = str(raw).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"Invalid date: {raw}")
    return datetime.strptime(text, "%Y%m%d").date()


def _read_one(sql: str, params: tuple) -> dict | None:
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()
    finally:
        conn.close()


def _is_first_trading_day_of_month(target: date) -> tuple[bool, str]:
    ymd = target.strftime("%Y%m%d")
    month_start = target.replace(day=1).strftime("%Y%m%d")
    row = _read_one(
        """
        SELECT
          MAX(CASE WHEN cal_date = %s THEN is_open ELSE NULL END) AS target_is_open,
          MIN(CASE WHEN is_open = 1 THEN cal_date ELSE NULL END) AS first_open_date
        FROM chenyiyun.dim_trade_cal
        WHERE exchange = 'SSE'
          AND cal_date >= %s
          AND cal_date <= %s
        """,
        (ymd, month_start, ymd),
    )
    if not row:
        return False, "trade_calendar_missing"
    target_is_open = int(row.get("target_is_open") or 0) == 1
    first_open_date = str(row.get("first_open_date") or "")
    if not target_is_open:
        return False, "non_trading_day"
    if first_open_date != ymd:
        return False, f"not_first_trading_day:first_open={first_open_date or 'unknown'}"
    return True, "first_trading_day"


def _completed_cycle_exists(target: date) -> tuple[bool, str | None]:
    month_prefix = target.strftime("%Y-%m")
    for manifest_path in sorted(RUN_ROOT.glob("20*/cycle_manifest.json"), reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        generated_at = str(manifest.get("generated_at") or "")
        if generated_at.startswith(month_prefix) and manifest.get("status") == "completed":
            return True, str(manifest_path.parent)
    return False, None


def _run_cycle(args: argparse.Namespace) -> tuple[dict, str]:
    cmd = [
        sys.executable,
        "scripts/run_bs_signal_enhancement_cycle.py",
        "--target",
        args.target,
        "--model-kind",
        "all",
        "--deploy-model-kind",
        args.deploy_model_kind,
        "--deploy-metric",
        args.deploy_metric,
        "--capital-per-trade",
        str(args.capital_per_trade),
        "--portfolio-capital",
        str(args.portfolio_capital),
        "--capacity-ratio",
        str(args.capacity_ratio),
        "--top-n",
        str(args.top_n),
    ]
    if args.risk_target:
        cmd.extend(["--risk-target", args.risk_target])
    if args.skip_import:
        cmd.append("--skip-import")
    if args.skip_research:
        cmd.append("--skip-research")
    if args.skip_reports:
        cmd.append("--skip-reports")
    if args.skip_check:
        cmd.append("--skip-check")
    if args.skip_tests:
        cmd.append("--skip-tests")

    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Monthly cycle failed ({proc.returncode}): {' '.join(cmd)}\n{output}")

    parsed = {}
    lines = proc.stdout.splitlines()
    for line_no in range(len(lines)):
        candidate = "\n".join(lines[line_no:]).strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    return parsed, output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run B-signal enhancement cycle once per month on the first trading day.")
    parser.add_argument("--date", default=None, help="Target date, YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--force", action="store_true", help="Run even when it is not the first trading day or a cycle already exists.")
    parser.add_argument("--target", default="hit_20_10pct")
    parser.add_argument("--risk-target", default=None)
    parser.add_argument("--deploy-model-kind", default="auto")
    parser.add_argument("--deploy-metric", default="precision_at_20")
    parser.add_argument("--capital-per-trade", type=float, default=100000.0)
    parser.add_argument("--portfolio-capital", type=float, default=1000000.0)
    parser.add_argument("--capacity-ratio", type=float, default=0.02)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--skip-reports", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    target = _parse_date(args.date)
    due, reason = _is_first_trading_day_of_month(target)
    exists, existing_run_dir = _completed_cycle_exists(target)
    if not args.force and (not due or exists):
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "target_date": target.isoformat(),
                    "reason": "already_completed_this_month" if exists else reason,
                    "existing_run_dir": existing_run_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    cycle_summary, _ = _run_cycle(args)
    print(
        json.dumps(
            {
                "status": "completed",
                "target_date": target.isoformat(),
                "reason": "forced" if args.force else reason,
                "cycle": cycle_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
