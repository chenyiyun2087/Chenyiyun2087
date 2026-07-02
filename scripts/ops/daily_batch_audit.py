"""Audit daily scheduled batch tasks and prepare confirmed replay candidates."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pymysql
import yaml
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_pymysql_config, build_sqlalchemy_url
from scripts.ops.feishu_notifier import send_feishu_text_audited


PIPELINE_PATH = PROJECT_ROOT / "task_registry" / "pipeline.yaml"
AUDIT_TASK_ID = "ops_daily_batch_audit"
@dataclass(frozen=True)
class ExpectedTask:
    task_name: str
    schedule_time: str
    script: str
    trading_day_only: bool
    group: str = "daily_close"
    day_of_week: int | None = None


def normalize_datestr(raw: str | None) -> str:
    if raw:
        value = str(raw).strip()
        if len(value) == 8 and value.isdigit():
            return value
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


def load_expected_daily_tasks(pipeline_path: Path = PIPELINE_PATH) -> list[ExpectedTask]:
    payload = yaml.safe_load(pipeline_path.read_text(encoding="utf-8")) or {}
    tasks = []
    for group_name, group in payload.items():
        if not isinstance(group, dict):
            continue
        for item in group.get("tasks", []):
            if item.get("status") != "enabled":
                continue
            task_name = str(item.get("id") or "").strip()
            if not task_name or task_name == AUDIT_TASK_ID:
                continue
            tasks.append(
                ExpectedTask(
                    task_name=task_name,
                    schedule_time=str(item.get("time") or ""),
                    script=str(item.get("script") or ""),
                    trading_day_only=bool(item.get("trading_day_only", group_name != "weekly")),
                    group=str(group_name),
                    day_of_week=int(item["day_of_week"]) if item.get("day_of_week") is not None else None,
                )
            )
    return tasks


def ensure_audit_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_daily_batch_audit (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            business_date VARCHAR(8) NOT NULL,
            task_name VARCHAR(64) NOT NULL,
            expected_time VARCHAR(5) NULL,
            status VARCHAR(32) NOT NULL,
            reason TEXT NULL,
            queue_id BIGINT NULL,
            queue_status VARCHAR(16) NULL,
            history_status VARCHAR(32) NULL,
            notification_status VARCHAR(32) NULL,
            replay_required TINYINT(1) NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_daily_batch_task (business_date, task_name),
            KEY idx_daily_batch_date (business_date, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_notification_delivery (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            business_date VARCHAR(8) NOT NULL,
            notification_type VARCHAR(64) NOT NULL,
            task_name VARCHAR(64) NULL,
            channel_key VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL,
            reason TEXT NULL,
            dedupe_key VARCHAR(160) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_notification_delivery_date (business_date, task_name, notification_type),
            KEY idx_notification_delivery_dedupe (dedupe_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def is_trading_day(cursor, business_date: str) -> bool:
    cursor.execute(
        """
        SELECT is_open
        FROM chenyiyun.dim_trade_cal
        WHERE cal_date = %s AND exchange = 'SSE'
        LIMIT 1
        """,
        (business_date,),
    )
    row = cursor.fetchone()
    return bool(row and int(row.get("is_open") or 0) == 1)


def _latest_queue(cursor, task_name: str, business_date: str) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT id, status, message, exit_code, attempt_count, max_attempts
        FROM app_task_queue
        WHERE task_name = %s AND business_date = %s
        ORDER BY (status = 'SUCCESS') DESC, id DESC
        LIMIT 1
        """,
        (task_name, business_date),
    )
    return cursor.fetchone() or {}


def _latest_history(cursor, task_name: str, business_date: str) -> dict[str, Any]:
    day_start = f"{business_date[:4]}-{business_date[4:6]}-{business_date[6:]} 00:00:00"
    cursor.execute(
        """
        SELECT status, exit_code, message, started_at, finished_at
        FROM app_task_history
        WHERE task_name = %s
          AND (business_date = %s OR (business_date IS NULL AND started_at >= %s AND started_at < DATE_ADD(%s, INTERVAL 1 DAY)))
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (task_name, business_date, day_start, day_start),
    )
    return cursor.fetchone() or {}


def _notification_status(cursor, task_name: str, business_date: str) -> str | None:
    cursor.execute(
        """
        SELECT status
        FROM app_notification_delivery
        WHERE business_date = %s AND task_name = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (business_date, task_name),
    )
    row = cursor.fetchone()
    return str(row.get("status") or "") if row else "MISSING"


def _recovered_artifact(cursor, task_name: str, business_date: str) -> str | None:
    """Return durable output evidence for tasks repaired outside the queue."""
    if task_name == "bs_signal_monthly_cycle":
        for manifest_path in sorted((PROJECT_ROOT / "exports" / "bs_signal_cycles").glob("*/cycle_manifest.json"), reverse=True):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            recovery_date = str(manifest.get("recovered_for_business_date") or "")
            activation = manifest.get("activation") or {}
            model_path = Path(str(manifest.get("model_path") or activation.get("model_path") or ""))
            if (
                recovery_date == business_date
                and manifest.get("status") == "completed"
                and bool(activation.get("committed"))
                and model_path.is_file()
            ):
                return f"补跑产物已核验：月度模型 {model_path.parent.name}（原失败记录保留）"
        return None
    if task_name == "trusted_strategy_candidates":
        cursor.execute(
            """SELECT
                 (SELECT COUNT(*) FROM chenyiyun.ads_trusted_strategy_candidates WHERE trade_date=%s) AS candidates,
                 (SELECT COUNT(*) FROM chenyiyun.ads_chenyiyun_selected_signals WHERE trade_date=%s) AS signals""",
            (business_date, business_date),
        )
        row = cursor.fetchone() or {}
        candidate_count = int(row.get("candidates") or 0)
        signal_count = int(row.get("signals") or 0)
        if candidate_count > 0 and signal_count > 0:
            return f"补跑产物已核验：生产候选 {candidate_count} 行，每日信号 {signal_count} 行"
        return None
    checks = {
        "adc_bs_detect": (
            "SELECT COUNT(*) AS c FROM chenyiyun.bs_detection_results WHERE batch_date=%s",
            "B/S检测结果",
        ),
    }
    spec = checks.get(task_name)
    if not spec:
        return None
    cursor.execute(spec[0], (business_date,))
    row = cursor.fetchone() or {}
    count = int(row.get("c") or 0)
    return f"补跑产物已核验：{spec[1]} {count} 行" if count > 0 else None


def classify_task(
    cursor, expected: ExpectedTask, business_date: str, trading_day: bool,
    *, require_notifications: bool = True,
) -> dict[str, Any]:
    business_day = datetime.strptime(business_date, "%Y%m%d").date()
    if expected.day_of_week is not None and business_day.weekday() != expected.day_of_week:
        return {
            "task_name": expected.task_name, "expected_time": expected.schedule_time,
            "status": "SKIPPED_SCHEDULE", "reason": "合法跳过：非计划星期",
            "queue_id": None, "queue_status": None, "history_status": "Success",
            "notification_status": None, "replay_required": 0,
        }
    if expected.trading_day_only and not trading_day:
        return {
            "task_name": expected.task_name,
            "expected_time": expected.schedule_time,
            "status": "SKIPPED_NON_TRADING",
            "reason": "合法跳过：非交易日",
            "queue_id": None,
            "queue_status": None,
            "history_status": "Success",
            "notification_status": None,
            "replay_required": 0,
        }

    queue = _latest_queue(cursor, expected.task_name, business_date)
    history = _latest_history(cursor, expected.task_name, business_date)
    notification = _notification_status(cursor, expected.task_name, business_date) if require_notifications else None
    queue_status = str(queue.get("status") or "").upper()
    history_status = str(history.get("status") or "")
    recovered = _recovered_artifact(cursor, expected.task_name, business_date)

    if recovered:
        status = "OK"
        reason = recovered
        replay_required = 0
    elif not queue and not history:
        status = "MISSING"
        reason = "未发现当日队列或历史执行记录"
        replay_required = 1
    elif queue_status in {"PENDING", "RUNNING"}:
        status = queue_status
        reason = str(queue.get("message") or "作业仍在等待或运行")
        replay_required = 0
    elif queue_status in {"FAILED", "BLOCKED", "CANCELLED"} or history_status in {"Failed", "Error"}:
        status = "FAILED"
        reason = str(queue.get("message") or history.get("message") or "任务失败")
        replay_required = 1
    elif queue_status == "SUCCESS" or history_status == "Success":
        if notification == "MISSING":
            status = "NOTIFICATION_MISSING"
            reason = "任务成功，但未发现飞书投递审计记录"
            replay_required = 0
        elif notification and notification != "ok":
            status = "NOTIFICATION_FAILED"
            reason = f"任务成功，但通知投递失败：{notification}"
            replay_required = 0
        else:
            status = "OK"
            reason = "任务完成"
            replay_required = 0
    else:
        status = "UNKNOWN"
        reason = str(queue.get("message") or history.get("message") or "状态无法判定")
        replay_required = 1

    return {
        "task_name": expected.task_name,
        "expected_time": expected.schedule_time,
        "status": status,
        "reason": reason[:2000],
        "queue_id": queue.get("id"),
        "queue_status": queue_status or None,
        "history_status": history_status or None,
        "notification_status": notification,
        "replay_required": int(replay_required),
    }


def persist_audit_row(cursor, business_date: str, row: dict[str, Any]) -> None:
    cursor.execute(
        """
        INSERT INTO app_daily_batch_audit
            (business_date, task_name, expected_time, status, reason, queue_id,
             queue_status, history_status, notification_status, replay_required)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            expected_time = VALUES(expected_time),
            status = VALUES(status),
            reason = VALUES(reason),
            queue_id = VALUES(queue_id),
            queue_status = VALUES(queue_status),
            history_status = VALUES(history_status),
            notification_status = VALUES(notification_status),
            replay_required = VALUES(replay_required)
        """,
        (
            business_date,
            row["task_name"],
            row.get("expected_time"),
            row["status"],
            row.get("reason"),
            row.get("queue_id"),
            row.get("queue_status"),
            row.get("history_status"),
            row.get("notification_status"),
            int(row.get("replay_required") or 0),
        ),
    )


def format_audit_notification(summary: dict[str, Any]) -> str:
    bad_rows = [r for r in summary["rows"] if r.get("status") not in {"OK", "SKIPPED_NON_TRADING", "SKIPPED_SCHEDULE"}]
    lines = [
        "【日终批量巡检】",
        f"日期：{summary['business_date']}",
        f"交易日：{'是' if summary['trading_day'] else '否'}",
        f"结论：{summary['status']}",
        f"需要确认补跑：{summary['replay_required_count']} 个任务",
    ]
    if bad_rows:
        lines.extend(["", "异常任务："])
        for row in bad_rows[:12]:
            lines.append(f"- {row['task_name']}: {row['status']} / {row.get('reason') or '-'}")
    return "\n".join(lines)


def run_audit(
    business_date: str, *, notify_feishu: bool = False,
    require_notifications: bool = True, historical_reissue: bool = False,
) -> dict[str, Any]:
    expected_tasks = load_expected_daily_tasks()
    conn = pymysql.connect(**build_pymysql_config())
    try:
        with conn.cursor() as cursor:
            ensure_audit_schema(cursor)
            trading_day = is_trading_day(cursor, business_date)
            rows = [
                classify_task(
                    cursor, task, business_date, trading_day,
                    require_notifications=require_notifications,
                )
                for task in expected_tasks
            ]
            # Record the audit task itself only after all classifications have
            # completed. This proves the script ran without making it depend on
            # its own queue/history record (which is finalized by the parent
            # worker after this process exits).
            rows.append({
                "task_name": AUDIT_TASK_ID,
                "expected_time": "22:20",
                "status": "OK",
                "reason": "巡检脚本已完成；业务异常见各任务状态",
                "queue_id": None,
                "queue_status": None,
                "history_status": "Success",
                "notification_status": None,
                "replay_required": 0,
            })
            for row in rows:
                persist_audit_row(cursor, business_date, row)
            conn.commit()
    finally:
        conn.close()

    replay_required_count = sum(int(row.get("replay_required") or 0) for row in rows)
    bad_count = sum(1 for row in rows if row.get("status") not in {"OK", "SKIPPED_NON_TRADING", "SKIPPED_SCHEDULE"})
    summary = {
        "business_date": business_date,
        "trading_day": trading_day,
        "status": "PASS" if bad_count == 0 else "ACTION_REQUIRED",
        "replay_required_count": replay_required_count,
        "rows": rows,
    }

    if notify_feishu:
        engine = create_engine(build_sqlalchemy_url())
        content = format_audit_notification(summary)
        if historical_reissue:
            content = "【历史补发】\n" + content
        send_feishu_text_audited(
            engine,
            content,
            business_date=business_date,
            notification_type="daily_batch_audit",
            task_name=AUDIT_TASK_ID,
            dedupe_key=f"daily_batch_audit:{business_date}",
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit daily batch task completion and notifications.")
    parser.add_argument("--date", default=None, help="Business date, YYYYMMDD or YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--notify-feishu", action="store_true")
    parser.add_argument("--no-require-notifications", action="store_true")
    parser.add_argument("--historical-reissue", action="store_true", help="Prefix the notification as a historical reissue.")
    args = parser.parse_args()
    summary = run_audit(
        normalize_datestr(args.date), notify_feishu=args.notify_feishu,
        require_notifications=not args.no_require_notifications,
        historical_reissue=args.historical_reissue,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
