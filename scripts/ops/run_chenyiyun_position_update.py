"""Update live position prices and snapshot for chenyiyun account."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
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


def _run_live_tracker(subcmd: str, date_iso: str | None, project_root: Path) -> None:
    runner = project_root / "sina" / "live_tracker" / "run_live_tracker.py"
    cmd = [sys.executable, str(runner), subcmd]
    if date_iso:
        cmd.extend(["--date", date_iso])
    subprocess.run(cmd, cwd=str(project_root), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="21:10 position info update task")
    parser.add_argument("--date", default=None, help="YYYYMMDD or YYYY-MM-DD")
    args = parser.parse_args()

    date_iso = _normalize_date(args.date)
    project_root = Path(__file__).resolve().parents[2]
    _run_live_tracker("sync", date_iso, project_root)
    _run_live_tracker("snapshot", date_iso, project_root)


if __name__ == "__main__":
    main()
