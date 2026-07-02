"""Dedicated durable scheduler/queue worker, isolated from Web restarts."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["CHENYIYUN_RUNTIME_ROLE"] = "worker"
os.environ["DISABLE_APP_SCHEDULER_LOOP"] = "1"

from scripts.ops.runtime_preflight import assert_runtime


def main() -> None:
    assert_runtime()
    from web import app as runtime

    conn = runtime.pymysql.connect(**runtime.DB_CONFIG)
    with conn.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('chenyiyun.task_queue_worker', 0) AS acquired")
        acquired = int((cursor.fetchone() or {}).get("acquired") or 0)
    if acquired != 1:
        conn.close()
        raise SystemExit("another task queue worker already owns the database lease")

    try:
        runtime._run_web_startup_preflight(strict=True)
        threading.Thread(target=runtime._run_scheduled_tasks_loop, name="task-scheduler", daemon=True).start()
        threading.Thread(target=runtime._run_notification_outbox_loop, name="notification-outbox-worker", daemon=True).start()
        runtime._run_queued_tasks_loop()
    finally:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK('chenyiyun.task_queue_worker')")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
