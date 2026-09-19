#!/usr/bin/env python3
"""Mark repaired queue rows successful only after artifact verification.

This is an operator-only recovery path for append-only tasks whose durable
artifacts were rebuilt outside the queue.  It never runs a task again and it
never changes the artifacts; it records a separate recovery history row after
the task's normal verifier passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RECOVERABLE_TASKS = (
    "alpha_signal_precommit",
    "alpha_signal_execution_reconcile",
    "alpha_signal_sell_precommit",
    "alpha_signal_nav",
)


def _normalize_date(raw: str) -> str:
    value = str(raw or "").strip().replace("-", "")
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"invalid business date: {raw}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover queue rows from verified repaired artifacts."
    )
    parser.add_argument("--date", dest="dates", action="append", required=True,
                        help="Business date YYYYMMDD; repeatable.")
    parser.add_argument("--min-queue-id", type=int, required=True,
                        help="Only consider queue rows at or above this repair run.")
    parser.add_argument("--max-queue-id", type=int, default=None,
                        help="Optional inclusive upper queue id bound.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify and print actions without changing the database.")
    args = parser.parse_args()
    dates = tuple(_normalize_date(item) for item in args.dates)
    max_queue_id = args.max_queue_id or 2**63 - 1
    if args.min_queue_id > max_queue_id:
        raise SystemExit("min queue id must not exceed max queue id")
    if not (os.environ.get("CHENYIYUN_DB_URL") or
            os.environ.get("CHENYIYUN_DB_PASSWORD")):
        raise SystemExit(
            "FATAL: provide database credentials through the credential manager"
        )

    os.environ.setdefault("CHENYIYUN_RUNTIME_ROLE", "maintenance")
    from web import app as runtime
    from scripts.ops.task_worker_service import record_task_evidence

    now = datetime.now()
    results = []
    conn = runtime._connect_db()
    try:
        with conn.cursor() as cursor:
            runtime._ensure_task_management_schema(cursor)
            for business_date in dates:
                for task_name in RECOVERABLE_TASKS:
                    cursor.execute(
                        """SELECT *
                           FROM app_task_queue
                           WHERE id >= %s AND id <= %s
                             AND task_name=%s AND business_date=%s
                           ORDER BY id DESC LIMIT 1""",
                        (args.min_queue_id, max_queue_id, task_name,
                         business_date),
                    )
                    job = cursor.fetchone()
                    if not job:
                        results.append({
                            "task_name": task_name,
                            "business_date": business_date,
                            "action": "skip",
                            "reason": "queue_row_not_found",
                        })
                        continue
                    if str(job.get("status") or "").upper() == "SUCCESS":
                        results.append({
                            "task_name": task_name,
                            "business_date": business_date,
                            "queue_id": job.get("id"),
                            "action": "skip",
                            "reason": "already_success",
                        })
                        continue

                    options = {"datestr": business_date,
                               "historical_safe": True}
                    ok, details = runtime._run_task_result_verification(
                        task_name, now, now, options
                    )
                    result = {
                        "task_name": task_name,
                        "business_date": business_date,
                        "queue_id": job.get("id"),
                        "action": "recover" if ok else "leave_terminal",
                        "verified": bool(ok),
                        "verification": details,
                    }
                    if not ok or args.dry_run:
                        results.append(result)
                        continue

                    message = "[recovered_artifact]\n" + "\n".join(details)
                    evidence_sha = record_task_evidence(
                        job, history_status="Success", exit_code=0,
                        message=message,
                    )
                    finished_at = datetime.now()
                    cursor.execute(
                        """UPDATE app_task_queue
                           SET status='SUCCESS', finished_at=%s, exit_code=0,
                               error_kind=NULL, message=%s,
                               evidence_manifest_sha=%s,
                               active_dedupe_key=NULL
                           WHERE id=%s""",
                        (finished_at, message[-6000:], evidence_sha,
                         job["id"]),
                    )
                    cursor.execute(
                        """INSERT INTO app_task_history
                           (task_name, task_display_name, trigger_type,
                            business_date, started_at, finished_at, status,
                            exit_code, duration_seconds, message)
                           VALUES (%s, %s, 'recovery', %s, %s, %s,
                                   'Success', 0, 0, %s)""",
                        (task_name,
                         runtime.TASKS.get(task_name, {}).get("name") or task_name,
                         business_date, finished_at, finished_at,
                         message[-6000:]),
                    )
                    results.append(result)
        if not args.dry_run:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(json.dumps({
        "dry_run": bool(args.dry_run),
        "scope": list(dates),
        "queue_id_range": [args.min_queue_id, max_queue_id],
        "results": results,
        "recovered": sum(item.get("action") == "recover" for item in results),
        "verified_failures": sum(
            item.get("action") == "leave_terminal" for item in results
        ),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
