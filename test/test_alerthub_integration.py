"""Unit and integration tests for AlertHub adapter and CYY notification categorization."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.ops.alerthub_adapter import (
    classify_notification,
    clean_business_message,
    ensure_cyy_prefix,
    format_batch_task_message,
    is_batch_task_notification_allowed,
    save_to_local_buffer,
    send_alerthub_event,
)
from scripts.ops import feishu_notifier as notifier


def test_ensure_cyy_prefix():
    assert ensure_cyy_prefix("策略订单草案已生成") == "【CYY】策略订单草案已生成"
    assert ensure_cyy_prefix("【CYY】策略订单草案已生成") == "【CYY】策略订单草案已生成"
    assert ensure_cyy_prefix("[CYY]系统通知") == "[CYY]系统通知"
    assert ensure_cyy_prefix("") == "【CYY】系统通知"


def test_classify_notification():
    # Batch tasks
    assert classify_notification("task_failed") == "batch_task"
    assert classify_notification("task_blocked") == "batch_task"
    assert classify_notification("scheduler_enqueue_failed") == "batch_task"
    assert classify_notification("ops_daily_batch_audit_incident") == "batch_task"
    assert classify_notification("", notification_type="task_failed") == "batch_task"

    # Business
    assert classify_notification("strategy_orders_ready") == "business"
    assert classify_notification("strategy_orders_no_rebalance") == "business"
    assert classify_notification("trusted_strategy_performance_review") == "business"
    assert classify_notification("daily_strategy_health") == "business"
    assert classify_notification("dual_strategy_analysis") == "business"
    assert classify_notification("trusted_strategy_shadow_monitor") == "business"


def test_is_batch_task_notification_allowed():
    # Only failures allowed
    assert is_batch_task_notification_allowed("task_failed") is True
    assert is_batch_task_notification_allowed("task_blocked") is True
    assert is_batch_task_notification_allowed("scheduler_enqueue_failed") is True

    # Non-failures disallowed
    assert is_batch_task_notification_allowed("task_recovered") is False
    assert is_batch_task_notification_allowed("task_retrying") is False
    assert is_batch_task_notification_allowed("task_completion", history_status="SUCCESS") is False
    assert is_batch_task_notification_allowed("task_completion", history_status="SKIPPED") is False


def test_format_batch_task_message():
    dt = datetime(2026, 10, 23, 20, 30, 0)
    msg = format_batch_task_message(
        task_name="东方财富行情采集",
        error_name="DATA_READINESS",
        error_detail="上游数据文件缺失，连续重试3次失败",
        occurred_at=dt,
        business_date="20261023",
        failure_stage="数据就绪检测",
    )
    assert "**发出时间**：2026-10-23 20:30:00" in msg
    assert "**任务名称**：东方财富行情采集" in msg
    assert "**错误名称**：`DATA_READINESS`" in msg
    assert "**业务日期**：20261023" in msg
    assert "**失败阶段**：数据就绪检测" in msg
    assert "**错误详情**：" in msg
    assert "上游数据文件缺失" in msg


def test_clean_business_message():
    raw_message = (
        "【交易指令卡】\n"
        "信号日：2026-10-23\n"
        "**任务：** trusted_strategy_candidates\n"
        "**队列ID：** 108\n"
        "**运行ID：** task:108:1\n"
        "**耗时：** 15 秒\n"
        "生产外壳：稳健选股内核 (baseline)\n"
        "风险档位：MEDIUM / 目标仓位 70%\n"
        "候选 Top5：\n"
        "- 000001 平安银行 (15.0%)\n"
        "- 600036 招商银行 (12.0%)"
    )
    cleaned = clean_business_message(raw_message)
    assert "**队列ID：**" not in cleaned
    assert "**运行ID：**" not in cleaned
    assert "**耗时：**" not in cleaned
    assert "**任务：**" not in cleaned
    assert "信号日：2026-10-23" in cleaned
    assert "生产外壳：稳健选股内核 (baseline)" in cleaned
    assert "候选 Top5：" in cleaned
    assert "000001 平安银行" in cleaned


def test_save_to_local_buffer(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(Path, "home", lambda: Path(tmpdir))
        payload = {"title": "Test Buffer", "severity": "P1"}
        path = save_to_local_buffer(payload, "idemp-001")
        assert path is not None
        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["payload"]["title"] == "Test Buffer"
        assert data["idempotency_key"] == "idemp-001"


def test_send_alerthub_event_success_mock(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 202
    mock_resp.read.return_value = json.dumps({
        "success": True,
        "event_id": "evt_mock_001",
        "incident_id": "inc_mock_001",
    }).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, reason, event_id = send_alerthub_event(
            title="策略订单生成完成",
            message="生成10笔调仓买卖订单",
            category="business",
            severity="INFO",
            component="strategy",
            reason_code="ORDERS_READY",
        )
        assert ok is True
        assert reason == "ok"
        assert event_id == "evt_mock_001"


def test_send_alerthub_event_unreachable_buffers_locally(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(Path, "home", lambda: Path(tmpdir))
        monkeypatch.setenv("ALERTHUB_URL", "http://unreachable.test:9999")
        ok, reason, event_id = send_alerthub_event(
            title="关键故障",
            message="数据崩溃",
            category="batch_task",
            severity="P0",
            timeout=1,
        )
        assert ok is False
        assert "connection_error" in reason or "http" in reason
        buffer_files = list((Path(tmpdir) / ".alerthub" / "buffer").glob("event_cyy_*.json"))
        assert len(buffer_files) == 1


def test_publish_notification_skips_recovered_and_retrying(monkeypatch):
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda *args: False)
    monkeypatch.setattr(notifier, "_reserve_delivery", lambda *args: True)

    # 1. Recovered task -> skipped
    recovered_event = notifier.NotificationEvent(
        event_type="task_recovered",
        business_date="20261023",
        title="任务已恢复",
        dedupe_key="task_recovered:42",
        task_name="sina_score",
    )
    res_rec = notifier.publish_notification(object(), recovered_event)
    assert res_rec.ok is True
    assert res_rec.status == "SKIPPED"

    # 2. Retrying task -> skipped
    retrying_event = notifier.NotificationEvent(
        event_type="task_retrying",
        business_date="20261023",
        title="任务重试中",
        dedupe_key="task_retrying:42",
        task_name="sina_score",
    )
    res_retry = notifier.publish_notification(object(), retrying_event)
    assert res_retry.ok is True
    assert res_retry.status == "SKIPPED"


def test_publish_notification_routes_failed_batch_task_to_alerthub(monkeypatch):
    monkeypatch.setattr(notifier, "ensure_notification_delivery_table", lambda engine: None)
    monkeypatch.setattr(notifier, "_already_delivered", lambda *args: False)
    monkeypatch.setattr(notifier, "_reserve_delivery", lambda *args: True)
    monkeypatch.setattr(notifier, "record_notification_delivery", lambda *args, **kwargs: None)

    dispatched = []
    monkeypatch.setattr(
        notifier,
        "_send_via_alerthub_event",
        lambda event: dispatched.append(event) or (True, "ok"),
    )

    failed_event = notifier.NotificationEvent(
        event_type="task_failed",
        business_date="20261023",
        title="批量任务执行失败",
        dedupe_key="task_failed:42:terminal",
        severity="ERROR",
        task_name="sina_picture",
        facts={"任务": "新浪行情图片采集", "错误分类": "DATA_READINESS", "失败阶段": "数据准备"},
        details=("行情接口返回空列表",),
    )

    res = notifier.publish_notification(object(), failed_event)
    assert res.ok is True
    assert res.status == "SENT"
    assert len(dispatched) == 1
    assert dispatched[0].event_type == "task_failed"
