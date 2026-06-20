import os

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def test_queue_business_date_prefers_explicit_replay_date():
    assert web_app._queue_business_date({"datestr": "2026-06-20"}) == "20260620"


def test_dependency_state_waits_for_missing_upstream_job():
    cursor = FakeCursor([])
    state, message = web_app._dependency_state(cursor, "sina_analyse", "20260620")
    assert state == "WAITING"
    assert "sina_picture" in message


def test_dependency_state_blocks_when_upstream_has_terminal_failure():
    cursor = FakeCursor([{"task_name": "sina_score", "status": "FAILED"}])
    state, message = web_app._dependency_state(cursor, "sina_bs_consensus", "20260620")
    assert state == "BLOCKED"
    assert "sina_score" in message


def test_dependency_state_accepts_successful_upstream_job():
    cursor = FakeCursor([{"task_name": "sina_bs_consensus", "status": "SUCCESS"}])
    state, message = web_app._dependency_state(cursor, "trusted_strategy_candidates", "20260620")
    assert state == "READY"
    assert not message


def test_queue_contract_keeps_active_deduplication_and_single_retry():
    source = open(web_app.__file__, encoding="utf-8").read()
    assert "UNIQUE KEY uniq_queue_active_dedupe" in source
    assert "max_attempts, run_options, active_dedupe_key" in source
    assert "TASK_RETRY_DELAY_SECONDS" in source
