import os
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app
from runtime import shadow_events
from scripts.ops import feishu_notifier
from scripts.ops import task_worker_service
from scripts.ops.daily_batch_audit import (
    ExpectedTask,
    _recovered_artifact,
    classify_task,
    load_expected_daily_tasks,
)


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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


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


def test_dependency_state_accepts_verified_recovery_artifact(monkeypatch):
    cursor = FakeCursor([])
    monkeypatch.setattr(
        "scripts.ops.daily_batch_audit._recovered_artifact",
        lambda _cursor, task_name, business_date: (
            "verified rows" if task_name == "adc_bs_detect" and business_date == "20260701" else None
        ),
    )
    state, message = web_app._dependency_state(cursor, "bs_ocr_adc_compare", "20260701")
    assert state == "READY"
    assert not message


def test_precommit_waits_for_cross_day_sealed_package(monkeypatch):
    cursor = FakeCursor([])
    monkeypatch.setattr(web_app, "_sealed_package_for_execution", lambda _: None)

    state, message = web_app._dependency_state(
        cursor, "alpha_signal_precommit", "20260825", "schedule"
    )

    assert state == "WAITING"
    assert "20260825" in message


def test_precommit_becomes_ready_after_cross_day_package_is_sealed(monkeypatch):
    cursor = FakeCursor([])
    monkeypatch.setattr(
        web_app,
        "_sealed_package_for_execution",
        lambda execution_date: ({"execution_date": execution_date}, Path("manifest")),
    )

    state, message = web_app._dependency_state(
        cursor, "alpha_signal_precommit", "20260825", "schedule"
    )

    assert state == "READY"
    assert not message


def test_reconcile_depends_on_same_day_precommit():
    assert web_app.TASK_DEPENDENCIES["alpha_signal_execution_reconcile"] == (
        "alpha_signal_precommit",
    )


def test_bs_compare_depends_on_same_day_adc_detection():
    assert web_app.TASK_DEPENDENCIES["bs_ocr_adc_compare"] == ("adc_bs_detect",)
    cursor = FakeCursor([])
    state, message = web_app._dependency_state(cursor, "bs_ocr_adc_compare", "20260701")
    assert state == "WAITING"
    assert "adc_bs_detect" in message


def test_bs_compare_has_result_verifier(monkeypatch):
    expected = (True, ["result=PASS"])
    monkeypatch.setattr(web_app, "_verify_bs_ocr_adc_compare_result", lambda *args, **kwargs: expected)
    result = web_app._run_task_result_verification(
        "bs_ocr_adc_compare", None, None, run_options={"datestr": "20260701"}
    )
    assert result == expected


def test_every_enabled_pipeline_task_has_a_result_verifier():
    root = Path(web_app.__file__).resolve().parents[1]
    expected = load_expected_daily_tasks(root / "task_registry" / "pipeline.yaml")
    verifier_tasks = {
        "sina_picture", "sina_analyse", "adc_bs_detect", "bs_ocr_adc_compare",
        "sina_score", "sina_bs_consensus", "trusted_strategy_backtest",
        "rolling_strategy_scorer", "trusted_strategy_candidates",
        "trusted_strategy_shadow_monitor", "trusted_strategy_performance_review",
        "candle_diag_scan", "pit_forward_shadow_collection", "bs_signal_monthly_cycle",
        "sina_bs_image_weekly_cleanup",
        # v5.5 (2026-08-04): Forward Shadow Engine v2 chain.
        # RE-ENABLED 2026-08-05 (Phase 5): artifact-based verifiers live in
        # web/app.py — SEALED packages + execution ledgers ARE the evidence,
        # so verification is file-based (no DB dependency).
        "alpha_signal_package_seal", "alpha_signal_precommit",
        "alpha_signal_execution_reconcile", "alpha_signal_sell_precommit",
        "alpha_signal_nav",
    }
    assert {task.task_name for task in expected} == verifier_tasks


def test_rolling_strategy_scorer_is_dispatched_to_verifier(monkeypatch):
    expected = (True, ["result=PASS"])
    monkeypatch.setattr(web_app, "_verify_rolling_strategy_scorer_result", lambda *args, **kwargs: expected)
    assert web_app._run_task_result_verification(
        "rolling_strategy_scorer", None, None, run_options={"datestr": "20260702"}
    ) == expected


def test_pit_forward_collection_is_dispatched_to_verifier(monkeypatch):
    expected = (True, ["result=PASS"])
    monkeypatch.setattr(web_app, "_verify_pit_forward_shadow_collection_result", lambda *args, **kwargs: expected)
    assert web_app._run_task_result_verification(
        "pit_forward_shadow_collection", None, None, run_options={"datestr": "20260702"}
    ) == expected


def test_weekly_cleanup_verifier_rejects_remaining_previous_week_dir(monkeypatch, tmp_path):
    root = tmp_path / "sina" / "bs_detection" / "SinaAppBS" / "config_1"
    (root / "20260626").mkdir(parents=True)
    monkeypatch.setattr(web_app.app, "root_path", str(tmp_path / "web"))

    ok, lines = web_app._verify_weekly_image_cleanup_result(
        datetime(2026, 7, 3, 22, 5), datetime(2026, 7, 3, 22, 6),
        run_options={"datestr": "20260703"},
    )

    assert ok is False
    assert "remaining_dirs=1" in lines[0]


def test_monthly_bs_verifier_accepts_existing_committed_cycle(monkeypatch, tmp_path):
    run_root = tmp_path / "exports" / "bs_signal_cycles" / "20260702_recovery"
    run_root.mkdir(parents=True)
    (run_root / "cycle_manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-07-02T09:20:00+08:00",
                "status": "completed",
                "activation": {"committed": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app.app, "root_path", str(tmp_path / "web"))

    ok, lines = web_app._verify_monthly_bs_cycle_result(
        datetime(2026, 7, 2, 22, 0),
        datetime(2026, 7, 2, 22, 1),
        run_options={"datestr": "20260702"},
    )

    assert ok is True
    assert "evidence=existing_monthly_cycle" in lines[0]


def test_monthly_bs_verifier_rejects_prior_month_cycle(monkeypatch, tmp_path):
    run_root = tmp_path / "exports" / "bs_signal_cycles" / "202606_cycle"
    run_root.mkdir(parents=True)
    (run_root / "cycle_manifest.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-06-30T22:00:00+08:00",
                "status": "completed",
                "activation": {"committed": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app.app, "root_path", str(tmp_path / "web"))

    ok, lines = web_app._verify_monthly_bs_cycle_result(
        datetime(2026, 7, 2, 22, 0),
        datetime(2026, 7, 2, 22, 1),
        run_options={"datestr": "20260702"},
    )

    assert ok is False
    assert "reason=no_completed_manifest" in lines[0]


def test_monthly_bs_verifier_accepts_legal_non_first_day_skip(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app.app, "root_path", str(tmp_path / "web"))
    monkeypatch.setattr(
        web_app,
        "_connect_db",
        lambda: FakeConnection([{"target_is_open": 1, "first_open_date": "20260901"}]),
    )

    ok, lines = web_app._verify_monthly_bs_cycle_result(
        datetime(2026, 9, 2, 22, 0),
        datetime(2026, 9, 2, 22, 1),
        run_options={"datestr": "20260902"},
    )

    assert ok is True
    assert "result=SKIP" in lines[0]
    assert "not_first_trading_day:first_open=20260901" in lines[0]


def test_queue_contract_keeps_active_deduplication_and_single_retry():
    source = open(web_app.__file__, encoding="utf-8").read()
    assert "UNIQUE KEY uniq_queue_active_dedupe" in source
    assert "max_attempts, run_options, active_dedupe_key" in source
    assert "TASK_RETRY_DELAY_SECONDS" in source


def test_stale_timeout_is_close_to_heartbeat_cadence():
    assert web_app.TASK_STALE_TIMEOUT_SECONDS <= 15 * web_app.TASK_HEARTBEAT_INTERVAL_SECONDS


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


def test_retired_db_bs_detect_cannot_be_scheduled():
    assert web_app.TASKS["db_bs_detect"]["legacy"] is True
    assert "db_bs_detect" not in web_app.SCHEDULED_TASK_WHITELIST
    assert "adc_bs_detect" in web_app.SCHEDULED_TASK_WHITELIST


def test_batch_monitor_definition_and_status_merge_follow_pipeline():
    definitions = web_app._load_batch_monitor_definition()
    # 16 baseline tasks + 5 re-enabled alpha_signal_* tasks (Phase 5,
    # 2026-08-05) = 21.  The morning group is back with the re-enable.
    assert len(definitions) == 21
    assert definitions[0]["task_name"] == "adc_bs_detect"
    assert {row["group_label"] for row in definitions} == {"盘中", "日终", "周度", "晨间"}

    rows = web_app._build_batch_monitor_rows(
        definitions[:2],
        [{"task_name": "adc_bs_detect", "status": "OK", "replay_required": 0}],
        [{"id": 9, "task_name": "bs_ocr_adc_compare", "status": "RUNNING", "message": "working"}],
        [],
        [{"task_name": "adc_bs_detect", "status": "ok", "notification_type": "task_completion"}],
    )
    assert rows[0]["status_tone"] == "success"
    assert rows[0]["notification_status"] == "ok"
    assert rows[1]["status_tone"] == "running"
    assert rows[1]["queue_id"] == 9


def test_batch_operations_page_renders_monitor(monkeypatch):
    monkeypatch.setattr(web_app, "_get_batch_monitor_data", lambda _: {
        "rows": [],
        "summary": {"total": 15, "healthy": 10, "active": 2, "attention": 1, "notified": 5},
        "feishu_deliveries": [],
        "feishu_summary": {"ok": 5, "failed": 0},
        "channels": [],
        "error": None,
    })
    client = web_app.app.test_client()
    response = client.get("/batch-operations?business_date=20260630")
    assert response.status_code == 200
    assert "批量任务与飞书监控" in response.get_data(as_text=True)
    assert "20260630" in response.get_data(as_text=True)


def test_generic_completion_notification_covers_failures_and_manual_runs(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_dispatch_non_feishu_task_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_has_successful_business_notification", lambda *_, **__: False)
    monkeypatch.setattr(
        feishu_notifier, "publish_notification",
        lambda _engine, event: sent.append(event),
    )
    from datetime import datetime, timedelta
    started = datetime(2026, 6, 29, 21, 0, 0)
    web_app._send_task_completion_notification(
        "sina_score", "Failed", "manual", started, started + timedelta(seconds=12),
        run_options={"datestr": "20260629"}, message="verification failed",
        queue_job={"id": 42, "attempt_count": 1, "max_attempts": 2},
        finish_result={
            "queue_id": 42, "attempt": 1, "max_attempts": 2,
            "error_kind": "VERIFICATION", "will_retry": False, "exit_code": 1,
        },
    )
    assert len(sent) == 1
    assert sent[0].event_type == "task_failed"
    assert sent[0].severity == "ERROR"
    assert sent[0].facts["触发方式"] == "manual"
    assert sent[0].task_name == "sina_score"


def test_rich_business_notification_suppresses_generic_duplicate(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_dispatch_non_feishu_task_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_has_successful_business_notification", lambda *_, **__: True)
    monkeypatch.setattr(feishu_notifier, "publish_notification", lambda *args, **kwargs: sent.append(1))
    from datetime import datetime
    now = datetime(2026, 6, 29, 21, 35, 0)
    web_app._send_task_completion_notification(
        "trusted_strategy_candidates", "Success", "schedule", now, now,
        run_options={"datestr": "20260629"},
    )
    assert sent == []


def test_task_retry_and_recovery_notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_dispatch_non_feishu_task_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_has_successful_business_notification", lambda *_, **__: False)
    monkeypatch.setattr(feishu_notifier, "publish_notification", lambda _engine, event: sent.append(event))
    from datetime import datetime, timedelta
    started = datetime(2026, 7, 15, 21, 0, 0)

    web_app._send_task_completion_notification(
        "sina_score", "Failed", "schedule", started, started + timedelta(seconds=5),
        run_options={"datestr": "20260715"},
        queue_job={"id": 51, "attempt_count": 1, "max_attempts": 2},
        finish_result={
            "queue_id": 51, "attempt": 1, "max_attempts": 2, "error_kind": "TRANSIENT",
            "will_retry": True, "next_retry_at": "2026-07-15T21:01:05", "exit_code": -1,
        },
    )
    web_app._send_task_completion_notification(
        "sina_score", "Success", "schedule", started, started + timedelta(seconds=20),
        run_options={"datestr": "20260715"},
        queue_job={"id": 51, "attempt_count": 2, "max_attempts": 2},
        finish_result={
            "queue_id": 51, "attempt": 2, "max_attempts": 2,
            "recovered": True, "will_retry": False, "exit_code": 0,
        },
    )

    assert [event.event_type for event in sent] == ["task_retrying", "task_recovered"]
    assert sent[0].severity == "WARNING"
    assert sent[1].severity == "SUCCESS"


def test_normal_task_success_is_silent(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_dispatch_non_feishu_task_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(feishu_notifier, "publish_notification", lambda *args, **kwargs: sent.append(1))
    now = datetime(2026, 7, 15, 21, 0, 0)
    web_app._send_task_completion_notification(
        "sina_score", "Success", "schedule", now, now,
        run_options={"datestr": "20260715"},
        queue_job={"id": 52, "attempt_count": 1, "max_attempts": 2},
        finish_result={"queue_id": 52, "attempt": 1, "recovered": False},
    )
    assert sent == []


def test_scheduler_skip_never_sends_failure_notification(monkeypatch):
    sent = []
    monkeypatch.setattr(web_app, "_dispatch_non_feishu_task_event", lambda *args, **kwargs: sent.append(1))
    monkeypatch.setattr(feishu_notifier, "publish_notification", lambda *args, **kwargs: sent.append(1))
    now = datetime(2026, 7, 18, 21, 5, 0)

    for status in ("SKIPPED_NON_TRADING_DAY", "SKIPPED_SCHEDULE_DAY"):
        web_app._send_task_completion_notification(
            "adc_bs_detect", status, "schedule", now, now,
            run_options={"datestr": "20260718"},
            message="result=SKIP",
        )

    assert sent == []


def test_dependency_block_publishes_warning_card(monkeypatch):
    sent = []
    monkeypatch.setattr(feishu_notifier, "publish_notification", lambda _engine, event: sent.append(event))
    web_app._send_task_blocked_notification(
        {
            "id": 77, "attempt_count": 0, "task_name": "trusted_strategy_candidates",
            "business_date": "20260715", "trigger_type": "schedule",
        },
        "前序任务失败：sina_bs_consensus",
    )
    assert len(sent) == 1
    assert sent[0].event_type == "task_blocked"
    assert sent[0].severity == "WARNING"
    assert sent[0].dedupe_key == "task_blocked:77"


def test_task_subprocess_env_exposes_job_identity():
    env = web_app._build_task_subprocess_env(
        "trusted_strategy_candidates", Path.cwd(),
        queue_job={"id": 88, "attempt_count": 2},
        run_options={"datestr": "20260715"},
    )
    assert env["CHENYIYUN_TASK_JOB_ID"] == "88"
    assert env["CHENYIYUN_TASK_ATTEMPT"] == "2"
    assert env["CHENYIYUN_TASK_BUSINESS_DATE"] == "20260715"
    assert any(
        "site-packages" in item
        for item in env.get("PYTHONPATH", "").split(os.pathsep)
    )


def test_task_execution_uses_manifest_runtime_python(monkeypatch, tmp_path):
    release_root = tmp_path / "release"
    (release_root / "web").mkdir(parents=True)
    runtime_python = tmp_path / "shared-python"
    runtime_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(web_app.app, "root_path", str(release_root / "web"))
    monkeypatch.setenv("CHENYIYUN_RUNTIME_PYTHON", str(runtime_python))
    monkeypatch.setattr(web_app, "_build_task_script_parts", lambda *args, **kwargs: ["dummy.py"])
    monkeypatch.setattr(web_app, "_build_task_subprocess_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(web_app, "update_task_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_insert_task_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_send_task_completion_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_mark_task_lock_finished", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "_run_task_result_verification", lambda *args, **kwargs: (True, ["result=PASS"]))
    captured = {}

    def fake_execute(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(task_worker_service, "execute_subprocess", fake_execute)
    web_app._execute_locked_task(
        "sina_picture",
        "manual",
        run_options={"datestr": "20260824"},
    )

    assert captured["cmd"][0] == str(runtime_python)
    assert captured["cmd"][1].endswith("/release/dummy.py")


def test_daily_batch_audit_classifies_missing_and_non_trading_skip():
    missing_cursor = FakeCursor([])
    expected = ExpectedTask("trusted_strategy_candidates", "21:25", "x.py", True)

    missing = classify_task(missing_cursor, expected, "20260624", trading_day=True)
    assert missing["status"] == "MISSING"
    assert missing["replay_required"] == 1

    skipped = classify_task(missing_cursor, expected, "20260620", trading_day=False)
    assert skipped["status"] == "SKIPPED_NON_TRADING"
    assert skipped["replay_required"] == 0


def test_adc_recovery_evidence_requires_ml_batch():
    cursor = FakeCursor([{"c": 435}])
    assert _recovered_artifact(cursor, "adc_bs_detect", "20260629")
    sql, params = cursor.calls[0]
    assert "batch_name='ml_detect_v3'" in sql
    assert params == ("20260629",)


def test_score_recovery_evidence_requires_complete_core_fields():
    cursor = FakeCursor([{"c": 5160, "null_score": 0, "null_opt": 0, "null_claude": 0}])
    evidence = _recovered_artifact(cursor, "sina_score", "20260629")
    assert evidence and "5160" in evidence
    assert "STR_TO_DATE" in cursor.calls[0][0]


def test_shadow_recovery_evidence_requires_target_execution_date():
    cursor = FakeCursor([{"c": 1}])
    evidence = _recovered_artifact(cursor, "trusted_strategy_shadow_monitor", "20260727")
    assert evidence and "影子盘日级结果 1 行" in evidence
    assert "execution_date=STR_TO_DATE" in cursor.calls[0][0]
    assert cursor.calls[0][1] == ("20260727",)


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
    assert calls == [(
        "trusted_strategy_candidates", "replay",
        {"datestr": "20260624", "historical_safe": True}, None,
    )]


def test_start_scheduler_delegates_to_dedicated_services():
    source = Path("start_scheduler.sh").read_text(encoding="utf-8")
    assert "install_web_launchd.sh" in source
    assert 'SCRIPT="scheduler.py"' not in source


def test_scheduler_is_silent_and_does_not_enqueue_on_non_trading_day(monkeypatch):
    calls = []
    monkeypatch.setattr(web_app, "_is_trading_day", lambda _date: False)
    monkeypatch.setattr(
        web_app,
        "_trigger_task_execution",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    def stop_after_first_poll(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(web_app.time, "sleep", stop_after_first_poll)

    try:
        web_app._run_scheduled_tasks_loop()
    except KeyboardInterrupt:
        pass

    assert calls == []


def test_historical_replay_disables_orders_and_marks_business_notifications():
    options = {
        "datestr": "20260630",
        "historical_safe": True,
        "historical_reissue": True,
    }
    candidate = web_app._build_task_script_parts("trusted_strategy_candidates", options)
    assert "--no-emit-orders" in candidate
    assert "--emit-orders" not in candidate
    assert "--historical-reissue" in candidate
    assert "--write-signal-snapshot" in candidate

    rolling = web_app._build_task_script_parts("rolling_strategy_scorer", options)
    assert rolling[-3:] == ["--calc-date", "2026-06-30", "--no-push"]
    assert web_app._build_task_script_parts("bs_ocr_adc_compare", options)[-4:] == [
        "--start", "20260630", "--end", "20260630",
    ]
    assert "--force" not in web_app._build_task_script_parts("bs_signal_monthly_cycle", options)

    for task_name in (
        "trusted_strategy_shadow_monitor",
        "trusted_strategy_performance_review",
        "ops_daily_batch_audit",
    ):
        assert "--historical-reissue" in web_app._build_task_script_parts(task_name, options)


def test_current_day_candidate_task_always_writes_signal_snapshot():
    candidate = web_app._build_task_script_parts(
        "trusted_strategy_candidates", {"datestr": "20260720"}
    )

    assert "--emit-orders" in candidate
    assert "--write-signal-snapshot" in candidate


def test_historical_replay_suppresses_business_notifications_without_reissue():
    options = {"datestr": "20260629", "historical_safe": True}
    for task_name in (
        "trusted_strategy_candidates",
        "trusted_strategy_shadow_monitor",
        "trusted_strategy_performance_review",
    ):
        assert "--notify-feishu" not in web_app._build_task_script_parts(task_name, options)


def test_performance_review_verifier_accounts_for_historical_notification_policy():
    source = Path(web_app.__file__).read_text(encoding="utf-8")
    assert "notification_expected = not historical_safe or historical_reissue" in source
    assert "notify_result in (None, \"skipped\")" in source


def test_launchd_assets_use_user_env_and_keepalive():
    root = Path(web_app.__file__).resolve().parents[1]
    start_source = (root / "start_web_console.sh").read_text(encoding="utf-8")
    launcher_source = (root / "scripts/ops/web_console_launcher.py").read_text(encoding="utf-8")
    plist_source = (root / "scripts/ops/com.chenyiyun.web-console.plist").read_text(encoding="utf-8")
    install_source = (root / "scripts/ops/install_web_launchd.sh").read_text(encoding="utf-8")

    assert ".config/chenyiyun/web.env" in start_source
    assert "CHENYIYUN_DB_PASSWORD" not in plist_source
    assert "<key>KeepAlive</key>" in plist_source
    assert "/opt/homebrew/bin/python3.14" in plist_source
    assert "web_console_launcher.py" in plist_source
    assert 'VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"' in launcher_source
    assert "python = str(VENV_PYTHON)" in launcher_source
    assert "os.execvpe(\n        python," in launcher_source
    assert "must have mode 600" in install_source
    assert "launchctl print \"$DOMAIN/$LABEL\"" in install_source
    assert "failed to register $LABEL after bounded retries" in install_source


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


# ── v5.5.3 (2026-08-05): alpha_signal_* production wiring ──────────────
#
# Defect found by probe: task_commands.py only injected --date for the
# OLD shadow task names; the five alpha_signal_* names fell through to
# the generic fallback and ran WITHOUT --date.  precommit(None)/
# sell_precommit(None) then resolved execution_date = open_days[-1] =
# 2026-12-31 (the snapshot calendar extends to year-end) and crashed
# with "shadow_blocked: no SEALED package for execution_date 2026-12-31".


def test_alpha_signal_tasks_carry_queue_date_except_sell():
    options = {"datestr": "20260805"}
    for task in ("alpha_signal_package_seal", "alpha_signal_precommit",
                 "alpha_signal_execution_reconcile", "alpha_signal_nav"):
        parts = web_app._build_task_script_parts(task, options)
        assert parts[-2:] == ["--date", "2026-08-05"], parts
    # The sell task MUST NOT receive --date: it runs at T 17:00 under
    # datestr=T but resolves the T+1 fill day from the latest SEALED
    # package — passing --date T would bind it to the stale T-1 package.
    sell = web_app._build_task_script_parts(
        "alpha_signal_sell_precommit", options)
    assert "--date" not in sell, sell
    assert sell[1:3] == ["--mode", "sell-precommit"], sell

    historical_sell = web_app._build_task_script_parts(
        "alpha_signal_sell_precommit",
        {"datestr": "20260805", "historical_safe": True},
    )
    assert historical_sell[-2:] == ["--business-date", "2026-08-05"], historical_sell


def test_sell_verifier_binds_latest_sealed_package_execution_date(monkeypatch, tmp_path):
    """The sell task runs at T 17:00 (datestr=T); its sells land under
    the latest SEALED package's execution_date (T+1) — the verifier must
    look there, not at datestr."""
    root = tmp_path / "evidence"
    pkg = root / "packages" / "2026-08-05"
    pkg.mkdir(parents=True)
    (pkg / "signal_package_manifest.json").write_text(json.dumps({
        "signal_date": "2026-08-05", "execution_date": "2026-08-06",
        "package_status": "SEALED",
    }), encoding="utf-8")
    (pkg / "package_sha256.json").write_text(json.dumps({
        "package_sha256": "a" * 64,
    }), encoding="utf-8")
    # no positions held -> the producer wrote the decision marker only.
    marker = root / "execution" / "2026-08-06" / "sell_decisions.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({
        "signal_date": "2026-08-05", "execution_date": "2026-08-06",
        "reason": "no_open_positions",
    }), encoding="utf-8")
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_sell_precommit_result(
        None, None, run_options={"datestr": "20260805"})
    assert ok, lines
    assert any("reason=no_open_positions" in ln for ln in lines)
    assert any("skipped=0" in ln for ln in lines)


def test_sell_verifier_rejects_stale_sealed_package(monkeypatch, tmp_path):
    """Fail-closed: the latest SEALED package whose execution day is not
    ahead of the queue date means no fresh signal — stale sells fail."""
    root = tmp_path / "evidence"
    pkg = root / "packages" / "2026-08-04"
    pkg.mkdir(parents=True)
    (pkg / "signal_package_manifest.json").write_text(json.dumps({
        "signal_date": "2026-08-04", "execution_date": "2026-08-05",
        "package_status": "SEALED",
    }), encoding="utf-8")
    (pkg / "package_sha256.json").write_text(json.dumps({
        "package_sha256": "b" * 64,
    }), encoding="utf-8")
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_sell_precommit_result(
        None, None, run_options={"datestr": "20260805"})
    assert not ok
    assert any("stale_sealed_package" in ln for ln in lines)


def test_sell_verifier_no_sealed_package_fails_closed(monkeypatch, tmp_path):
    root = tmp_path / "evidence"
    (root / "packages").mkdir(parents=True)
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_sell_precommit_result(
        None, None, run_options={"datestr": "20260805"})
    assert not ok
    assert any("no_sealed_package" in ln for ln in lines)


def test_precommit_verifier_unpacks_manifest_path_convention(monkeypatch, tmp_path):
    """Regression (2026-08-05): _sealed_package_for_execution returned
    (path, manifest) but the precommit verifier unpacks (manifest, path)
    — on any real artifact it crashed with AttributeError
    ('PosixPath' has no 'get'), caught into a FAIL.  The convention is
    (manifest, path) like _forward_sealed_manifest."""
    root = tmp_path / "evidence"
    pkg = root / "packages" / "2026-08-05"
    pkg.mkdir(parents=True)
    (pkg / "signal_package_manifest.json").write_text(json.dumps({
        "signal_date": "2026-08-05", "execution_date": "2026-08-06",
        "package_status": "SEALED",
    }), encoding="utf-8")
    (pkg / "package_sha256.json").write_text(json.dumps({
        "package_sha256": "c" * 64,
    }), encoding="utf-8")
    import pandas as pd

    pd.DataFrame([{"candidate_id": "c0", "symbol": "600001",
                   "target_weight": 1.0}]).to_parquet(
        pkg / "target_portfolios.parquet", index=False)
    # A BUY order bound to the T-seal, precommitted for the T+1 day.
    exec_dir = root / "execution" / "2026-08-06"
    exec_dir.mkdir(parents=True)
    (exec_dir / "orders.json").write_text(json.dumps([{
        "signal_date": "2026-08-05", "execution_date": "2026-08-06",
        "challenger_id": "c0", "symbol": "600001", "side": "BUY",
        "state": "ORDER_PRECOMMITTED", "package_sha": "c" * 64,
        "order_id": "0123456789abcdef",
    }]), encoding="utf-8")
    # replay_all reads the REAL execution zone — a hermetic run without
    # precommitted events must not crash the contract (held_keys = {}).
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    replay_calls = {}
    real_replay_all = shadow_events.replay_all

    def replay_with_cutoff(zone, as_of_date=None):
        replay_calls["as_of_date"] = as_of_date
        return real_replay_all(zone, as_of_date=as_of_date)

    monkeypatch.setattr(shadow_events, "replay_all", replay_with_cutoff)
    ok, lines = web_app._verify_alpha_signal_precommit_result(
        None, None, run_options={"datestr": "20260806"})
    assert ok, lines
    assert any("buys=1" in ln for ln in lines)
    assert replay_calls["as_of_date"] == "2026-08-05"


# ── A5 vacuous contracts (v5.5.3 first-run discovery) ──────────────────
# The bootstrap night (2026-08-05) reconciles/navs execution dates with
# zero precommitted orders and zero fills.  The nav() script no-ops
# (reason=no_fills) without writing a NAV day and reconcile returns
# no_orders_for_execution_date without an events log — both verifiers
# must PASS vacuously (nothing to prove), mirroring the sell verifier's
# no_open_positions branch.  Non-vacuous cases must keep FAILing.


def test_reconcile_verifier_vacuous_pass_no_orders(monkeypatch, tmp_path):
    root = tmp_path / "fe"
    root.mkdir(parents=True)
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_execution_reconcile_result(
        None, None, run_options={"datestr": "20260805"})
    assert ok, lines
    assert any("no_orders_vacuous" in ln for ln in lines)


def test_reconcile_verifier_still_fails_orders_without_events(
        monkeypatch, tmp_path):
    root = tmp_path / "fe"
    exec_dir = root / "execution" / "2026-08-05"
    exec_dir.mkdir(parents=True)
    (exec_dir / "orders.json").write_text(json.dumps([{
        "signal_date": "2026-08-04", "execution_date": "2026-08-05",
        "challenger_id": "c0", "symbol": "600001", "side": "BUY",
        "state": "ORDER_PRECOMMITTED", "package_sha": "b" * 64,
        "order_id": "0123456789abcdef",
    }]), encoding="utf-8")
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_execution_reconcile_result(
        None, None, run_options={"datestr": "20260805"})
    assert not ok, lines  # orders exist but no events log — unproven
    assert any("no_events_log" in ln for ln in lines)


def test_nav_verifier_vacuous_pass_no_fills(monkeypatch, tmp_path):
    root = tmp_path / "fe"
    root.mkdir(parents=True)
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_nav_result(
        None, None, run_options={"datestr": "20260805"})
    assert ok, lines
    assert any("no_fills_vacuous" in ln for ln in lines)


def test_nav_verifier_still_fails_fills_without_day(monkeypatch, tmp_path):
    root = tmp_path / "fe"
    events_dir = root / "execution" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "2026-08-05.jsonl").write_text(json.dumps({
        "event_type": "BUY_FILLED", "order_id": "0123456789abcdef",
        "challenger_id": "c0", "symbol": "600001",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    ok, lines = web_app._verify_alpha_signal_nav_result(
        None, None, run_options={"datestr": "20260805"})
    assert not ok, lines  # fills exist but no NAV day — real defect
    assert any("no_nav_summary" in ln for ln in lines)


def test_latest_sealed_package_multiple_packages(monkeypatch, tmp_path):
    """v5.5.3 (2026-08-06): the sell verifier's latest-package scan crashed
    with AttributeError ('dict' object has no attribute 'parent') once a
    SECOND SEALED package existed — best[0] is the manifest DICT, the
    Path lives at best[1].  Production hit this on the 08-06 run (three
    sealed packages: 08-04 / 08-05 / 08-06)."""
    root = tmp_path / "forward_shadow_evidence"
    for date_iso in ("2026-08-04", "2026-08-05", "2026-08-06"):
        pkg_dir = root / "packages" / date_iso
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "signal_package_manifest.json").write_text(json.dumps({
            "package_status": "SEALED", "signal_date": date_iso,
            "execution_date": "2026-08-05",
        }))
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    man, mpath = web_app._latest_sealed_package()
    assert mpath.parent.name == "2026-08-06"
    assert man["signal_date"] == "2026-08-06"
    # both manifests were candidates — the comparison must not crash
    assert man["package_status"] == "SEALED"


def test_sealed_package_for_execution_prefers_latest_revision(monkeypatch, tmp_path):
    """Revision scan: for one execution date the NEWEST matching revision
    wins; a non-matching execution_date package is never picked."""
    root = tmp_path / "forward_shadow_evidence"
    pkg_dir = root / "packages" / "2026-08-05"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "signal_package_manifest.json").write_text(json.dumps({
        "package_status": "SEALED", "signal_date": "2026-08-05",
        "execution_date": "2026-08-06",
    }))
    (pkg_dir / "revision_2").mkdir()
    (pkg_dir / "revision_2" / "signal_package_manifest.json").write_text(
        json.dumps({
            "package_status": "SEALED", "signal_date": "2026-08-05",
            "execution_date": "2026-08-06",
        }))
    # a package targeting a different execution day must be ignored
    other = root / "packages" / "2026-08-06"
    other.mkdir()
    (other / "signal_package_manifest.json").write_text(json.dumps({
        "package_status": "SEALED", "signal_date": "2026-08-06",
        "execution_date": "2026-08-07",
    }))
    monkeypatch.setattr(web_app, "FORWARD_EVIDENCE_ROOT", root)
    man, mpath = web_app._sealed_package_for_execution("2026-08-06")
    assert mpath.parent.name == "revision_2"
    assert man["signal_date"] == "2026-08-05"
