#!/usr/bin/env python3
"""Run the daily batch audit and notify only when operator action is needed.

Routine task completion is intentionally silent. The scheduler already emits
immediate typed events for failures, dependency blocks, retries and recoveries;
this job provides the final cross-task reconciliation and one anomaly digest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops.daily_batch_audit import (
    format_audit_notification,
    normalize_datestr,
    run_audit,
)
from scripts.ops.feishu_notifier import send_feishu_text_audited

HEALTHY_STATUSES = {"OK", "SKIPPED_NON_TRADING", "SKIPPED_SCHEDULE"}
DIGEST_TASK = "trusted_strategy_performance_review"


def _digest_delivery_row(business_date: str) -> dict[str, Any]:
    engine = create_engine(build_sqlalchemy_url())
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """SELECT status,reason,dedupe_key,updated_at
                       FROM chenyiyun.app_notification_delivery
                       WHERE business_date=:business_date
                         AND task_name=:task_name
                       ORDER BY id DESC LIMIT 1"""
                ),
                {"business_date": business_date, "task_name": DIGEST_TASK},
            ).mappings().first()
    except Exception as exc:
        return {
            "task_name": "integrated_strategy_digest_delivery",
            "expected_time": "21:45",
            "status": "NOTIFICATION_AUDIT_FAILED",
            "reason": f"无法读取综合策略简报投递状态：{type(exc).__name__}",
            "replay_required": 0,
        }
    finally:
        engine.dispose()

    if row is None:
        return {
            "task_name": "integrated_strategy_digest_delivery",
            "expected_time": "21:45",
            "status": "NOTIFICATION_MISSING",
            "reason": "综合策略简报未发现投递审计记录",
            "replay_required": 0,
        }
    status = str(row.get("status") or "").lower()
    if status == "ok":
        return {
            "task_name": "integrated_strategy_digest_delivery",
            "expected_time": "21:45",
            "status": "OK",
            "reason": "综合策略简报投递成功",
            "notification_status": status,
            "replay_required": 0,
        }
    return {
        "task_name": "integrated_strategy_digest_delivery",
        "expected_time": "21:45",
        "status": "NOTIFICATION_FAILED",
        "reason": f"综合策略简报投递未成功：{status or 'unknown'} / {row.get('reason') or '-'}",
        "notification_status": status or None,
        "replay_required": 0,
    }


def attention_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in list(summary.get("rows") or [])
        if str(row.get("status") or "") not in HEALTHY_STATUSES
    ]


def run_integrated_audit(
    business_date: str,
    *,
    notify_feishu: bool = False,
    historical_safe: bool = False,
    historical_reissue: bool = False,
) -> dict[str, Any]:
    # Notification delivery is not a duty of every batch task. Audit task
    # execution and artifacts first, then check the single integrated digest.
    summary = run_audit(
        business_date,
        notify_feishu=False,
        require_notifications=False,
        historical_reissue=False,
    )
    digest_expected = bool(summary.get("trading_day")) and (
        not historical_safe or historical_reissue
    )
    if digest_expected:
        summary["rows"] = list(summary.get("rows") or []) + [
            _digest_delivery_row(business_date)
        ]

    bad_rows = attention_rows(summary)
    summary["notification_mode"] = "ANOMALY_ONLY"
    summary["digest_delivery_expected"] = digest_expected
    summary["attention_count"] = len(bad_rows)
    # The digest delivery row is appended AFTER the legacy summary was
    # computed — recompute the status so a missing/failed digest alone
    # turns the day ACTION_REQUIRED (and the card's conclusion line
    # matches the anomaly rows it lists).
    summary["status"] = "ACTION_REQUIRED" if bad_rows else "PASS"
    if historical_safe and not historical_reissue:
        summary["notify_result"] = "skipped_historical_safe"
    else:
        summary["notify_result"] = "skipped_healthy" if not bad_rows else None

    should_notify = (
        notify_feishu
        and bool(bad_rows)
        and (not historical_safe or historical_reissue)
    )
    if should_notify:
        content = format_audit_notification(summary)
        if historical_reissue:
            content = "【历史补发】\n" + content
        engine = create_engine(build_sqlalchemy_url())
        try:
            ok, reason = send_feishu_text_audited(
                engine,
                content,
                business_date=business_date,
                notification_type="ops_daily_batch_audit_incident",
                task_name="ops_daily_batch_audit",
                dedupe_key=f"ops_daily_batch_audit_incident:{business_date}",
            )
            summary["notify_result"] = "ok" if ok else reason
            summary["notify_detail"] = reason
        finally:
            engine.dispose()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit daily batches and send Feishu only for anomalies."
    )
    parser.add_argument("--date", default=None)
    parser.add_argument("--notify-feishu", action="store_true")
    parser.add_argument("--historical-safe", action="store_true")
    parser.add_argument("--historical-reissue", action="store_true")
    args = parser.parse_args()
    summary = run_integrated_audit(
        normalize_datestr(args.date),
        notify_feishu=args.notify_feishu,
        historical_safe=args.historical_safe,
        historical_reissue=args.historical_reissue,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
