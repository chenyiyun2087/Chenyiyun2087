#!/usr/bin/env python3
"""Launch the Web task center from launchd without embedding credentials."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(os.environ.get("CHENYIYUN_ENV_FILE", "~/.config/chenyiyun/web.env")).expanduser()


def load_env(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"FATAL: missing {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> None:
    load_env(ENV_FILE)
    if not (os.environ.get("CHENYIYUN_DB_URL") or os.environ.get("CHENYIYUN_DB_PASSWORD")):
        raise SystemExit(f"FATAL: database credentials are missing from {ENV_FILE}")
    os.chdir(PROJECT_ROOT)
    os.execvpe(
        sys.executable,
        [
            sys.executable,
            "-m",
            "flask",
            "--app",
            "web.app",
            "run",
            "--host",
            "0.0.0.0",
            "--port",
            "5001",
            "--no-debugger",
            "--no-reload",
        ],
        os.environ,
    )


if __name__ == "__main__":
    main()
