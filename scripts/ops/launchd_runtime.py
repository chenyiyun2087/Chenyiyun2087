"""Publish the application PID to its launchd supervisor before exec."""
from __future__ import annotations

import os
from pathlib import Path


def record_service_pid() -> None:
    raw = os.environ.get("CHENYIYUN_SERVICE_PID_FILE", "")
    if not raw:
        return
    path = Path(raw)
    # Each supervisor uses a unique file. Never overwrite another instance's
    # identity: the supervisor uses it to forward termination to this process.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        os.fsync(handle.fileno())
