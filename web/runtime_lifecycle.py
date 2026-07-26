"""Lifecycle helpers shared by the Flask web process and compatibility mode."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable


LoopTarget = tuple[str, Callable[[], None]]


def start_daemon_loops(targets: Iterable[LoopTarget]) -> tuple[threading.Thread, ...]:
    """Start each named loop once for the current invocation.

    Process-role selection remains in ``web.app``.  Keeping thread creation in
    this small module makes imports side-effect free and gives tests a pure
    lifecycle boundary.
    """

    threads = tuple(
        threading.Thread(target=target, name=name, daemon=True)
        for name, target in targets
    )
    for thread in threads:
        thread.start()
    return threads
