#!/usr/bin/env python3
"""Launchd entrypoint for the dedicated task worker."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(os.environ.get("CHENYIYUN_ENV_FILE", "~/.config/chenyiyun/web.env")).expanduser()
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


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
    os.chdir(PROJECT_ROOT)
    python = str(VENV_PYTHON)
    os.execvpe(python, [python, "scripts/ops/task_queue_worker.py"], os.environ)


if __name__ == "__main__":
    main()
