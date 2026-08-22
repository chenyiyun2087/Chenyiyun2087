#!/usr/bin/env python3
"""Replay only failed/blocked batch links for 2026-08-17..2026-08-21."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_ENV_FILE = Path("~/.config/chenyiyun/web.env").expanduser()
DEFAULT_DATES = ("20260817", "20260818", "20260819", "20260820", "20260821")
REPLAY_ORDER = (
    ("alpha_signal_package_seal", ("20260817", "20260818", "20260819")),
    ("candle_diag_scan", ("20260817", "20260818", "20260819")),
    ("alpha_signal_precommit", ("20260818", "20260819")),
    ("alpha_signal_sell_precommit", ("20260818", "20260819")),
    ("alpha_signal_execution_reconcile", ("20260818", "20260819")),
    ("alpha_signal_nav", ("20260818", "20260819")),
    ("ops_daily_batch_audit", DEFAULT_DATES),
)
HEALTHY_QUEUE_STATUSES = {"SUCCESS"}
HEALTHY_HISTORY_STATUSES = {"SUCCESS"}
FAILED_STATUSES = {"FAILED", "BLOCKED", "FAILURE"}
HEALTHY_AUDIT_STATUSES = {"OK", "SKIPPED_NON_TRADING", "SKIPPED_SCHEDULE"}


def _load_env(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"FATAL: missing credentials env file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def _normalize_date(raw: str) -> str:
    value = str(raw or "").strip().replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid business date: {raw}")
    return value


def _latest_state(cursor, task_name: str, business_date: str) -> dict[str, Any]:
    cursor.execute(
        """SELECT id, status, message, requested_at, finished_at
           FROM app_task_queue
           WHERE task_name=%s AND business_date=%s
           ORDER BY id DESC LIMIT 1""",
        (task_name, business_date),
    )
    queue = cursor.fetchone() or {}
    cursor.execute(
        """SELECT id, status, message, created_at, finished_at
           FROM app_task_history
           WHERE task_name=%s AND business_date=%s
           ORDER BY id DESC LIMIT 1""",
        (task_name, business_date),
    )
    history = cursor.fetchone() or {}
    return {"queue": queue, "history": history}


def _audit_state(cursor, business_date: str) -> dict[str, Any] | None:
    cursor.execute(
        """SELECT status, reason, replay_required, updated_at
           FROM app_daily_batch_audit
           WHERE business_date=%s AND task_name=%s""",
        (business_date, "ops_daily_batch_audit"),
    )
    return cursor.fetchone()


def _package_is_sealed(business_date: str) -> bool:
    iso = f"{business_date[:4]}-{business_date[4:6]}-{business_date[6:]}"
    path = PROJECT_ROOT / "exports" / "forward_shadow_evidence" / "packages" / iso / "signal_package_manifest.json"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("package_status") == "SEALED"


def _needs_replay(state: dict[str, Any]) -> tuple[bool, str]:
    queue_status = str((state.get("queue") or {}).get("status") or "").upper()
    history_status = str((state.get("history") or {}).get("status") or "").upper()
    if queue_status in HEALTHY_QUEUE_STATUSES or history_status in HEALTHY_HISTORY_STATUSES:
        return False, "already_successful"
    if queue_status in FAILED_STATUSES or history_status in FAILED_STATUSES:
        return True, "failed_or_blocked"
    if queue_status in {"PENDING", "RUNNING"}:
        return False, f"active_{queue_status.lower()}"
    return False, "no_failed_record"


def build_replay_plan(
    cursor,
    dates: tuple[str, ...],
    *,
    historical_safe: bool,
) -> list[dict[str, Any]]:
    date_set = set(dates)
    plan: list[dict[str, Any]] = []
    for task_name, task_dates in REPLAY_ORDER:
        for business_date in task_dates:
            if business_date not in date_set:
                continue
            state = _latest_state(cursor, task_name, business_date)
            if task_name == "alpha_signal_package_seal" and _package_is_sealed(business_date):
                plan.append({
                    "task_name": task_name,
                    "business_date": business_date,
                    "action": "skip",
                    "reason": "sealed_package_preserved",
                })
                continue
            if task_name == "ops_daily_batch_audit":
                audit = _audit_state(cursor, business_date)
                audit_status = str((audit or {}).get("status") or "").upper()
                if audit_status in HEALTHY_AUDIT_STATUSES:
                    plan.append({
                        "task_name": task_name,
                        "business_date": business_date,
                        "action": "skip",
                        "reason": "audit_already_healthy",
                    })
                    continue
                if audit is None:
                    should_replay, reason = True, "audit_missing"
                else:
                    should_replay, reason = (
                        audit_status not in HEALTHY_AUDIT_STATUSES,
                        f"audit_{audit_status.lower() or 'unknown'}",
                    )
            else:
                should_replay, reason = _needs_replay(state)
            plan.append({
                "task_name": task_name,
                "business_date": business_date,
                "action": "enqueue" if should_replay else "skip",
                "reason": reason,
                "historical_safe": historical_safe,
            })
    return plan


def execute_replay_plan(runtime, plan: list[dict[str, Any]], *, historical_safe: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in plan:
        if item.get("action") != "enqueue":
            results.append(item)
            continue
        task_name = str(item["task_name"])
        business_date = str(item["business_date"])
        job, created, reason = runtime._enqueue_task(
            task_name,
            trigger_type="replay",
            run_options={"datestr": business_date, "historical_safe": historical_safe},
        )
        results.append({
            **item,
            "action": "enqueued" if created else "deduped" if job else "failed",
            "queue_id": (job or {}).get("id") if job else None,
            "enqueue_reason": reason,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely replay the weekly failed batch chain.")
    parser.add_argument("--date", dest="dates", action="append", help="Business date YYYYMMDD (repeatable).")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true", help="Inspect and print actions without enqueueing.")
    parser.add_argument(
        "--historical-safe",
        action="store_true",
        help="Required for historical execution; suppresses orders and success notifications.",
    )
    args = parser.parse_args()
    dates = tuple(_normalize_date(item) for item in (args.dates or list(DEFAULT_DATES)))
    today = date.today().strftime("%Y%m%d")
    if any(item < today for item in dates) and not args.historical_safe:
        raise SystemExit("FATAL: historical dates require --historical-safe")
    _load_env(args.env_file.expanduser())
    os.environ.setdefault("CHENYIYUN_RUNTIME_ROLE", "maintenance")

    from web import app as runtime

    conn = runtime._connect_db()
    try:
        with conn.cursor() as cursor:
            plan = build_replay_plan(
                cursor,
                dates,
                historical_safe=bool(args.historical_safe),
            )
        if args.dry_run:
            results = plan
        else:
            results = execute_replay_plan(
                runtime,
                plan,
                historical_safe=bool(args.historical_safe),
            )
    finally:
        conn.close()
    print(json.dumps({
        "scope": list(dates),
        "dry_run": bool(args.dry_run),
        "historical_safe": bool(args.historical_safe),
        "results": results,
        "enqueued": sum(item.get("action") == "enqueued" for item in results),
        "skipped": sum(item.get("action") == "skip" for item in results),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
