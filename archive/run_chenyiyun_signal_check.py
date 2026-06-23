"""Run daily chenyiyunSelected signal check."""

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

from project_network import build_direct_network_env, enforce_direct_network

enforce_direct_network()


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
    parser = argparse.ArgumentParser(description="09:05 daily signal check for chenyiyunSelected")
    parser.add_argument("--date", default=None, help="YYYYMMDD or YYYY-MM-DD")
    args, passthrough = parser.parse_known_args()

    date_iso = _normalize_date(args.date)
    project_root = PROJECT_ROOT
    runner = project_root / "scripts" / "ops" / "run_chenyiyun_daily.py"
    cmd = [sys.executable, str(runner), "--emit-signals"]
    if date_iso:
        cmd.extend(["--date", date_iso])
    cmd.extend(passthrough)
    subprocess.run(
        cmd,
        cwd=str(project_root),
        env=build_direct_network_env(os.environ, pythonpath_prefix=str(project_root)),
        check=True,
    )


if __name__ == "__main__":
    main()
