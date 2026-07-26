"""Fail-fast validation shared by the Web console and batch worker."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PYTHON = (PROJECT_ROOT / ".venv" / "bin" / "python").resolve()
REQUIRED_MODULES = ("pandas", "pymysql", "sqlalchemy", "yaml")
MIN_PYTHON = (3, 11)


def collect_runtime_issues(*, require_database: bool = True) -> list[str]:
    issues: list[str] = []
    current_python = tuple(sys.version_info[:2])
    if current_python < MIN_PYTHON:
        issues.append(
            f"unsupported_python:{current_python[0]}.{current_python[1]}; "
            f"minimum:{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
        )
    executable = Path(sys.executable).resolve()
    if executable != PROJECT_PYTHON:
        issues.append(f"wrong_python:{executable}; expected:{PROJECT_PYTHON}")
    for module in REQUIRED_MODULES:
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            issues.append(f"missing_module:{module}")
    if require_database and not (os.getenv("CHENYIYUN_DB_URL") or os.getenv("CHENYIYUN_DB_PASSWORD")):
        issues.append("missing_database_credentials")
    return issues


def assert_runtime(*, require_database: bool = True) -> None:
    issues = collect_runtime_issues(require_database=require_database)
    if issues:
        raise RuntimeError("runtime preflight failed: " + " | ".join(issues))
