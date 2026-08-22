from __future__ import annotations

from scripts.maintenance.replay_weekly_failed_batches import (
    _needs_replay,
    execute_replay_plan,
)


def test_replay_plan_only_marks_failed_or_blocked_records():
    assert _needs_replay({"queue": {"status": "SUCCESS"}, "history": {}}) == (
        False,
        "already_successful",
    )
    assert _needs_replay({"queue": {"status": "FAILED"}, "history": {}}) == (
        True,
        "failed_or_blocked",
    )
    assert _needs_replay({"queue": {"status": "PENDING"}, "history": {}}) == (
        False,
        "active_pending",
    )


def test_historical_replay_passes_safe_options_to_enqueue():
    calls = []

    class Runtime:
        @staticmethod
        def _enqueue_task(task_name, trigger_type, run_options):
            calls.append((task_name, trigger_type, run_options))
            return {"id": 42}, True, None

    results = execute_replay_plan(
        Runtime(),
        [{
            "task_name": "alpha_signal_precommit",
            "business_date": "20260818",
            "action": "enqueue",
            "reason": "failed_or_blocked",
        }],
        historical_safe=True,
    )

    assert results[0]["action"] == "enqueued"
    assert calls == [(
        "alpha_signal_precommit",
        "replay",
        {"datestr": "20260818", "historical_safe": True},
    )]
