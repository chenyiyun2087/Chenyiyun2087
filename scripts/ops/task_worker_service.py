"""Worker-owned task state and subprocess primitives.

Web may enqueue and display jobs, but retry classification and execution
identity belong to the worker service so Web restarts cannot redefine them.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from collections.abc import Callable


TRANSIENT_FAILURE_MARKERS = (
    "deadlock", "lock wait timeout", "connection reset", "connection refused",
    "lost connection", "server has gone away", "temporarily unavailable",
    "timed out", "timeout", "(1205", "(1213", "(2006", "(2013",
    "too many open files", "errno 24", "emfile",
)
DATA_READINESS_FAILURE_MARKERS = (
    "qfq 在", "无数据，检查导入或日期对齐", "loading data for 0 stocks",
    "expected_stocks=0", "adjust_factor_coverage", "prescoregate: blocked",
    # Post-close producers can finish after the scheduled slot. These are
    # fail-closed verification messages, but the missing same-day input is
    # safe to retry while the upstream loader catches up.
    "data_quality: no bars",
    "data_quality: zero rows for",
    "no bars for ",
    "latest available",
    "stale-date substitution is forbidden",
    "same_day_snapshot",
    "same_day_collection_eligible",
    "waiting_same_day_complete_snapshot",
)


@dataclass(frozen=True)
class TaskExecutionIdentity:
    run_id: str
    release_id: str
    task_name: str
    business_date: str
    queue_id: int
    attempt: int


@dataclass(frozen=True)
class TaskProcessResult:
    exit_code: int
    stdout: str
    stderr: str


def _tail(path: str, max_chars: int) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read()[-max_chars * 4:]
        return data.decode("utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


def execute_subprocess(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    heartbeat: Callable[[], object],
    heartbeat_interval_seconds: int = 20,
) -> TaskProcessResult:
    """Execute one task with durable heartbeat callbacks and bounded logs."""
    stdout_path = stderr_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb+", delete=False, prefix="task_", suffix=".stdout.log") as stdout_handle, tempfile.NamedTemporaryFile(mode="wb+", delete=False, prefix="task_", suffix=".stderr.log") as stderr_handle:
            stdout_path, stderr_path = stdout_handle.name, stderr_handle.name
            process = subprocess.Popen(
                command, stdout=stdout_handle, stderr=stderr_handle,
                cwd=str(cwd), env=dict(env),
                close_fds=True,
            )
            while True:
                try:
                    process.wait(timeout=heartbeat_interval_seconds)
                    break
                except subprocess.TimeoutExpired:
                    heartbeat()
        return TaskProcessResult(
            exit_code=int(process.returncode or 0),
            stdout=_tail(stdout_path, 4000),
            stderr=_tail(stderr_path, 2400),
        )
    finally:
        for path in (stdout_path, stderr_path):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


def build_task_identity(job: Mapping[str, object], run_options: Mapping[str, object] | None = None) -> TaskExecutionIdentity:
    options = dict(run_options or {})
    queue_id = int(job.get("id") or 0)
    if queue_id <= 0:
        raise ValueError("task identity requires persisted queue id")
    task_name = str(job.get("task_name") or "")
    business_date = str(job.get("business_date") or "")
    if not task_name or len(business_date) != 8 or not business_date.isdigit():
        raise ValueError("task identity is incomplete")
    raw = f"task:{queue_id}:{task_name}:{business_date}"
    run_id = raw if len(raw) <= 128 else f"task:{queue_id}:{hashlib.sha256(raw.encode()).hexdigest()}"
    return TaskExecutionIdentity(
        run_id=run_id,
        release_id=str(options.get("release_id") or job.get("release_id") or "UNASSIGNED_BLOCKED"),
        task_name=task_name,
        business_date=business_date,
        queue_id=queue_id,
        attempt=int(job.get("attempt_count") or 0),
    )


def classify_task_failure(history_status: str, exit_code: int, message: object) -> tuple[str | None, bool]:
    body = str(message or "").lower()
    if str(history_status).lower() == "success":
        return None, False
    if "modulenotfounderror" in body or "missing_module" in body:
        return "DEPENDENCY", False
    if "usage:" in body or "unrecognized arguments" in body or int(exit_code or 0) == 2:
        return "ARGUMENT", False
    if "failed test" in body or "pytest" in body or "assertionerror" in body:
        return "TEST_GATE", False
    if any(marker.lower() in body for marker in DATA_READINESS_FAILURE_MARKERS):
        return "DATA_READINESS", True
    if "[verify]" in body and ("result=fail" in body or "result=blocked" in body):
        return "VERIFICATION", False
    if any(marker in body for marker in TRANSIENT_FAILURE_MARKERS):
        return "TRANSIENT", True
    if int(exit_code or 0) < 0:
        return "PROCESS", True
    return str(history_status or "FAILED").upper(), False


def record_task_evidence(
    job: Mapping[str, object],
    *,
    history_status: str,
    exit_code: int,
    message: object,
) -> str:
    """Persist a small immutable completion manifest and return its SHA."""
    from runtime.evidence_store import EvidenceStore

    identity = build_task_identity(job)
    payload = {
        "schema_version": "task_evidence_v1",
        "run_id": identity.run_id,
        "release_id": identity.release_id,
        "task_name": identity.task_name,
        "business_date": identity.business_date,
        "queue_id": identity.queue_id,
        "attempt": identity.attempt,
        "status": str(history_status).upper(),
        "exit_code": int(exit_code),
        "message_sha": hashlib.sha256(str(message or "").encode()).hexdigest(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    return EvidenceStore().put_json(
        payload,
        release_id=identity.release_id,
        run_id=identity.run_id,
        coverage_start=identity.business_date,
        coverage_end=identity.business_date,
    ).sha256
