"""Fail-fast validation shared by the Web console and batch worker."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from scripts.ops.release_runtime import RUNTIME_STATUS_EXCLUDES


PROJECT_ROOT = Path(
    os.environ.get("CHENYIYUN_PROJECT_ROOT") or Path(__file__).resolve().parents[2]
).expanduser().resolve()
PROJECT_PYTHON = Path(
    os.environ.get("CHENYIYUN_RUNTIME_PYTHON")
    or (PROJECT_ROOT / ".venv" / "bin" / "python")
).expanduser().resolve()
REQUIRED_MODULES = (
    "pandas",
    "pymysql",
    "sqlalchemy",
    "yaml",
    "selenium",
    "webdriver_manager",
    "cv2",
    "pytesseract",
    "PIL",
)
MIN_PYTHON = (3, 11)
GIT_STATUS_PATHS = (".", *RUNTIME_STATUS_EXCLUDES)


def _release_required() -> bool:
    return os.environ.get("CHENYIYUN_REQUIRE_RELEASE") == "1" or os.environ.get(
        "CHENYIYUN_RUNTIME_ROLE"
    ) == "worker"


def _collect_release_issues() -> list[str]:
    from scripts.ops.release_runtime import load_runtime_release

    issues: list[str] = []
    try:
        release = load_runtime_release()
    except RuntimeError as exc:
        return [str(exc)]

    if release.project_root != PROJECT_ROOT:
        issues.append(
            f"release_root_mismatch:{release.project_root}; expected:{PROJECT_ROOT}"
        )
    expected_sha = os.environ.get("CHENYIYUN_RELEASE_SHA", "").strip().lower()
    if expected_sha and expected_sha != release.commit_sha:
        issues.append("release_manifest_sha_env_mismatch")
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip().lower()
        if head != release.commit_sha:
            issues.append(f"release_commit_mismatch:{head}; expected:{release.commit_sha}")
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *GIT_STATUS_PATHS],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        )
        if status.strip():
            issues.append("release_worktree_dirty")
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(f"release_git_unavailable:{type(exc).__name__}")

    pipeline_path = PROJECT_ROOT / "task_registry" / "pipeline.yaml"
    if not pipeline_path.is_file():
        issues.append("pipeline_registry_missing")
    else:
        try:
            import yaml

            pipeline = yaml.safe_load(pipeline_path.read_text(encoding="utf-8")) or {}
            seen: set[str] = set()
            for group_name, group in pipeline.items():
                for task in (group or {}).get("tasks", []):
                    task_id = str(task.get("id") or "")
                    if task_id in seen:
                        issues.append(f"pipeline_duplicate_task:{task_id}")
                    if task_id:
                        seen.add(task_id)
                    if task.get("status") != "enabled":
                        continue
                    script = str(task.get("script") or "")
                    if not script or not (PROJECT_ROOT / script).is_file():
                        issues.append(f"pipeline_script_missing:{group_name}:{task_id}:{script}")
        except Exception as exc:  # fail closed: registry parse is a release gate
            issues.append(f"pipeline_registry_invalid:{type(exc).__name__}")
    return issues


def _check_database() -> str | None:
    try:
        from scoreRank.core.db_config import build_pymysql_config
        from scoreRank.core.db_runtime import connect_pymysql
    except ImportError as exc:
        return f"database_client_import_failed:{type(exc).__name__}"
    try:
        conn = connect_pymysql(build_pymysql_config())
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone() or {}
                if isinstance(row, dict):
                    ok = row.get("ok")
                else:
                    ok = row[0] if row else None
                if int(ok or 0) != 1:
                    return "database_probe_failed"
        finally:
            conn.close()
    except Exception as exc:  # do not include connection strings in output
        return f"database_unreachable:{type(exc).__name__}"
    return None


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
    if _release_required():
        issues.extend(_collect_release_issues())
    if require_database and not any(issue.startswith("missing_database_credentials") for issue in issues):
        database_issue = _check_database()
        if database_issue:
            issues.append(database_issue)
    return issues


def assert_runtime(*, require_database: bool = True) -> None:
    issues = collect_runtime_issues(require_database=require_database)
    if issues:
        raise RuntimeError("runtime preflight failed: " + " | ".join(issues))
