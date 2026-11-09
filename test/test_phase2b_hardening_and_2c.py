"""Phase 2B-Hardening 与 Phase 2C/2D 闭环验收与安全测试套件。"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backtest.src.backtest_engine.datafeed.tushare_feed import TushareDailyFeed
from cross_project.adc_client import (
    ADCClient,
    ADCConnectionError,
    ADCReadOnlyViolationError,
    ADCReleaseClient,
)
from cross_project.execution_context import (
    ExecutionContext,
    ProductionAdmissionBlockedError,
)
from scoreRank.cli.run_daily import _register_snapshot_and_layers, save_scores_to_db
from scoreRank.core.db_io import fetch_bars_batch
from scripts.ops.data_readiness_gate import PipelineReadinessGate, PreScoreGate
from sina.live_tracker.live_tracker_db import get_latest_price, get_latest_prices_from_kline


# ---------------------------------------------------------------------------
# P0-01: READY 成功路径端到端测试与字段契约对齐
# ---------------------------------------------------------------------------

def test_ready_success_path_end_to_end_context_creation():
    """验证当 ASDC 发布门禁 READY 时，PreScoreGate 与 ExecutionContext.create_production 字段完美对齐并成功创建。"""
    mock_engine = MagicMock()
    mock_adc_client = MagicMock()
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": True,
        "is_production_eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_20261106_01",
        "pit_snapshot_id": "pit_20261106_01",
        "critical_datasets_ok": True,
        "degraded_optional_datasets": [],
        "blocked_datasets": [],
        "reason": "正式日版认证通过",
    }

    gate = PreScoreGate(mock_engine, adc_client=mock_adc_client)
    gate_res = gate.check_asdc_release_gate(date(2026, 11, 6))

    assert gate_res["passed"] is True
    assert gate_res["eligible"] is True
    assert gate_res["state"] == "READY"
    assert gate_res["release_state"] == "READY"

    # 端到端串联验证：直接传给 ExecutionContext.create_production 必须成功
    ctx = ExecutionContext.create_production(20261106, gate_res)
    assert ctx.mode == "PRODUCTION"
    assert ctx.research_only is False
    assert ctx.release_id == "rel_20261106_v1"
    assert ctx.data_version_id == "dv_20261106_01"
    assert ctx.pit_snapshot_id == "pit_20261106_01"
    assert ctx.certification_status == "CERTIFIED_DAILY"

    # 验证准入校验放行正式写入
    ctx.validate_for_production_write("score_rank_daily")


def test_production_context_blocks_placeholder_credentials():
    """验证 ExecutionContext.create_production 严禁使用占位符或缺失凭据创建正式上下文。"""
    placeholder_gate = {
        "eligible": True,
        "state": "READY",
        "release_id": "PLACEHOLDER",
        "data_version_id": "None",
        "pit_snapshot_id": "",
    }
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        ExecutionContext.create_production(20261106, placeholder_gate)
    assert "发布凭据不完整或为占位符" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P0-02: Critical 数据集缺失与指针一致性校验
# ---------------------------------------------------------------------------

def test_check_release_gate_blocks_empty_datasets():
    """验证当 release 存在但绑定的数据集列表为空时，强制 Fail-Closed 阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_empty",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": None,
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=[]):

        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "ALL_DATASETS_MISSING" in res["blocked_datasets"]
        assert "未绑定任何有效数据集" in res["reason"]


def test_check_release_gate_blocks_missing_critical_datasets():
    """验证当缺少必需的 Critical 数据集（如缺少 dim_stock）时，强制 Fail-Closed 阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # 仅提供部分数据集，缺少 dim_stock
    partial_datasets = [
        {"dataset_name": "dwd_daily", "criticality": "CRITICAL", "state": "PASS"},
        {"dataset_name": "dwd_stock_daily_standard", "criticality": "CRITICAL", "state": "PASS"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_partial",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": None,
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=partial_datasets):

        res = release_client.check_release_gate(
            20261106,
            required_critical_datasets=["dwd_daily", "dwd_stock_daily_standard", "dim_stock"],
        )
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "dim_stock(MISSING)" in res["reason"]


def test_check_release_gate_blocks_pointer_mismatch():
    """验证当活动发布指针 meta_data_release_pointer 与当前 release_id 冲突时，强制阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    pointer_mismatch_release = {
        "release_id": "rel_20261106_old",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"release_id": "rel_20261106_latest"},
        "pointer_matched": False,
    }

    with patch.object(release_client, "get_certified_release", return_value=pointer_mismatch_release):
        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "POINTER_MISMATCH" in res["blocked_datasets"]
        assert "发布指针冲突" in res["reason"]


# ---------------------------------------------------------------------------
# P0-03: 消除空执行上下文写入绕过
# ---------------------------------------------------------------------------

def test_save_scores_blocks_none_execution_context():
    """验证调用 save_scores_to_db 时传入 execution_context=None 强制抛出 ProductionAdmissionBlockedError。"""
    df_fake = pd.DataFrame([{"stock_code": "000001.SZ", "score": 90.0}])
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        save_scores_to_db(
            df_fake,
            asof_date=pd.to_datetime("2026-11-06"),
            connection=MagicMock(),
            execution_context=None,
        )
    assert "禁止传入 None 绕过数据准入校验" in str(exc_info.value)


def test_register_snapshot_blocks_none_execution_context():
    """验证调用 _register_snapshot_and_layers 时传入 execution_context=None 强制抛出 ProductionAdmissionBlockedError。"""
    df_fake = pd.DataFrame([{"stock_code": "000001.SZ", "score": 90.0, "symbol": "000001"}])
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        _register_snapshot_and_layers(
            df_fake,
            asof_date=pd.to_datetime("2026-11-06"),
            snapshot_id="rs_test_01",
            bs_source_batch="ml_detect_v3",
            execution_context=None,
        )
    assert "禁止传入 None 绕过数据准入校验" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P0-04: 关闭 Live Tracker 与 Backtest 的生产静默回退
# ---------------------------------------------------------------------------

def test_live_tracker_fail_closed_in_production():
    """验证在正式/可信影子模式下，Live Tracker ADCClient 失败时绝不静默回退 SQL，严格抛出 RuntimeError。"""
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 数据库不可用")):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
            )
        assert "严格禁止静默回退未认证原生 SQL" in str(exc_info.value)


def test_live_tracker_allows_fallback_in_explicit_research():
    """验证在显式指定 allow_research_fallback=True 的研究模式下允许回退并发出警告。"""
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 数据库不可用")), \
         patch("sina.live_tracker.live_tracker_db.get_db_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_date": 20261106}
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        # 模拟回退路径执行
        prices = get_latest_prices_from_kline(
            symbols=["000001"],
            trade_date=date(2026, 11, 6),
            allow_research_fallback=True,
        )
        assert isinstance(prices, dict)


def test_backtest_feed_fail_closed_in_production():
    """验证在可信回测生产模式下，ADCClient 失败时绝不静默回退 SQL，严格抛出 RuntimeError。"""
    feed = TushareDailyFeed(mode="production", allow_research_fallback=False)
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 数据库不可用")):
        with pytest.raises(RuntimeError) as exc_info:
            list(feed.iter_bars(
                start="2026-11-01",
                end="2026-11-06",
                universe=["000001.SZ"],
                fields=None,
                freq="1d",
            ))
        assert "严格禁止静默回退未认证数据库表" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P1-01: 端到端版本可见性与未来函数拦截
# ---------------------------------------------------------------------------

def test_future_leakage_visible_date_max_blocked():
    """验证请求的数据日期若超出该 Release 的 visible_date_max，直接抛出未来函数拦截异常。"""
    mock_conn = MagicMock()
    client = ADCClient()
    client._conn = mock_conn

    # 模拟该 Release 对日线数据的可见上限为 20261106
    with patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
        {"dataset_name": "dwd_stock_daily_standard", "visible_date_max": 20261106}
    ]):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261101,
                end_date=20261110,  # 发生未来穿越
                release_id="rel_20261106_v1",
            )
        assert "未来函数拦截" in str(exc_info.value)
        assert "超过了该 Release [rel_20261106_v1] 的最大可见日期 20261106" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P1-02: 服务端只读真实性与超级用户阻断测试
# ---------------------------------------------------------------------------

def test_server_side_readonly_probe_blocks_super_user():
    """验证当 SHOW GRANTS 显示超级用户拥有全局写权限时，verify_server_side_readonly 强制抛出异常阻断。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT ALL PRIVILEGES ON *.* TO 'fake_ro'@'%'"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_info:
            client.verify_server_side_readonly()
        assert "当前连接拥有全局超级管理员权限 (ALL PRIVILEGES)" in str(exc_info.value)
