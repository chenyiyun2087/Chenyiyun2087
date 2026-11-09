"""Phase 2B-Hardening 与 Phase 2C 闭环验收测试套件。"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cross_project.adc_client import (
    ADCClient,
    ADCConnectionError,
    ADCReadOnlyViolationError,
)
from cross_project.execution_context import (
    ExecutionContext,
    ProductionAdmissionBlockedError,
)
from scoreRank.core.db_io import fetch_bars_batch
from sina.live_tracker.live_tracker_db import get_latest_price


def test_production_context_requires_eligible_release():
    # 模拟未发布或被阻断的 release_gate_result
    blocked_gate = {
        "eligible": False,
        "state": "BLOCKED",
        "reason": "PIT 质量门禁阻断",
    }
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        ExecutionContext.create_production(20261106, blocked_gate)
    assert "未获得正式发布资格" in str(exc_info.value)


def test_production_context_admission_write_guard():
    # 合规通过的 Release 创建生产上下文
    ready_gate = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_ready",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    prod_ctx = ExecutionContext.create_production(20261106, ready_gate)
    # 正式模式允许写入生产表
    prod_ctx.validate_for_production_write("score_rank_daily")

    # 研究模式严格阻断向生产资源写入
    res_ctx = ExecutionContext.create_research(20261106, ready_gate)
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        res_ctx.validate_for_production_write("score_rank_daily")
    assert "严禁向正式生产资源 [score_rank_daily] 写入数据" in str(exc_info.value)


def test_adc_client_blocks_root_user_in_readonly_mode(monkeypatch):
    monkeypatch.delenv("ADC_ALLOW_ROOT_RO", raising=False)
    client = ADCClient(host="127.0.0.1", user="root", readonly_only=True)
    with pytest.raises(ADCReadOnlyViolationError) as exc_info:
        client._resolve_config()
    assert "安全红线阻断：跨项目只读客户端禁止复用超级管理员账号 'root'" in str(exc_info.value)


def test_adc_client_missing_user_fails_without_fallback_root(monkeypatch):
    monkeypatch.delenv("ADC_MYSQL_USER", raising=False)
    monkeypatch.delenv("ASDC_MYSQL_USER", raising=False)
    monkeypatch.delenv("ADC_MYSQL_RO_USER", raising=False)
    monkeypatch.delenv("ASDC_MYSQL_RO_USER", raising=False)

    fake_cfg = "[mysql]\nhost = 127.0.0.1\nport = 3306\n"
    with patch("builtins.open", MagicMock()), \
         patch("configparser.ConfigParser.read", MagicMock()), \
         patch("configparser.ConfigParser.has_section", return_value=True), \
         patch("configparser.ConfigParser.get", return_value=None), \
         patch("os.path.exists", return_value=True):
        client = ADCClient(config_path="/fake/etl.ini", readonly_only=True)
        with pytest.raises(ADCConnectionError) as exc_info:
            client._resolve_config()
        assert "严禁隐式回退 root 账号" in str(exc_info.value)


def test_fetch_bars_batch_fails_closed_in_production():
    # 在正式生产模式下，ADCClient 报错必须直接向外抛出异常，绝不静默回退 SQL
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("网络断开")):
        with pytest.raises(RuntimeError) as exc_info:
            fetch_bars_batch(
                engine=None,
                symbols=["000001"],
                adj_type="qfq",
                start_date="2026-11-06",
                execution_context=None,  # 默认生产模式
            )
        assert "正式策略生产取数阻断" in str(exc_info.value)
        assert "严格禁止静默回退未认证 SQL" in str(exc_info.value)


def test_live_tracker_blocks_zero_price():
    # 模拟最新价格未查到的异常场景，禁止返回 0.0 导致资产断崖失真
    with patch("sina.live_tracker.live_tracker_db.get_latest_prices_from_kline", return_value={}), \
         patch("sina.live_tracker.live_tracker_db.get_db_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        with pytest.raises(ValueError) as exc_info:
            get_latest_price("000001")
        assert "拒绝返回伪造的 0.0 价格" in str(exc_info.value)


def test_pipeline_readiness_blocks_order_drafts_when_asdc_blocked():
    """验证当 ASDC Release 处于 BLOCKED 时，PipelineReadinessGate 判定 market_data_complete 为 critical 失败，
    整体状态阻断为 BLOCKED，绝不放行订单草案生成。
    """
    from datetime import date
    from scripts.ops.data_readiness_gate import PipelineReadinessGate, PreScoreGate

    mock_engine = MagicMock()
    mock_pre_gate = MagicMock()
    mock_pre_gate.all_checks.return_value = {
        "passed": False,
        "status": "BLOCKED",
        "failed_critical": ["asdc_release_gate"],
        "checks": [
            {
                "check": "asdc_release_gate",
                "passed": False,
                "severity": "critical",
                "detail": "ASDC 正式发布处于 BLOCKED",
            }
        ],
    }

    pipeline_gate = PipelineReadinessGate(mock_engine)
    with patch("scripts.ops.data_readiness_gate.PreScoreGate", return_value=mock_pre_gate):
        result = pipeline_gate.check_market_data_complete(date(2026, 11, 6))
        assert result["passed"] is False
        assert result["severity"] == "critical"
        assert "asdc_release_gate" in result["failed"]

