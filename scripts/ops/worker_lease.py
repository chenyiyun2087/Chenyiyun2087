"""Fail closed when the dedicated worker loses its connection-owned lease."""
from __future__ import annotations


class WorkerLease:
    def __init__(self, connection):
        self.connection = connection

    def assert_owned(self):
        try:
            # Reconnecting would create a new session without the GET_LOCK.
            self.connection.ping(reconnect=False)
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT IS_USED_LOCK('chenyiyun.task_queue_worker') = CONNECTION_ID() AS owned"
                )
                owned = int((cursor.fetchone() or {}).get("owned") or 0)
            if owned != 1:
                raise RuntimeError("lease ownership mismatch")
        except Exception as exc:
            raise RuntimeError("worker database lease lost; restart required") from exc
