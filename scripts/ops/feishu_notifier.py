"""Shared Feishu notification module for all production ops scripts.

Consolidates the duplicated _load_feishu_webhook / _send_feishu_text functions
that previously existed in three separate scripts. Also provides a standardized
strategy identity block for consistent governor/selection strategy display.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Mapping, Optional
from urllib import error, request

if TYPE_CHECKING:
    from sqlalchemy import Engine

from scripts.ops.production_config import load_production_config
from scripts.strategy_display import strategy_display_name


@dataclass(frozen=True)
class NotificationEvent:
    """Channel-neutral notification event used by production jobs."""

    event_type: str
    business_date: str
    title: str
    dedupe_key: str
    severity: str = "INFO"
    task_name: str | None = None
    run_id: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    facts: Mapping[str, Any] = field(default_factory=dict)
    details: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    occurred_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not str(self.event_type or "").strip():
            raise ValueError("event_type is required")
        if not str(self.business_date or "").strip():
            raise ValueError("business_date is required")
        if not str(self.title or "").strip():
            raise ValueError("title is required")
        if not str(self.dedupe_key or "").strip():
            raise ValueError("dedupe_key is required")


@dataclass(frozen=True)
class DeliveryResult:
    """Result returned by the unified publisher."""

    ok: bool
    status: str
    reason: str
    event_id: str
    channel_key: str = "feishu"
    deduped: bool = False
    queued: bool = False


def _get_text_sql():
    """Lazy import of sqlalchemy.text to avoid import errors in test environments."""
    from sqlalchemy import text as _text
    return _text


def load_feishu_webhook(engine: Engine) -> str | None:
    """Load Feishu webhook URL from env var FEISHU_WEBHOOK_URL or DB.

    Priority:
    1. Environment variable FEISHU_WEBHOOK_URL
    2. Database table app_notification_channel (channel_key='feishu', enabled=1)
    """
    env_url = str(os.environ.get("FEISHU_WEBHOOK_URL") or "").strip()
    if env_url.startswith(("http://", "https://")):
        return env_url

    text_fn = _get_text_sql()

    # Check if table exists before querying (avoid errors on fresh DB)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text_fn(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'chenyiyun' "
                    "AND table_name = 'app_notification_channel' LIMIT 1"
                )
            ).scalar()
        if not exists:
            return None
    except Exception:
        return None

    sql = text_fn(
        """
        SELECT webhook_url
        FROM chenyiyun.app_notification_channel
        WHERE channel_key = 'feishu'
          AND enabled = 1
          AND webhook_url IS NOT NULL
          AND TRIM(webhook_url) <> ''
        LIMIT 1
        """
    )
    try:
        with engine.connect() as conn:
            value = conn.execute(sql).scalar()
    except Exception:
        return None

    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else None


def send_feishu_text(webhook_url: str, content: str) -> tuple[bool, str]:
    """Send a Feishu text message. Returns (success, detail).

    TLS certificate errors are treated as hard failures — no insecure fallback.
    Callers must handle failures by logging, alerting, and retrying with backoff.
    """
    payload = json.dumps(
        {"msg_type": "text", "content": {"text": content}},
        ensure_ascii=False,
    ).encode("utf-8")

    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        body = (
            exc.read().decode("utf-8", errors="ignore")
            if hasattr(exc, "read")
            else str(exc)
        )
        return False, f"http_error={exc.code}; body={body[:200]}"
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, (
                f"TLS_CERTIFICATE_ERROR: certificate verification failed for "
                f"{webhook_url[:60]}... — refusing to send without verified TLS. "
                f"Check Feishu webhook certificate validity."
            )
        return False, f"url_error={exc}"
    except Exception as exc:
        return False, f"exception={exc}"

    if status < 200 or status >= 300:
        return False, f"http_status={status}; body={body[:200]}"

    try:
        parsed = json.loads(body) if body else {}
    except Exception:
        return True, "http_ok"

    if isinstance(parsed, dict):
        errcode = parsed.get("errcode")
        code = parsed.get("code")
        if errcode not in (None, 0, "0"):
            return False, f"errcode={errcode}; body={body[:200]}"
        if code not in (None, 0, "0"):
            return False, f"code={code}; body={body[:200]}"

    return True, "ok"


def _send_feishu_payload(webhook_url: str, payload: Mapping[str, Any]) -> tuple[bool, str]:
    """Send a pre-rendered Feishu payload with the same strict TLS policy."""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        return False, f"http_error={exc.code}; body={body[:200]}"
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return False, "TLS_CERTIFICATE_ERROR: certificate verification failed; refusing insecure fallback"
        return False, f"url_error={exc}"
    except Exception as exc:
        return False, f"exception={exc}"

    if status < 200 or status >= 300:
        return False, f"http_status={status}; body={body[:200]}"
    try:
        parsed = json.loads(body) if body else {}
    except Exception:
        return True, "http_ok"
    if isinstance(parsed, dict):
        for key in ("errcode", "code", "StatusCode"):
            if parsed.get(key) not in (None, 0, "0"):
                return False, f"{key}={parsed.get(key)}; body={body[:200]}"
    return True, "ok"


def build_feishu_interactive_payload(event: NotificationEvent) -> dict[str, Any]:
    """Render a typed event as a Feishu interactive card payload."""
    severity = str(event.severity or "INFO").upper()
    template = {
        "INFO": "blue", "SUCCESS": "green", "WARNING": "orange",
        "YELLOW": "orange", "ERROR": "red", "RED": "red", "CRITICAL": "red",
    }.get(severity, "blue")
    lines = [
        f"**业务日：** {event.business_date}",
        f"**事件类型：** {event.event_type}",
        f"**严重程度：** {severity}",
    ]
    if event.task_name:
        lines.append(f"**任务：** {event.task_name}")
    if event.run_id:
        lines.append(f"**运行ID：** {event.run_id}")
    lines.append(f"**事件ID：** {event.event_id}")
    for key, value in event.facts.items():
        lines.append(f"**{key}：** {value}")
    if event.details:
        lines.extend(["", "**详情**", *[f"- {item}" for item in event.details]])
    action_buttons = []
    action_notes = []
    for item in event.actions:
        label, separator, target = str(item).partition("|")
        if separator and target.startswith(("http://", "https://")):
            action_buttons.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": label[:40]},
                "type": "primary",
                "url": target,
            })
        else:
            action_notes.append(str(item))
    if action_notes:
        lines.extend(["", "**建议动作**", *[f"- {item}" for item in action_notes]])
    if event.artifact_paths:
        lines.extend(["", "**产物/日志**", *[f"- `{item}`" for item in event.artifact_paths]])
    lines.extend(["", f"发生时间：{event.occurred_at.isoformat(timespec='seconds')}"])
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "\n".join(lines)},
    ]
    if action_buttons:
        elements.append({"tag": "action", "actions": action_buttons[:3]})
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": event.title},
            },
            "body": {"elements": elements},
        },
    }


def _is_permanent_delivery_failure(reason: str) -> bool:
    """400/401/403/404-style configuration errors are not retryable.

    408 and 429 remain retryable because they normally represent transient
    timeout/rate-limit conditions rather than a bad webhook configuration.
    """
    match = re.search(r"(?:http_error|http_status)=(\d{3})", str(reason or ""))
    if not match:
        return False
    status = int(match.group(1))
    return 400 <= status < 500 and status not in {408, 429}


def ensure_notification_delivery_table(engine: "Engine") -> None:
    """Create/migrate notification audit and outbox tables idempotently."""
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        conn.execute(
            text_fn(
                """
                CREATE TABLE IF NOT EXISTS chenyiyun.app_notification_delivery (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    business_date VARCHAR(8) NOT NULL,
                    notification_type VARCHAR(64) NOT NULL,
                    task_name VARCHAR(64) NULL,
                    channel_key VARCHAR(32) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    reason TEXT NULL,
                    dedupe_key VARCHAR(160) NULL,
                    event_id VARCHAR(64) NULL,
                    run_id VARCHAR(64) NULL,
                    content_hash CHAR(64) NULL,
                    attempt_count INT NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    KEY idx_notification_delivery_date (business_date, task_name, notification_type),
                    KEY idx_notification_delivery_dedupe (dedupe_key),
                    UNIQUE KEY uniq_notification_delivery_channel_dedupe (channel_key, dedupe_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )
        conn.execute(
            text_fn(
                """
                CREATE TABLE IF NOT EXISTS chenyiyun.app_notification_outbox (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    business_date VARCHAR(8) NOT NULL,
                    notification_type VARCHAR(64) NOT NULL,
                    task_name VARCHAR(64) NULL,
                    channel_key VARCHAR(32) NOT NULL DEFAULT 'feishu',
                    content MEDIUMTEXT NOT NULL,
                    payload_json MEDIUMTEXT NULL,
                    dedupe_key VARCHAR(160) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
                    retry_count INT NOT NULL DEFAULT 0,
                    max_retries INT NOT NULL DEFAULT 3,
                    next_retry_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_error TEXT NULL,
                    claimed_by VARCHAR(64) NULL,
                    claimed_at DATETIME NULL,
                    lease_until DATETIME NULL,
                    event_id VARCHAR(64) NULL,
                    run_id VARCHAR(64) NULL,
                    content_hash CHAR(64) NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_notification_outbox_channel_dedupe (channel_key, dedupe_key),
                    KEY idx_notification_outbox_ready (status, next_retry_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
        )
    # Existing installations predate typed events and lease-based claims. MySQL
    # does not consistently support ADD COLUMN IF NOT EXISTS across supported
    # versions, so inspect first and apply only missing migrations.
    with engine.connect() as conn:
        delivery_columns = {
            str(row[0]) for row in conn.execute(text_fn(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='chenyiyun' AND table_name='app_notification_delivery'"
            )).fetchall()
        }
        outbox_columns = {
            str(row[0]) for row in conn.execute(text_fn(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='chenyiyun' AND table_name='app_notification_outbox'"
            )).fetchall()
        }
        delivery_indexes = {
            str(row[0]) for row in conn.execute(text_fn(
                "SELECT DISTINCT index_name FROM information_schema.statistics "
                "WHERE table_schema='chenyiyun' AND table_name='app_notification_delivery'"
            )).fetchall()
        }
        outbox_indexes = {
            str(row[0]) for row in conn.execute(text_fn(
                "SELECT DISTINCT index_name FROM information_schema.statistics "
                "WHERE table_schema='chenyiyun' AND table_name='app_notification_outbox'"
            )).fetchall()
        }
    delivery_alters = {
        "event_id": "ADD COLUMN event_id VARCHAR(64) NULL AFTER dedupe_key",
        "run_id": "ADD COLUMN run_id VARCHAR(64) NULL AFTER event_id",
        "content_hash": "ADD COLUMN content_hash CHAR(64) NULL AFTER run_id",
        "attempt_count": "ADD COLUMN attempt_count INT NOT NULL DEFAULT 1 AFTER content_hash",
        "updated_at": "ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at",
    }
    outbox_alters = {
        "payload_json": "ADD COLUMN payload_json MEDIUMTEXT NULL AFTER content",
        "claimed_by": "ADD COLUMN claimed_by VARCHAR(64) NULL AFTER last_error",
        "claimed_at": "ADD COLUMN claimed_at DATETIME NULL AFTER claimed_by",
        "lease_until": "ADD COLUMN lease_until DATETIME NULL AFTER claimed_at",
        "event_id": "ADD COLUMN event_id VARCHAR(64) NULL AFTER lease_until",
        "run_id": "ADD COLUMN run_id VARCHAR(64) NULL AFTER event_id",
        "content_hash": "ADD COLUMN content_hash CHAR(64) NULL AFTER run_id",
    }
    def execute_migration(conn, sql: str) -> None:
        try:
            conn.execute(text_fn(sql))
        except Exception as exc:
            # Multiple Web workers may enter this idempotent migration at the
            # same time. Only duplicate/missing-object DDL races are benign.
            detail = str(exc).lower()
            code = getattr(getattr(exc, "orig", None), "args", (None,))[0]
            if code in {1060, 1061, 1091} or any(marker in detail for marker in (
                "duplicate column", "duplicate key name", "check that column/key exists",
            )):
                return
            raise

    with engine.begin() as conn:
        for column, ddl in delivery_alters.items():
            if column not in delivery_columns:
                execute_migration(conn, f"ALTER TABLE chenyiyun.app_notification_delivery {ddl}")
        for column, ddl in outbox_alters.items():
            if column not in outbox_columns:
                execute_migration(conn, f"ALTER TABLE chenyiyun.app_notification_outbox {ddl}")
        if "uniq_notification_delivery_channel_dedupe" not in delivery_indexes:
            # Preserve the latest audit state before enforcing idempotence.
            conn.execute(text_fn(
                "DELETE older FROM chenyiyun.app_notification_delivery older "
                "JOIN chenyiyun.app_notification_delivery newer "
                "ON newer.channel_key=older.channel_key AND newer.dedupe_key=older.dedupe_key "
                "AND newer.id>older.id WHERE older.dedupe_key IS NOT NULL"
            ))
            execute_migration(conn,
                "ALTER TABLE chenyiyun.app_notification_delivery "
                "ADD UNIQUE KEY uniq_notification_delivery_channel_dedupe (channel_key,dedupe_key)"
            )
        if "uniq_notification_outbox_dedupe" in outbox_indexes:
            execute_migration(conn,
                "ALTER TABLE chenyiyun.app_notification_outbox DROP INDEX uniq_notification_outbox_dedupe"
            )
        if "uniq_notification_outbox_channel_dedupe" not in outbox_indexes:
            execute_migration(conn,
                "ALTER TABLE chenyiyun.app_notification_outbox "
                "ADD UNIQUE KEY uniq_notification_outbox_channel_dedupe (channel_key,dedupe_key)"
            )


def enqueue_notification_retry(
    engine: "Engine", content: str, *, business_date: str, notification_type: str,
    task_name: str | None, dedupe_key: str, reason: str,
    channel_key: str = "feishu", payload: Mapping[str, Any] | None = None,
    event_id: str | None = None, run_id: str | None = None,
    content_hash: str | None = None, dead_letter: bool = False,
) -> None:
    """Persist a failed delivery without changing the business task result."""
    ensure_notification_delivery_table(engine)
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        conn.execute(
            text_fn(
                """INSERT INTO chenyiyun.app_notification_outbox
                   (business_date,notification_type,task_name,channel_key,content,payload_json,
                    dedupe_key,status,retry_count,max_retries,next_retry_at,last_error,event_id,run_id,content_hash)
                   VALUES (:business_date,:notification_type,:task_name,:channel_key,:content,:payload_json,
                           :dedupe_key,:status,0,3,DATE_ADD(NOW(), INTERVAL 1 MINUTE),:reason,:event_id,:run_id,:content_hash)
                   ON DUPLICATE KEY UPDATE
                     content=IF(status='SENT',content,VALUES(content)),
                     payload_json=IF(status='SENT',payload_json,VALUES(payload_json)),
                     last_error=IF(status='SENT',last_error,VALUES(last_error)),
                     event_id=IF(status='SENT',event_id,VALUES(event_id)),
                     run_id=IF(status='SENT',run_id,VALUES(run_id)),
                     content_hash=IF(status='SENT',content_hash,VALUES(content_hash)),
                     status=IF(status='SENT','SENT',VALUES(status)),
                     claimed_by=NULL,claimed_at=NULL,lease_until=NULL"""
            ),
            {
                "business_date": business_date,
                "notification_type": notification_type,
                "task_name": task_name,
                "channel_key": channel_key,
                "content": content,
                "payload_json": json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                "dedupe_key": dedupe_key,
                "reason": str(reason or "")[:2000],
                "event_id": event_id,
                "run_id": run_id,
                "content_hash": content_hash,
                "status": "NOTIFICATION_FAILED" if dead_letter else "PENDING",
            },
        )


def process_notification_outbox(
    engine: "Engine", limit: int = 10, *, worker_id: str | None = None, lease_seconds: int = 60,
) -> dict[str, int]:
    """Claim and retry due messages without allowing concurrent double-send."""
    ensure_notification_delivery_table(engine)
    text_fn = _get_text_sql()
    worker_id = worker_id or f"notify-{uuid.uuid4().hex[:16]}"
    with engine.begin() as conn:
        conn.execute(text_fn(
            "UPDATE chenyiyun.app_notification_outbox SET status='PENDING',claimed_by=NULL,"
            "claimed_at=NULL,lease_until=NULL WHERE status='SENDING' AND lease_until<NOW()"
        ))
        rows = list(conn.execute(
            text_fn(
                """SELECT id,business_date,notification_type,task_name,channel_key,content,payload_json,
                          dedupe_key,retry_count,max_retries,event_id,run_id,content_hash
                   FROM chenyiyun.app_notification_outbox
                   WHERE status='PENDING' AND next_retry_at<=NOW()
                   ORDER BY next_retry_at,id LIMIT :limit FOR UPDATE SKIP LOCKED"""
            ), {"limit": int(limit)}
        ).mappings())
        ids = [int(row["id"]) for row in rows]
        for row_id in ids:
            conn.execute(text_fn(
                "UPDATE chenyiyun.app_notification_outbox SET status='SENDING',claimed_by=:worker_id,"
                "claimed_at=NOW(),lease_until=DATE_ADD(NOW(),INTERVAL :lease SECOND) "
                "WHERE id=:id AND status='PENDING'"
            ), {"worker_id": worker_id, "lease": int(lease_seconds), "id": row_id})

    result = {"sent": 0, "failed": 0, "pending": 0, "claimed": len(rows)}
    for row in rows:
        webhook = load_feishu_webhook(engine)
        if not webhook:
            ok, reason = False, "no_webhook"
        elif row.get("payload_json"):
            try:
                ok, reason = _send_feishu_payload(webhook, json.loads(row["payload_json"]))
            except Exception as exc:
                ok, reason = False, f"invalid_payload={exc}"
        else:
            ok, reason = send_feishu_text(webhook, row["content"])
        record_notification_delivery(
            engine, business_date=row["business_date"], notification_type=row["notification_type"],
            task_name=row["task_name"], channel_key=row["channel_key"], status="ok" if ok else "failed",
            reason=reason, dedupe_key=row["dedupe_key"], event_id=row.get("event_id"),
            run_id=row.get("run_id"), content_hash=row.get("content_hash"),
        )
        retry_count = int(row["retry_count"] or 0) + 1
        if ok:
            status, result_key = "SENT", "sent"
        elif _is_permanent_delivery_failure(reason) or retry_count >= int(row["max_retries"] or 3):
            status, result_key = "NOTIFICATION_FAILED", "failed"
        else:
            status, result_key = "PENDING", "pending"
        result[result_key] += 1
        delay_minutes = {1: 5, 2: 15}.get(retry_count, 15)
        with engine.begin() as conn:
            conn.execute(
                text_fn(
                    """UPDATE chenyiyun.app_notification_outbox
                       SET status=:status,retry_count=:retry_count,last_error=:reason,
                           next_retry_at=DATE_ADD(NOW(), INTERVAL :delay MINUTE),
                           claimed_by=NULL,claimed_at=NULL,lease_until=NULL
                       WHERE id=:id AND claimed_by=:worker_id"""
                ),
                {"status": status, "retry_count": retry_count, "reason": reason,
                 "delay": delay_minutes, "id": row["id"], "worker_id": worker_id},
            )
    return result


def record_notification_delivery(
    engine: "Engine",
    *,
    business_date: str,
    notification_type: str,
    task_name: str | None,
    channel_key: str,
    status: str,
    reason: str,
    dedupe_key: str | None = None,
    event_id: str | None = None,
    run_id: str | None = None,
    content_hash: str | None = None,
) -> None:
    """Persist the latest state for one channel/idempotency key."""
    ensure_notification_delivery_table(engine)
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        conn.execute(
            text_fn(
                """
                INSERT INTO chenyiyun.app_notification_delivery
                    (business_date, notification_type, task_name, channel_key, status, reason,
                     dedupe_key, event_id, run_id, content_hash)
                VALUES (:business_date, :notification_type, :task_name, :channel_key, :status, :reason,
                        :dedupe_key, :event_id, :run_id, :content_hash)
                ON DUPLICATE KEY UPDATE
                    business_date=VALUES(business_date),notification_type=VALUES(notification_type),
                    task_name=VALUES(task_name),status=VALUES(status),reason=VALUES(reason),
                    event_id=COALESCE(VALUES(event_id),event_id),run_id=COALESCE(VALUES(run_id),run_id),
                    content_hash=COALESCE(VALUES(content_hash),content_hash),attempt_count=attempt_count+1
                """
            ),
            {
                "business_date": str(business_date or ""),
                "notification_type": str(notification_type or "unknown"),
                "task_name": task_name,
                "channel_key": str(channel_key or "feishu"),
                "status": str(status or "unknown"),
                "reason": str(reason or "")[:2000],
                "dedupe_key": dedupe_key,
                "event_id": event_id,
                "run_id": run_id,
                "content_hash": content_hash,
            },
        )


def _already_delivered(engine: "Engine", channel_key: str, dedupe_key: str) -> bool:
    text_fn = _get_text_sql()
    with engine.connect() as conn:
        return bool(conn.execute(text_fn(
            "SELECT 1 FROM chenyiyun.app_notification_delivery "
            "WHERE channel_key=:channel_key AND dedupe_key=:dedupe_key AND status='ok' LIMIT 1"
        ), {"channel_key": channel_key, "dedupe_key": dedupe_key}).scalar())


def _reserve_delivery(
    engine: "Engine", event: NotificationEvent, channel_key: str, content_hash: str,
) -> bool:
    """Atomically reserve a channel/dedupe pair before making an HTTP call."""
    text_fn = _get_text_sql()
    with engine.begin() as conn:
        result = conn.execute(text_fn(
            "INSERT IGNORE INTO chenyiyun.app_notification_delivery "
            "(business_date,notification_type,task_name,channel_key,status,reason,dedupe_key,event_id,"
            "run_id,content_hash,attempt_count) "
            "VALUES (:business_date,:notification_type,:task_name,:channel_key,'sending','reserved',"
            ":dedupe_key,:event_id,:run_id,:content_hash,0)"
        ), {
            "business_date": event.business_date,
            "notification_type": event.event_type,
            "task_name": event.task_name,
            "channel_key": channel_key,
            "dedupe_key": event.dedupe_key,
            "event_id": event.event_id,
            "run_id": event.run_id,
            "content_hash": content_hash,
        })
        if int(result.rowcount or 0) == 1:
            return True
        # A process can die after reserving but before sending/enqueueing. Allow
        # a later publisher to recover only an abandoned reservation.
        recovered = conn.execute(text_fn(
            "UPDATE chenyiyun.app_notification_delivery SET event_id=:event_id,run_id=:run_id,"
            "content_hash=:content_hash,reason='reservation_recovered',"
            "updated_at=NOW() WHERE channel_key=:channel_key AND dedupe_key=:dedupe_key "
            "AND status='sending' AND updated_at<DATE_SUB(NOW(),INTERVAL 2 MINUTE)"
        ), {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "content_hash": content_hash,
            "channel_key": channel_key,
            "dedupe_key": event.dedupe_key,
        })
        return int(recovered.rowcount or 0) == 1


def publish_notification(
    engine: "Engine", event: NotificationEvent, *, channel_key: str = "feishu",
) -> DeliveryResult:
    """Publish a typed notification with audit, idempotence and durable retry."""
    if channel_key != "feishu":
        raise ValueError(f"unsupported channel: {channel_key}")
    ensure_notification_delivery_table(engine)
    payload = build_feishu_interactive_payload(event)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    if _already_delivered(engine, channel_key, event.dedupe_key):
        return DeliveryResult(
            True, "DEDUPED", "deduped_already_delivered", event.event_id,
            channel_key=channel_key, deduped=True,
        )
    if not _reserve_delivery(engine, event, channel_key, content_hash):
        # Another publisher reserved or queued this exact event between the
        # delivered check and our insert. It owns delivery from this point.
        return DeliveryResult(
            False, "IN_PROGRESS", "deduped_delivery_in_progress", event.event_id,
            channel_key=channel_key, deduped=True, queued=True,
        )

    content = str(payload["card"]["body"]["elements"][0]["content"])
    webhook = load_feishu_webhook(engine)
    ok, reason = _send_feishu_payload(webhook, payload) if webhook else (False, "no_webhook")
    status = "ok" if ok else (
        "notification_failed" if _is_permanent_delivery_failure(reason) else "failed"
    )
    record_notification_delivery(
        engine,
        business_date=event.business_date,
        notification_type=event.event_type,
        task_name=event.task_name,
        channel_key=channel_key,
        status=status,
        reason=reason,
        dedupe_key=event.dedupe_key,
        event_id=event.event_id,
        run_id=event.run_id,
        content_hash=content_hash,
    )
    if ok:
        return DeliveryResult(True, "SENT", reason, event.event_id, channel_key=channel_key)

    dead = _is_permanent_delivery_failure(reason)
    enqueue_notification_retry(
        engine,
        content,
        business_date=event.business_date,
        notification_type=event.event_type,
        task_name=event.task_name,
        dedupe_key=event.dedupe_key,
        reason=reason,
        channel_key=channel_key,
        payload=payload,
        event_id=event.event_id,
        run_id=event.run_id,
        content_hash=content_hash,
        dead_letter=dead,
    )
    return DeliveryResult(
        False, "NOTIFICATION_FAILED" if dead else "QUEUED", reason, event.event_id,
        channel_key=channel_key, queued=not dead,
    )


def send_feishu_text_audited(
    engine: "Engine",
    content: str,
    *,
    business_date: str,
    notification_type: str,
    task_name: str | None = None,
    dedupe_key: str | None = None,
) -> tuple[bool, str]:
    """Send a Feishu message and record the delivery result."""
    effective_dedupe_key = dedupe_key or f"{notification_type}:{task_name or 'none'}:{business_date}"
    delivered = False
    if hasattr(engine, "connect") and hasattr(engine, "begin"):
        ensure_notification_delivery_table(engine)
        text_fn = _get_text_sql()
        with engine.connect() as conn:
            delivered = conn.execute(
                text_fn(
                    """SELECT 1 FROM chenyiyun.app_notification_delivery
                       WHERE channel_key='feishu' AND dedupe_key=:dedupe_key AND status='ok' LIMIT 1"""
                ), {"dedupe_key": effective_dedupe_key}
            ).scalar()
    if delivered:
        return True, "deduped_already_delivered"
    webhook = load_feishu_webhook(engine)
    if not webhook:
        record_notification_delivery(
            engine,
            business_date=business_date,
            notification_type=notification_type,
            task_name=task_name,
            channel_key="feishu",
            status="no_webhook",
            reason="no enabled Feishu webhook",
            dedupe_key=effective_dedupe_key,
        )
        if hasattr(engine, "begin"):
            enqueue_notification_retry(
                engine, content, business_date=business_date, notification_type=notification_type,
                task_name=task_name, dedupe_key=effective_dedupe_key, reason="no_webhook",
            )
        return False, "no_webhook"

    ok, reason = send_feishu_text(webhook, content)
    record_notification_delivery(
        engine,
        business_date=business_date,
        notification_type=notification_type,
        task_name=task_name,
        channel_key="feishu",
        status="ok" if ok else "failed",
        reason=reason,
        dedupe_key=effective_dedupe_key,
    )
    if not ok:
        enqueue_notification_retry(
            engine, content, business_date=business_date, notification_type=notification_type,
            task_name=task_name, dedupe_key=effective_dedupe_key, reason=reason,
            dead_letter=_is_permanent_delivery_failure(reason),
        )
    return ok, reason


def strategy_identity_block() -> str:
    """Return a standardized strategy identity block for all Feishu cards.

    Shows both governor and selection strategies with their Chinese display names
    and internal strategy IDs.
    """
    config = load_production_config()
    governor = str(config.get("primary_strategy", "-"))
    selection = str(config.get("primary_selection_strategy", governor))
    risk_anchor = str(config.get("shadow_risk_strategy", "-"))
    shadow_version = str(config.get("shadow_version", "-"))
    risk_profile = str(config.get("risk_profile", "-"))
    position_ratio = config.get("position_ratio", 0.70)

    return (
        f"生产外壳：{strategy_display_name(governor, include_id=True)}\n"
        f"选股内核：{strategy_display_name(selection, include_id=True)}\n"
        f"风控锚：{strategy_display_name(risk_anchor, include_id=True)} {shadow_version}\n"
        f"风险档位：{risk_profile} / 当前目标仓位 {int(position_ratio * 100)}%"
    )


def dual_strategy_identity_block() -> str:
    """Return a dual-strategy identity block showing both risk-anchor and selection.

    Strategy A (风控锚): adaptive_market_style — governs position sizing & risk regime
    Strategy B (选股内核): baseline_full_liquidity_detail_vol_position — generates candidate pool
    """
    config = load_production_config()
    risk_anchor = str(config.get("shadow_risk_strategy", "adaptive_market_style"))
    selection = str(config.get("primary_selection_strategy", "baseline_full_liquidity_detail_vol_position"))
    risk_profile = str(config.get("risk_profile", "-"))
    position_ratio = config.get("position_ratio", 0.70)

    return (
        f"风控锚(A)：{strategy_display_name(risk_anchor, include_id=True)}\n"
        f"选股内核(B)：{strategy_display_name(selection, include_id=True)}\n"
        f"风险档位：{risk_profile} / 目标仓位 {int(position_ratio * 100)}%"
    )


# ---------------------------------------------------------------------------
# Four-card Feishu notification system
# ---------------------------------------------------------------------------


def format_trade_card(
    asof_date: str,
    candidate_count: int,
    order_count: int,
    buy_count: int,
    sell_count: int,
    buy_amount: float,
    sell_amount: float,
    top5_lines: list[str],
    order_lines: list[str],
    data_status: str = "READY",
) -> str:
    """Format a Trade Card (交易指令卡) for Feishu.

    Shows candidate Top5, order drafts, and data readiness status.
    """
    lines = [
        "【交易指令卡】",
        f"信号日：{asof_date}",
        strategy_identity_block(),
        f"数据就绪：{data_status}",
        f"候选数：{candidate_count} | 订单数：{order_count}（BUY {buy_count} / SELL {sell_count}）",
        f"买入额：{buy_amount:,.2f} | 卖出额：{sell_amount:,.2f}",
        "",
        "候选 Top5：",
    ]
    lines.extend(top5_lines[:5] if top5_lines else ["- 无候选"])
    lines.append("")
    lines.append("订单草案：")
    lines.extend(order_lines[:10] if order_lines else ["- 无需调仓"])

    return "\n".join(lines)


def format_dual_strategy_card(
    asof_date: str,
    data_status: str = "READY",
    # --- Strategy A (风控锚: adaptive_market_style) ---
    anchor_return_3m: float = 0.0,
    anchor_return_6m: float = 0.0,
    anchor_return_1y: float = 0.0,
    anchor_max_dd: float = 0.0,
    anchor_sharpe_3m: float = 0.0,
    anchor_daily_win_rate: float = 0.0,
    anchor_regime: str = "-",
    anchor_target_exposure: float = 0.50,
    anchor_candidate_count: int = 0,
    anchor_top5_lines: list[str] | None = None,
    # --- Strategy B (选股内核: baseline_full_liquidity_detail_vol_position) ---
    selection_return_3m: float = 0.0,
    selection_return_6m: float = 0.0,
    selection_return_1y: float = 0.0,
    selection_max_dd: float = 0.0,
    selection_sharpe_3m: float = 0.0,
    selection_daily_win_rate: float = 0.0,
    selection_candidate_count: int = 0,
    selection_top5_lines: list[str] | None = None,
    selection_cost_ratio: float = 0.0,
    # --- Warnings ---
    warnings: list[str] | None = None,
) -> str:
    """Format a Dual-Strategy Card (双策略对照卡) for Feishu.

    Top section: adaptive_market_style (risk anchor + position governance)
    Middle section: adaptive candidates
    Bottom section: baseline_full_liquidity_detail_vol_position (candidate selection)
    """
    def _pct(v: float) -> str:
        return f"{v:+.2f}%" if v else "—"

    def _dd(v: float) -> str:
        return f"{v:.2f}%" if v else "—"

    def _sharpe(v: float) -> str:
        return f"{v:.2f}" if v else "—"

    anchor_regime_emoji = {
        "strong_risk_on": "🟢", "normal_risk_on": "🟡",
        "neutral": "⚪", "risk_off": "🔵", "stress": "🔴",
    }.get(anchor_regime, "❓")

    lines = [
        "📊 【双策略对照卡】",
        f"信号日：{asof_date}",
        dual_strategy_identity_block(),
        f"数据就绪：{data_status}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{anchor_regime_emoji} 风控锚(A) 市场风格自适应策略",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"近3月收益：{_pct(anchor_return_3m)}  |  近6月：{_pct(anchor_return_6m)}  |  近1年：{_pct(anchor_return_1y)}",
        f"全期最大回撤：{_dd(anchor_max_dd)}",
        f"近3月 Sharpe：{_sharpe(anchor_sharpe_3m)}  |  日胜率：{_pct(anchor_daily_win_rate)}",
        f"当前市场状态：{anchor_regime}  |  目标敞口：{anchor_target_exposure:.0%}",
        "",
        "候选 Top5（影子盘，研究参考）：",
    ]
    if anchor_top5_lines:
        lines.extend(anchor_top5_lines[:5])
    else:
        lines.append("- 无候选数据")
    lines.append(f"候选数：{anchor_candidate_count}  |  职能：风控锚，不直接生成生产订单")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 选股内核(B) 流动性质量稳健策略",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"近3月收益：{_pct(selection_return_3m)}  |  近6月：{_pct(selection_return_6m)}  |  近1年：{_pct(selection_return_1y)}",
        f"全期最大回撤：{_dd(selection_max_dd)}",
        f"近3月 Sharpe：{_sharpe(selection_sharpe_3m)}  |  日胜率：{_pct(selection_daily_win_rate)}",
        f"候选数：{selection_candidate_count}  |  成本/毛收益：{selection_cost_ratio:.1%}",
        "",
        "候选 Top5（生产候选池）：",
    ])
    if selection_top5_lines:
        lines.extend(selection_top5_lines[:5])
    else:
        lines.append("- 无候选")

    if warnings:
        lines.append("")
        lines.append("⚠️ 告警：")
        lines.extend(f"  {i+1}. {w}" for i, w in enumerate(warnings[:5]))

    return "\n".join(lines)


def format_health_card(
    asof_date: str,
    overall_grade: str,
    execution_grade: str,
    performance_grade: str,
    risk_grade: str,
    data_grade: str,
    warnings: list[str] | None = None,
) -> str:
    """Format a Health Card (风险健康卡) for Feishu.

    Shows 4-dimension GREEN/YELLOW/RED health grades and active warnings.
    """
    grade_emoji = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🚨"}

    lines = [
        f"{grade_emoji.get(overall_grade, '❓')} 【风险健康卡】",
        f"日期：{asof_date}",
        f"总体：{overall_grade}",
        strategy_identity_block(),
        "",
        "分项评分：",
        f"- 执行质量：{execution_grade} {grade_emoji.get(execution_grade, '')}",
        f"- 策略表现：{performance_grade} {grade_emoji.get(performance_grade, '')}",
        f"- 风险总闸：{risk_grade} {grade_emoji.get(risk_grade, '')}",
        f"- 数据完整性：{data_grade} {grade_emoji.get(data_grade, '')}",
    ]

    if warnings:
        lines.extend(["", "⚠️ 告警："])
        lines.extend(f"- {w}" for w in warnings[:5])

    return "\n".join(lines)


def format_shadow_card(
    signal_date: str,
    execution_date: str,
    total_orders: int,
    buy_orders: int,
    sell_orders: int,
    executable_orders: int,
    blocked_orders: int,
    warning_orders: int,
    avg_slippage_bps: float,
    max_slippage_bps: float,
    validation_status: str,
    validation_actions: str,
    bad_order_lines: list[str] | None = None,
    large_slippage_lines: list[str] | None = None,
) -> str:
    """Format a Shadow Card (影子候选卡) for Feishu.

    Shows shadow execution quality with explicit "research only" disclaimer.
    """
    lines = [
        "【影子盘成交监控】",
        f"信号日：{signal_date}，执行日：{execution_date}",
        strategy_identity_block(),
        f"订单：{total_orders}（BUY {buy_orders} / SELL {sell_orders}）",
        f"可成交：{executable_orders}，不可成交：{blocked_orders}，大滑点警告：{warning_orders}",
        f"平均不利滑点：{avg_slippage_bps:.1f} bps",
        f"最大不利滑点：{max_slippage_bps:.1f} bps",
        f"验收结果：{validation_status} / {validation_actions}",
        "",
        "⚠️ 研究候选，不构成当前交易指令",
    ]

    if bad_order_lines:
        lines.extend(["", "不可成交订单："])
        lines.extend(bad_order_lines[:10])

    if large_slippage_lines:
        lines.extend(["", "大滑点警告："])
        lines.extend(large_slippage_lines[:5])

    return "\n".join(lines)


def format_incident_card(
    incident_type: str,
    incident_detail: str,
    asof_date: str,
    severity: str = "RED",
    action_taken: str = "",
) -> str:
    """Format an Incident & Freeze Card (事故冻结卡) for Feishu.

    Only pushed on exceptions: data anomalies, reconciliation failures,
    excessive drawdown, order matching errors.
    """
    severity_emoji = {"RED": "🚨", "YELLOW": "⚠️"}

    lines = [
        f"{severity_emoji.get(severity, '🚨')} 【事故冻结卡】",
        f"日期：{asof_date}",
        f"类型：{incident_type}",
        f"严重程度：{severity}",
        "",
        strategy_identity_block(),
        "",
        f"详情：{incident_detail}",
    ]

    if action_taken:
        lines.append(f"已执行动作：{action_taken}")

    return "\n".join(lines)
