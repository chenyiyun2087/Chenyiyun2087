"""Run Monday-only weekly rebalance for chenyiyunSelected."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(description="09:30 Monday weekly rebalance task")
    parser.add_argument("--date", default=None, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="run even when date is not Monday")
    args, passthrough = parser.parse_known_args()

    date_iso = _normalize_date(args.date)
    target_date = datetime.strptime(date_iso, "%Y-%m-%d").date() if date_iso else date.today()
    if target_date.weekday() != 0 and not args.force:
        print(f"Skip weekly rebalance: {target_date} is not Monday.")
        return

    project_root = Path(__file__).resolve().parents[2]
    runner = project_root / "scripts" / "ops" / "run_chenyiyun_daily.py"
    cmd = [sys.executable, str(runner), "--emit-signals"]
    if date_iso:
        cmd.extend(["--date", date_iso])
    cmd.extend(passthrough)
    subprocess.run(cmd, cwd=str(project_root), check=True)


if __name__ == "__main__":
    main()
