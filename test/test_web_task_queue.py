import os
from pathlib import Path

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app
from scripts.ops import feishu_notifier
from scripts.ops.daily_batch_audit import ExpectedTask, classify_task, load_expected_daily_tasks


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_queue_business_date_prefers_explicit_replay_date():
    assert web_app._queue_business_date({"datestr": "2026-06-20"}) == "20260620"


def test_dependency_state_waits_for_missing_upstream_job():
    cursor = FakeCursor([])
    state, message = web_app._dependency_state(cursor, "sina_analyse", "20260620")
    assert state == "WAITING"
    assert "sina_picture" in message


def test_dependency_state_blocks_when_upstream_has_terminal_failure():
    cursor = FakeCursor([{"task_name": "trusted_strategy_candidates", "status": "FAILED"}])
    state, message = web_app._dependency_state(cursor, "trusted_strategy_performance_review", "20260620")
    assert state == "BLOCKED"
    assert "trusted_strategy_candidates" in message


def test_dependency_state_accepts_successful_upstream_job():
    cursor = FakeCursor([
        {"task_name": "sina_bs_consensus", "status": "SUCCESS"},
        {"task_name": "rolling_strategy_scorer", "status": "SUCCESS"},
    ])
    state, message = web_app._dependency_state(cursor, "trusted_strategy_candidates", "20260620")
    assert state == "READY"
    assert not message


def test_queue_contract_keeps_active_deduplication_and_single_retry():
    source = open(web_app.__file__, encoding="utf-8").read()
    assert "UNIQUE KEY uniq_queue_active_dedupe" in source
    assert "max_attempts, run_options, active_dedupe_key" in source
    assert "TASK_RETRY_DELAY_SECONDS" in source


def test_pipeline_enabled_scripts_exist_and_are_whitelisted():
    root = Path(web_app.__file__).resolve().parents[1]
    expected = load_expected_daily_tasks(root / "task_registry" / "pipeline.yaml")

    assert expected
    missing = [task.script for task in expected if not (root / task.script).exists()]
    assert missing == []
    assert {task.task_name for task in expected}.issubset(web_app.SCHEDULED_TASK_WHITELIST)
    names = [task.task_name for task in expected if task.group == "daily_close"]
    assert names.index("sina_score") < names.index("sina_bs_consensus")
    assert names.index("sina_bs_consensus") < names.index("trusted_strategy_candidates")


def test_generic_completion_notification_covers_failures_and_manual_runs(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_has_successful_business_notification", lambda *_: False)
    monkeypatch.setattr(web_app, "_dispatch_task_notification", lambda content, **kwargs: sent.append((content, kwargs)))
    from datetime import datetime, timedelta
    started = datetime(2026, 6, 29, 21, 0, 0)
    web_app._send_task_completion_notification(
        "sina_score", "Failed", "manual", started, started + timedelta(seconds=12),
        run_options={"datestr": "20260629"}, message="verification failed",
    )
    assert len(sent) == 1
    assert "状态：Failed" in sent[0][0]
    assert "触发方式：manual" in sent[0][0]
    assert sent[0][1]["task_name"] == "sina_score"


def test_rich_business_notification_suppresses_generic_duplicate(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_has_successful_business_notification", lambda *_: True)
    monkeypatch.setattr(web_app, "_dispatch_task_notification", lambda *args, **kwargs: sent.append(1))
    from datetime import datetime
    now = datetime(2026, 6, 29, 21, 35, 0)
    web_app._send_task_completion_notification(
        "trusted_strategy_candidates", "Success", "schedule", now, now,
        run_options={"datestr": "20260629"},
    )
    assert sent == []


def test_daily_batch_audit_classifies_missing_and_non_trading_skip():
    missing_cursor = FakeCursor([])
    expected = ExpectedTask("trusted_strategy_candidates", "21:25", "x.py", True)

    missing = classify_task(missing_cursor, expected, "20260624", trading_day=True)
    assert missing["status"] == "MISSING"
    assert missing["replay_required"] == 1

    skipped = classify_task(missing_cursor, expected, "20260620", trading_day=False)
    assert skipped["status"] == "SKIPPED_NON_TRADING"
    assert skipped["replay_required"] == 0


def test_replay_required_jobs_enqueue_as_replay(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_get_daily_batch_audit_rows",
        lambda business_date: [
            {"task_name": "trusted_strategy_candidates", "replay_required": 1},
            {"task_name": "trusted_strategy_shadow_monitor", "replay_required": 0},
        ],
    )
    calls = []

    def fake_enqueue(task_name, trigger_type="manual", run_options=None, scheduled_for=None):
        calls.append((task_name, trigger_type, run_options, scheduled_for))
        return {"id": 7, "task_name": task_name}, True, None

    monkeypatch.setattr(web_app, "_enqueue_task", fake_enqueue)
    result = web_app._enqueue_replay_required_jobs("20260624")

    assert len(result["created"]) == 1
    assert calls == [("trusted_strategy_candidates", "replay", {"datestr": "20260624"}, None)]


def test_start_scheduler_delegates_to_web_console():
    source = Path("start_scheduler.sh").read_text(encoding="utf-8")
    assert "start_web_console.sh" in source
    assert 'SCRIPT="scheduler.py"' not in source


def test_feishu_audit_records_no_webhook(monkeypatch):
    records = []
    monkeypatch.setattr(feishu_notifier, "load_feishu_webhook", lambda engine: None)
    monkeypatch.setattr(
        feishu_notifier,
        "record_notification_delivery",
        lambda engine, **kwargs: records.append(kwargs),
    )

    ok, reason = feishu_notifier.send_feishu_text_audited(
        object(),
        "hello",
        business_date="20260624",
        notification_type="unit",
        task_name="task",
        dedupe_key="unit:20260624",
    )

    assert ok is False
    assert reason == "no_webhook"
    assert records[0]["status"] == "no_webhook"
    assert records[0]["business_date"] == "20260624"
