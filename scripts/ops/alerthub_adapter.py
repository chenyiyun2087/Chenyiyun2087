"""AlertHub Adapter for Chenyiyun (CYY) Platform.

Responsibilities:
1. Standardizes message delivery through AlertHub REST API (v1.0).
2. Classifies notifications into two main categories:
   - 批量任务 (Batch Tasks): Only for failed batch tasks, emphasizing emit time,
     task name, error classification/name, and error summary.
   - 业务 (Business): Emphasizing business outcomes (strategy performance, orders,
     portfolio risk, execution metrics) while omitting low-level task execution details.
3. Automatically enforces the 【CYY】 prefix on all outgoing messages.
4. Provides fail-safe local disk buffering if AlertHub is temporarily unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

logger = logging.getLogger("chenyiyun.alerthub_adapter")

# Environment & Default Configuration
DEFAULT_ALERTHUB_URL = "http://127.0.0.1:8090"
DEFAULT_ALERTHUB_API_KEY = "cyy-secret-key-123456"
DEFAULT_ALERTHUB_PROJECT_ID = "cyy"


def get_alerthub_url() -> str:
    return str(os.getenv("ALERTHUB_URL") or DEFAULT_ALERTHUB_URL).rstrip("/")


def get_alerthub_api_key() -> str:
    return str(os.getenv("ALERTHUB_API_KEY") or DEFAULT_ALERTHUB_API_KEY).strip()


def get_alerthub_project_id() -> str:
    return str(os.getenv("ALERTHUB_PROJECT_ID") or DEFAULT_ALERTHUB_PROJECT_ID).strip()


def is_alerthub_enabled() -> bool:
    val = os.getenv("ALERTHUB_ENABLED", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "disable", "disabled")


def ensure_cyy_prefix(title: str) -> str:
    """Ensure message title is prefixed with 【CYY】."""
    clean = str(title or "").strip()
    if not clean:
        return "【CYY】系统通知"
    if clean.startswith("【CYY】") or clean.startswith("[CYY]"):
        return clean
    return f"【CYY】{clean}"


def classify_notification(
    event_type: str,
    notification_type: str | None = None,
    task_name: str | None = None,
) -> str:
    """Classify notification into 'batch_task' or 'business'."""
    ev = str(event_type or "").lower()
    notif = str(notification_type or "").lower()

    # Batch tasks: any task lifecycle event or batch audit
    if (
        ev.startswith("task_")
        or notif.startswith("task_")
        or ev in ("scheduler_enqueue_failed", "ops_daily_batch_audit_incident", "batch_task", "daily_batch_audit")
        or notif in ("scheduler_enqueue_failed", "ops_daily_batch_audit_incident", "batch_task", "daily_batch_audit")
    ):
        return "batch_task"

    # Business categories: strategy orders, performance review, risk health, shadow, etc.
    return "business"


def is_batch_task_notification_allowed(event_type: str, history_status: str | None = None) -> bool:
    """Enforce requirement: Batch task notifications ONLY for execution failures."""
    ev = str(event_type or "").lower()
    st = str(history_status or "").upper()

    if ev in ("task_recovered", "task_retrying") or st in ("SUCCESS", "SKIPPED"):
        return False
    return True


def format_batch_task_message(
    task_name: str,
    error_name: str,
    error_detail: str,
    occurred_at: datetime | None = None,
    business_date: str | None = None,
    failure_stage: str | None = None,
) -> str:
    """Format focused failure message for batch tasks."""
    dt = occurred_at or datetime.now()
    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"**发出时间**：{time_str}",
        f"**任务名称**：{task_name}",
        f"**错误名称**：`{error_name}`",
    ]
    if business_date:
        lines.append(f"**业务日期**：{business_date}")
    if failure_stage:
        lines.append(f"**失败阶段**：{failure_stage}")

    clean_detail = str(error_detail or "").strip()
    if clean_detail:
        lines.extend(["", "**错误详情**：", clean_detail[:1800]])

    return "\n".join(lines)


def clean_business_message(raw_text: str) -> str:
    """Remove low-level task execution details and keep business output."""
    lines = raw_text.splitlines()
    filtered: list[str] = []
    skip_prefixes = (
        "**任务：**",
        "**任务ID：**",
        "**队列ID：**",
        "**运行ID：**",
        "**事件ID：**",
        "**触发方式：**",
        "**尝试次数：**",
        "**下次重试：**",
        "**耗时：**",
        "**退出码：**",
        "**失败阶段：**",
        "任务：",
        "队列ID：",
        "耗时：",
    )
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(p) for p in skip_prefixes):
            continue
        filtered.append(line)

    return "\n".join(filtered).strip()


def save_to_local_buffer(payload: Mapping[str, Any], idempotency_key: str) -> Path | None:
    """Save alert event to local disk buffer (~/.alerthub/buffer) when server unreachable."""
    try:
        buffer_dir = Path.home() / ".alerthub" / "buffer"
        buffer_dir.mkdir(parents=True, exist_ok=True)
        filename = f"event_cyy_{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
        filepath = buffer_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"payload": payload, "idempotency_key": idempotency_key}, f, ensure_ascii=False)
        logger.info("Saved alert to local AlertHub buffer: %s", filepath)
        return filepath
    except Exception as exc:
        logger.warning("Failed to save to local AlertHub buffer: %s", exc)
        return None


def send_alerthub_event(
    title: str,
    message: str,
    *,
    category: str = "business",
    severity: str = "INFO",
    component: str = "cyy_platform",
    reason_code: str = "BUSINESS_OUTPUT",
    fingerprint: str | None = None,
    occurred_at: datetime | None = None,
    business_date: str | None = None,
    task_name: str | None = None,
    run_id: str | None = None,
    labels: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    timeout: int = 5,
) -> Tuple[bool, str, Optional[str]]:
    """Send an event to AlertHub REST API.

    Returns:
        (ok, reason, event_id)
    """
    if not is_alerthub_enabled():
        return False, "alerthub_disabled", None

    base_url = get_alerthub_url()
    api_key = get_alerthub_api_key()
    now = occurred_at or datetime.now(timezone.utc)
    occurred_iso = now.isoformat()

    final_title = ensure_cyy_prefix(title)
    final_fp = fingerprint or f"cyy:{category}:{component}:{reason_code}"
    src_id = f"cyy_{uuid.uuid4().hex[:12]}"
    idemp_key = idempotency_key or f"{final_fp}:{business_date or 'today'}"

    all_labels = {
        "category": category,
        "project": "cyy",
    }
    if business_date:
        all_labels["business_date"] = str(business_date)
    if task_name:
        all_labels["task_name"] = str(task_name)
    if labels:
        all_labels.update(labels)

    all_metadata = dict(metadata or {})
    if business_date:
        all_metadata["business_date"] = business_date
    if run_id:
        all_metadata["run_id"] = run_id

    # Normalization of severity for AlertHub
    sev_upper = severity.upper()
    if sev_upper not in ("P0", "P1", "P2", "INFO"):
        sev_map = {
            "CRITICAL": "P0",
            "RED": "P0",
            "ERROR": "P1",
            "WARNING": "P2",
            "YELLOW": "P2",
            "SUCCESS": "INFO",
            "GREEN": "INFO",
        }
        sev_upper = sev_map.get(sev_upper, "INFO")

    payload = {
        "source_event_id": src_id,
        "severity": sev_upper,
        "status": "OPEN",
        "component": component[:64],
        "environment": "production",
        "host": socket.gethostname(),
        "reason_code": reason_code[:64],
        "title": final_title[:255],
        "message": message,
        "fingerprint": final_fp[:255],
        "occurred_at": occurred_iso,
        "run_id": (run_id[:128] if run_id else None),
        "job_key": (task_name[:128] if task_name else None),
        "labels": all_labels,
        "metadata": all_metadata,
    }

    url = f"{base_url}/api/v1/events"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Idempotency-Key": idemp_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
            if 200 <= code < 300:
                try:
                    res_json = json.loads(body)
                    event_id = res_json.get("event_id")
                    return True, "ok", event_id
                except Exception:
                    return True, "ok", None
            return False, f"http_status={code}; body={body[:200]}", None
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        logger.warning("AlertHub HTTPError %s: %s", exc.code, err_body[:200])
        save_to_local_buffer(payload, idemp_key)
        return False, f"http_error={exc.code}; body={err_body[:200]}", None
    except Exception as exc:
        logger.warning("AlertHub connection failure: %s", exc)
        save_to_local_buffer(payload, idemp_key)
        return False, f"connection_error={exc}", None
