from __future__ import annotations

import inspect
from datetime import datetime

import pytest

from scripts.ops import feishu_notifier as notifier


def _event(**overrides):
    values = {
        "event_type": "task_failed",
        "business_date": "20260715",
        "title": "任务执行失败",
        "dedupe_key": "task_failed:42:terminal",
        "severity": "RED",
        "task_name": "trusted_strategy_candidates",
        "run_id": "queue-42-attempt-2",
        "event_id": "evt-42",
        "facts": {"错误类型": "VERIFICATION", "重试": "已耗尽"},
        "details": ("exit_code=1",),
        "actions": ("检查任务日志",),
        "artifact_paths": ("/tmp/task.log",),
        "occurred_at": datetime(2026, 7, 15, 22, 10, 0),
    }
    values.update(overrides)
    return notifier.NotificationEvent(**values)


def test_notification_event_requires_identity_fields():
    with pytest.raises(ValueError, match="dedupe_key"):
        _event(dedupe_key="")


def test_feishu_interactive_payload_has_typed_sections_and_severity_color():
    payload = notifier.build_feishu_interactive_payload(
        _event(actions=("检查任务日志", "查看任务详情|https://example.test/admin/job/42"))
    )

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["schema"] == "2.0"
    assert payload["card"]["header"]["template"] == "red"
    assert payload["card"]["header"]["title"]["content"] == "任务执行失败"
    content = payload["card"]["body"]["elements"][0]["content"]
    assert "**错误类型：** VERIFICATION" in content
    assert "**运行ID：** queue-42-attempt-2" in content
    assert "**建议动作**" in content
    assert "`/tmp/task.log`" in content
    action = payload["card"]["body"]["elements"][1]
    assert action["tag"] == "action"
    assert action["actions"][0]["url"].endswith("/admin/job/42")


@pytest.mark.parametrize("reason", ["http_error=400", "http_status=401", "http_error=404"])
def test_configuration_4xx_is_permanent(reason):
    assert notifier._is_permanent_delivery_failure(reason) is True


@pytest.mark.parametrize("reason", ["http_error=408", "http_error=429", "http_error=500", "url_error=timeout"])
def test_transient_delivery_failure_is_retryable(reason):
    assert notifier._is_permanent_delivery_failure(reason) is False


def test_publish_notification_dedupes_success(monkeypatch):
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda engine, channel, key: True)
    monkeypatch.setattr(notifier, "load_feishu_webhook", lambda engine: (_ for _ in ()).throw(AssertionError()))

    result = notifier.publish_notification(object(), _event())

    assert result.ok is True
    assert result.status == "DEDUPED"
    assert result.deduped is True


def test_publish_notification_concurrent_reservation_prevents_second_send(monkeypatch):
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda *args: False)
    monkeypatch.setattr(notifier, "_reserve_delivery", lambda *args: False)
    monkeypatch.setattr(notifier, "load_feishu_webhook", lambda engine: (_ for _ in ()).throw(AssertionError()))

    result = notifier.publish_notification(object(), _event())

    assert result.status == "IN_PROGRESS"
    assert result.deduped is True
    assert result.queued is True


def test_publish_notification_success_records_delivery(monkeypatch):
    recorded = []
    queued = []
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda *args: False)
    monkeypatch.setattr(notifier, "_reserve_delivery", lambda *args: True)
    monkeypatch.setattr(notifier, "load_feishu_webhook", lambda engine: "https://example.test/hook")
    monkeypatch.setattr(notifier, "_send_feishu_payload", lambda url, payload: (True, "ok"))
    monkeypatch.setattr(notifier, "record_notification_delivery", lambda engine, **kwargs: recorded.append(kwargs))
    monkeypatch.setattr(notifier, "enqueue_notification_retry", lambda *args, **kwargs: queued.append(kwargs))

    result = notifier.publish_notification(object(), _event())

    assert result.status == "SENT"
    assert recorded[0]["status"] == "ok"
    assert recorded[0]["event_id"] == "evt-42"
    assert recorded[0]["run_id"] == "queue-42-attempt-2"
    assert len(recorded[0]["content_hash"]) == 64
    assert queued == []


def test_publish_notification_permanent_4xx_goes_directly_to_notification_failed(monkeypatch):
    queued = []
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda *args: False)
    monkeypatch.setattr(notifier, "_reserve_delivery", lambda *args: True)
    monkeypatch.setattr(notifier, "load_feishu_webhook", lambda engine: "https://example.test/hook")
    monkeypatch.setattr(notifier, "_send_feishu_payload", lambda url, payload: (False, "http_error=403"))
    monkeypatch.setattr(notifier, "record_notification_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr(notifier, "enqueue_notification_retry", lambda *args, **kwargs: queued.append(kwargs))

    result = notifier.publish_notification(object(), _event())

    assert result.ok is False
    assert result.status == "NOTIFICATION_FAILED"
    assert result.queued is False
    assert queued[0]["dead_letter"] is True
    assert queued[0]["payload"]["msg_type"] == "interactive"
    assert len(queued[0]["content_hash"]) == 64


def test_outbox_contract_uses_composite_uniqueness_claim_lease_and_bounded_backoff():
    schema_source = inspect.getsource(notifier.ensure_notification_delivery_table)
    worker_source = inspect.getsource(notifier.process_notification_outbox)
    enqueue_source = inspect.getsource(notifier.enqueue_notification_retry)
    reserve_source = inspect.getsource(notifier._reserve_delivery)

    assert "uniq_notification_delivery_channel_dedupe (channel_key,dedupe_key)" in schema_source
    assert "uniq_notification_outbox_channel_dedupe (channel_key,dedupe_key)" in schema_source
    assert "FOR UPDATE SKIP LOCKED" in worker_source
    assert "status='SENDING'" in worker_source
    assert "lease_until" in worker_source
    assert "content_hash" in schema_source
    assert "run_id" in schema_source
    assert "WHERE id=:id AND claimed_by=:worker_id" in worker_source
    assert "{1: 5, 2: 15}" in worker_source
    assert "DATE_ADD(NOW(), INTERVAL 1 MINUTE)" in enqueue_source
    assert "INSERT IGNORE" in reserve_source


def test_legacy_audited_sender_signature_remains_compatible():
    signature = inspect.signature(notifier.send_feishu_text_audited)
    assert list(signature.parameters) == [
        "engine", "content", "business_date", "notification_type", "task_name", "dedupe_key"
    ]
