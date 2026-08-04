import os
import json
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web import app as web_app
from scripts.ops import feishu_notifier
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
