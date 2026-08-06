"""PR #195: integrated batch audit — anomaly-only notification.

DB-free coverage of run_integrated_audit (the legacy run_audit, the
digest delivery check, and the Feishu send are all mocked):

- healthy days stay silent; only non-PASS days emit ONE card
- the digest delivery row is the only notification contract — all other
  tasks run with require_notifications=False, so pure compute/persist
  jobs are never mis-flagged NOTIFICATION_MISSING
- historical-safe replays skip the digest check and stay silent;
  an explicit reissue restores the check and prefixes the card
- _digest_delivery_row: OK / NOTIFICATION_MISSING / NOTIFICATION_FAILED /
  NOTIFICATION_AUDIT_FAILED states from app_notification_delivery
"""

from __future__ import annotations

import pytest

import scripts.ops.run_integrated_batch_audit as audit_mod


# ── fake engine for _digest_delivery_row ────────────────────────────────


class _FakeRows:
    def __init__(self, row):
        self._row = dict(row) if row else None

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        if isinstance(self.row, Exception):
            raise self.row
        return _FakeRows(self.row)


class _FakeEngine:
    def __init__(self, row):
        self.conn = _FakeConn(row)
        self.disposed = False

    def connect(self):
        return self.conn

    def dispose(self):
        self.disposed = True


def _install_delivery(monkeypatch, row):
    engine = _FakeEngine(row)
    monkeypatch.setattr(audit_mod, "create_engine", lambda url: engine)
    return engine


# ── _digest_delivery_row state machine ──────────────────────────────────


def test_digest_delivery_row_ok(monkeypatch):
    engine = _install_delivery(
        monkeypatch, {"status": "ok", "reason": "delivered"})
    row = audit_mod._digest_delivery_row("20260805")
    assert row["task_name"] == "integrated_strategy_digest_delivery"
    assert row["status"] == "OK"
    assert row["notification_status"] == "ok"
    assert row["replay_required"] == 0
    assert "20260805" in str(engine.conn.calls[0][1]["business_date"])
    assert engine.conn.calls[0][1]["task_name"] == "trusted_strategy_performance_review"


def test_digest_delivery_row_missing(monkeypatch):
    _install_delivery(monkeypatch, None)
    row = audit_mod._digest_delivery_row("20260805")
    assert row["status"] == "NOTIFICATION_MISSING"
    assert "未发现投递审计记录" in row["reason"]


def test_digest_delivery_row_failed_status(monkeypatch):
    _install_delivery(
        monkeypatch, {"status": "queued", "reason": "retry"})
    row = audit_mod._digest_delivery_row("20260805")
    assert row["status"] == "NOTIFICATION_FAILED"
    assert row["notification_status"] == "queued"


def test_digest_delivery_row_audit_failure_is_fail_open(monkeypatch):
    _install_delivery(monkeypatch, RuntimeError("db down"))
    row = audit_mod._digest_delivery_row("20260805")
    assert row["status"] == "NOTIFICATION_AUDIT_FAILED"
    assert "无法读取综合策略简报投递状态" in row["reason"]


# ── run_integrated_audit behavior ───────────────────────────────────────


def _install_audit(monkeypatch, *, audit_rows=None, digest_row=None, trading_day=True):
    calls = {
        "run_audit": None,
        "sends": [],
        "digest_checks": 0,
    }
    audit_rows = audit_rows if audit_rows is not None else [
        {"task_name": "t1", "expected_time": "21:30", "status": "OK",
         "reason": "ok", "replay_required": 0},
    ]

    def fake_run_audit(business_date, *, notify_feishu, require_notifications, historical_reissue):
        calls["run_audit"] = {
            "business_date": business_date, "notify_feishu": notify_feishu,
            "require_notifications": require_notifications,
            "historical_reissue": historical_reissue,
        }
        # Mirror the legacy summary contract format_audit_notification needs.
        bad = [r for r in audit_rows if r.get("status") not in audit_mod.HEALTHY_STATUSES]
        return {
            "business_date": business_date,
            "trading_day": trading_day,
            "status": "ACTION_REQUIRED" if bad else "PASS",
            "replay_required_count": sum(int(r.get("replay_required") or 0) for r in bad),
            "rows": [dict(r) for r in audit_rows],
        }

    monkeypatch.setattr(audit_mod, "run_audit", fake_run_audit)
    if digest_row is None:
        digest_row = {"task_name": "integrated_strategy_digest_delivery",
                      "expected_time": "22:20", "status": "OK",
                      "reason": "综合策略简报投递成功", "replay_required": 0}

    def fake_digest_row(business_date):
        calls["digest_checks"] += 1
        return dict(digest_row)

    monkeypatch.setattr(audit_mod, "_digest_delivery_row", fake_digest_row)

    def fake_send(engine, content, *, business_date, notification_type, task_name, dedupe_key):
        calls["sends"].append({
            "content": content, "business_date": business_date,
            "notification_type": notification_type, "task_name": task_name,
            "dedupe_key": dedupe_key,
        })
        return True, "ok"

    monkeypatch.setattr(audit_mod, "send_feishu_text_audited", fake_send)
    return calls


def test_healthy_day_silent_even_with_notify_flag(monkeypatch):
    calls = _install_audit(monkeypatch)
    summary = audit_mod.run_integrated_audit("20260805", notify_feishu=True)
    assert summary["notification_mode"] == "ANOMALY_ONLY"
    assert summary["digest_delivery_expected"] is True
    assert summary["attention_count"] == 0
    assert summary["status"] == "PASS"
    assert summary["notify_result"] == "skipped_healthy"
    assert calls["sends"] == []
    assert calls["digest_checks"] == 1


def test_anomaly_day_sends_single_incident_card(monkeypatch):
    calls = _install_audit(monkeypatch, audit_rows=[
        {"task_name": "t1", "status": "OK", "reason": "ok", "replay_required": 0},
        {"task_name": "t2", "status": "FAILED", "reason": "脚本崩溃", "replay_required": 1},
    ])
    summary = audit_mod.run_integrated_audit("20260805", notify_feishu=True)
    assert summary["attention_count"] == 1
    assert summary["status"] == "ACTION_REQUIRED"
    assert summary["notify_result"] == "ok"
    assert len(calls["sends"]) == 1
    send = calls["sends"][0]
    assert send["notification_type"] == "ops_daily_batch_audit_incident"
    assert send["task_name"] == "ops_daily_batch_audit"
    assert send["dedupe_key"] == "ops_daily_batch_audit_incident:20260805"
    assert "t2" in send["content"]


def test_anomaly_day_without_notify_flag_stays_silent(monkeypatch):
    calls = _install_audit(monkeypatch, audit_rows=[
        {"task_name": "t2", "status": "FAILED", "reason": "x", "replay_required": 1},
    ])
    summary = audit_mod.run_integrated_audit("20260805", notify_feishu=False)
    assert summary["attention_count"] == 1
    assert summary["notify_result"] is None
    assert calls["sends"] == []


def test_historical_safe_skips_digest_check_and_stays_silent(monkeypatch):
    calls = _install_audit(monkeypatch, audit_rows=[
        {"task_name": "t2", "status": "FAILED", "reason": "x", "replay_required": 1},
    ])
    summary = audit_mod.run_integrated_audit(
        "20260805", notify_feishu=True, historical_safe=True)
    assert summary["digest_delivery_expected"] is False
    assert calls["digest_checks"] == 0
    assert summary["notify_result"] == "skipped_historical_safe"
    assert calls["sends"] == []


def test_historical_reissue_restores_digest_check_and_prefixes_card(monkeypatch):
    calls = _install_audit(monkeypatch, audit_rows=[
        {"task_name": "t2", "status": "FAILED", "reason": "x", "replay_required": 1},
    ])
    summary = audit_mod.run_integrated_audit(
        "20260805", notify_feishu=True,
        historical_safe=True, historical_reissue=True)
    assert summary["digest_delivery_expected"] is True
    assert calls["digest_checks"] == 1
    assert len(calls["sends"]) == 1
    assert calls["sends"][0]["content"].startswith("【历史补发】")


def test_non_trading_day_skips_digest_check(monkeypatch):
    calls = _install_audit(monkeypatch, trading_day=False)
    summary = audit_mod.run_integrated_audit("20260803", notify_feishu=True)
    assert summary["digest_delivery_expected"] is False
    assert calls["digest_checks"] == 0
    assert calls["sends"] == []


def test_missing_digest_delivery_makes_day_anomalous(monkeypatch):
    calls = _install_audit(
        monkeypatch,
        digest_row={"task_name": "integrated_strategy_digest_delivery",
                    "status": "NOTIFICATION_MISSING",
                    "reason": "综合策略简报未发现投递审计记录", "replay_required": 0},
    )
    summary = audit_mod.run_integrated_audit("20260805", notify_feishu=True)
    assert summary["attention_count"] == 1
    # The digest row is appended AFTER the legacy summary — the status must
    # be recomputed so a missing digest alone turns the day ACTION_REQUIRED.
    assert summary["status"] == "ACTION_REQUIRED"
    assert len(calls["sends"]) == 1  # the missing digest is itself the anomaly
    assert "NOTIFICATION_MISSING" in calls["sends"][0]["content"]


def test_run_audit_disables_legacy_notification_requirements(monkeypatch):
    """Pure compute/persist jobs must never be flagged NOTIFICATION_MISSING."""
    calls = _install_audit(monkeypatch)
    audit_mod.run_integrated_audit("20260805", notify_feishu=False)
    assert calls["run_audit"]["require_notifications"] is False
    assert calls["run_audit"]["notify_feishu"] is False
    # The digest delivery check is the ONLY notification contract.
    assert calls["digest_checks"] == 1
