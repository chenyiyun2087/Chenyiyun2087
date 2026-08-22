#!/usr/bin/env python3
"""Launchd entrypoint for the dedicated task worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
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
    from scripts.ops.release_runtime import apply_runtime_release_environment

    release = apply_runtime_release_environment()
    os.chdir(release.project_root)
    python = str(release.runtime_python)
    worker_script = release.project_root / "scripts" / "ops" / "task_queue_worker.py"
    if not worker_script.is_file():
        raise SystemExit(f"FATAL: release worker is missing: {worker_script}")
    os.execvpe(python, [python, str(worker_script)], os.environ)


if __name__ == "__main__":
    main()
