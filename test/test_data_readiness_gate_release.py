"""DataReadinessGate 对接 ASDC Release Gate 的测试套件。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from scripts.ops.data_readiness_gate import (
    DataReadinessGate,
    PipelineReadinessGate,
    PreScoreGate,
)


def test_prescore_gate_blocks_when_asdc_release_blocked():
    mock_engine = MagicMock()
    # 模拟其他常规检查均能正常查询
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = 5000
    mock_conn.execute.return_value.fetchone.return_value = ("20261106",)
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # 模拟 ADCClient
    mock_adc_client = MagicMock()
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": False,
        "is_production_eligible": False,
        "is_research_eligible": True,
        "state": "BLOCKED",
        "release_id": "rel_20261106_blocked",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "critical_datasets_ok": False,
        "degraded_optional_datasets": [],
        "blocked_datasets": ["ALL_BLOCKED_BY_GATE"],
        "reason": "ASDC 发布已被质量门禁阻断 (BLOCKED)",
    }

    gate = PreScoreGate(mock_engine, adc_client=mock_adc_client)
    res = gate.all_checks(date(2026, 11, 6))

    assert res["status"] == "BLOCKED"
    assert res["passed"] is False
    assert "asdc_release_gate" in res["failed_critical"]
    assert res["data_certification"] == "BLOCKED"


def test_prescore_gate_blocks_when_asdc_release_missing():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = 5000
    mock_conn.execute.return_value.fetchone.return_value = ("20261106",)
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_adc_client = MagicMock()
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": False,
        "is_production_eligible": False,
        "is_research_eligible": True,
        "state": "RELEASE_MISSING",
        "release_id": None,
        "reason": "ASDC 尚未对交易日 20261106 发布 CERTIFIED_DAILY 聚合版本",
    }

    gate = PreScoreGate(mock_engine, adc_client=mock_adc_client)
    res = gate.all_checks(date(2026, 11, 6))

    assert res["status"] == "BLOCKED"
    assert res["passed"] is False
    assert "asdc_release_gate" in res["failed_critical"]


def test_prescore_gate_allows_when_asdc_release_ready():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = 5200
    mock_conn.execute.return_value.fetchone.return_value = ("20261106",)
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_adc_client = MagicMock()
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": True,
        "is_production_eligible": True,
        "is_research_eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_ready",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "critical_datasets_ok": True,
        "degraded_optional_datasets": [],
        "blocked_datasets": [],
        "reason": "正式日版认证通过",
    }

    gate = PreScoreGate(mock_engine, adc_client=mock_adc_client)
    res = gate.all_checks(date(2026, 11, 6))

    # Release 门禁必须通过
    release_check = next(c for c in res["checks"] if c["check"] == "asdc_release_gate")
    assert release_check["passed"] is True
    assert release_check["release_id"] == "rel_20261106_ready"
    assert res["data_certification"] == "CERTIFIED_DAILY"


def test_prescore_gate_skip_flag_marks_unverified_research():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = 5200
    mock_conn.execute.return_value.fetchone.return_value = ("20261106",)
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    gate = PreScoreGate(mock_engine, skip_release_gate=True)
    res = gate.all_checks(date(2026, 11, 6))

    release_check = next(c for c in res["checks"] if c["check"] == "asdc_release_gate")
    assert release_check["passed"] is True
    assert release_check["severity"] == "warning"
    assert res["data_certification"] == "UNVERIFIED_RESEARCH_ONLY"


def test_pipeline_readiness_gate_propagates_blocked_status():
    mock_engine = MagicMock()
    mock_adc_client = MagicMock()
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": False,
        "state": "BLOCKED",
        "reason": "ASDC 发布门禁阻断",
    }

    pipe_gate = PipelineReadinessGate(mock_engine)
    # 替换其中的 PreScoreGate 为包含 mock adc_client 的实例
    with pytest.MonkeyPatch.context() as m:
        m.setattr(
            "scripts.ops.data_readiness_gate.PreScoreGate",
            lambda eng: PreScoreGate(eng, adc_client=mock_adc_client),
        )
        res = pipe_gate.check_market_data_complete(date(2026, 11, 6))
        assert res["passed"] is False
        assert res["status"] == "BLOCKED"
        assert "asdc_release_gate" in res["failed"]
