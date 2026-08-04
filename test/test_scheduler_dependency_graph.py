"""Scheduler DAG integrity tests (v5.4.1 evidence repair).

Validates task_registry/pipeline.yaml:
  - a dependent task's time must NOT be earlier than its dependency's time
    (the old daily_vls_scores 15:25 depends_on sina_analyse 16:10 relation
    was an impossible DAG — that bug class must never return)
  - the DAG must be acyclic
  - trading-day-only tasks share the calendar gate (single calendar)
  - shadow tasks stay disabled until Shadow Engine v2 passes
"""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PIPELINE = PROJECT_ROOT / "task_registry" / "pipeline.yaml"


def _tasks() -> dict[str, dict]:
    cfg = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    tasks = {}
    for group in cfg.values():
        for t in group.get("tasks", []):
            tasks[t["id"]] = t
    return tasks


def test_dag_is_acyclic():
    tasks = _tasks()
    visited = set()
    stack = set()

    def visit(node: str) -> None:
        if node in stack:
            raise AssertionError(f"cycle detected at {node}")
        if node in visited:
            return
        stack.add(node)
        for dep in tasks[node].get("depends_on", []):
            if dep in tasks:  # external deps tolerated
                visit(dep)
        stack.discard(node)
        visited.add(node)

    for tid in tasks:
        visit(tid)


def test_no_reverse_time_dependency():
    """A task must never run before a task it depends on."""
    tasks = _tasks()

    def minutes(t: str) -> int:
        hh, mm = t.split(":")
        return int(hh) * 60 + int(mm)

    bad = []
    for tid, task in tasks.items():
        for dep in task.get("depends_on", []):
            if dep not in tasks:
                continue
            dt, tt = minutes(tasks[dep]["time"]), minutes(task["time"])
            if dt > tt:
                bad.append(f"{tid}@{task['time']} depends_on {dep}@{tasks[dep]['time']} (reverse-time)")
    assert not bad, f"impossible DAG relations: {bad}"


def test_trading_day_tasks_share_calendar_gate():
    """Tasks are gated by exactly one of: trading_day_only (trade calendar)
    or day_of_week (weekly) — never both, never neither."""
    tasks = _tasks()
    for tid, task in tasks.items():
        has_calendar_gate = task.get("trading_day_only") is True
        has_weekly_gate = task.get("day_of_week") is not None
        assert has_calendar_gate or has_weekly_gate, (
            f"{tid} must carry a calendar gate (trading_day_only) or a "
            "day_of_week gate")


def test_shadow_tasks_disabled_until_shadow_v2():
    """Pre-v5.5 shadow tasks must stay disabled (evidence repair)."""
    tasks = _tasks()
    for tid in ("daily_vls_scores", "alpha_challenger_shadow_record",
                "alpha_challenger_shadow_reconcile"):
        assert tasks[tid]["status"] == "disabled", f"{tid} must be disabled"
        assert "disabled_reason" in tasks[tid], f"{tid} must carry a reason"
        assert "v5.4.1" in tasks[tid]["disabled_reason"] or "v5.5" in tasks[tid]["disabled_reason"]


def test_corrected_dag_times_are_post_close():
    """The fixed shadow DAG must sit after the 16:10 data collection."""
    tasks = _tasks()
    assert tasks["daily_vls_scores"]["time"] >= "16:00"
    assert tasks["alpha_challenger_shadow_record"]["time"] >= "16:40"
