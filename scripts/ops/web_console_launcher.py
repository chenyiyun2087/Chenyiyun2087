#!/usr/bin/env python3
"""Launch the Web task center from launchd without embedding credentials."""

from __future__ import annotations

import os
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
PROJECT_ROOT = SOURCE_ROOT
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
    from scripts.ops.release_runtime import apply_runtime_release_environment

    release = apply_runtime_release_environment()
    os.environ["CHENYIYUN_RUNTIME_ROLE"] = "web"
    if not (os.environ.get("CHENYIYUN_DB_URL") or os.environ.get("CHENYIYUN_DB_PASSWORD")):
        raise SystemExit(f"FATAL: database credentials are missing from {ENV_FILE}")
    os.chdir(release.project_root)
    VENV_PYTHON = release.runtime_python
    python = str(VENV_PYTHON)
    os.execvpe(
        python,
        [
            python,
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
