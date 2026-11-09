"""Phase 2B-Hardening 与 Phase 2C/2D 闭环验收与安全测试套件。"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pymysql
import pytest

from backtest.src.backtest_engine.datafeed.tushare_feed import TushareDailyFeed
from cross_project.adc_client import (
    ADCClient,
    ADCConnectionError,
    ADCReadOnlyViolationError,
    ADCReleaseClient,
    CANONICAL_RELEASE_POINTER_NAME,
)
from cross_project.execution_context import (
    DataProvenanceProof,
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
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_empty"},
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
        {"dataset_name": "dwd_daily", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_1"},
        {"dataset_name": "dwd_stock_daily_standard", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_2"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_partial",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_partial"},
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
    """验证在显式指定 allow_research_fallback=True 且处于 RESEARCH 模式下允许回退。"""
    res_ctx = ExecutionContext.create_research(20261106, reason="TEST_FALLBACK")
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 数据库不可用")), \
         patch("sina.live_tracker.live_tracker_db.get_db_connection") as mock_conn:
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"max_date": 20261106}
        mock_cursor.fetchall.return_value = [{"symbol": "000001", "close": 10.5}]
        mock_conn.return_value.cursor.return_value.__enter__.return_value = mock_cursor

        # 模拟回退路径执行
        prices = get_latest_prices_from_kline(
            symbols=["000001"],
            trade_date=date(2026, 11, 6),
            allow_research_fallback=True,
            execution_context=res_ctx,
        )
        assert isinstance(prices, dict)
        assert prices.get("000001") == 10.5


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


# ---------------------------------------------------------------------------
# Phase 2E: P0-01 服务端只读写探测负向与正向全面测试
# ---------------------------------------------------------------------------

def test_server_side_readonly_probe_blocks_write_probe_success():
    """验证当写探测 UPDATE 语句顺利执行未抛异常时，证明账号有写权限，必须抛出 ADCReadOnlyViolationError。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    # SHOW GRANTS 无显式写权限
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT USAGE ON *.* TO 'fake_ro'@'%'"}]
    # 但 UPDATE 顺利执行未报错（未抛异常）
    mock_cursor.execute.side_effect = [None, None]  # SHOW GRANTS, UPDATE
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_info:
            client.verify_server_side_readonly()
        assert "服务端成功执行了写探测语句" in str(exc_info.value)
        assert "只读验证失败" in str(exc_info.value)


def test_server_side_readonly_probe_blocks_syntax_error_1064():
    """验证当写探测返回语法错误 (code=1064) 时，不能误判为只读通过，必须抛出 ADCReadOnlyViolationError。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT SELECT ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    syntax_err = pymysql.MySQLError(1064, "You have an error in your SQL syntax")
    mock_cursor.execute.side_effect = [None, syntax_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_info:
            client.verify_server_side_readonly()
        assert "只读探测 SQL 语法错误 (code=1064)" in str(exc_info.value)


def test_server_side_readonly_probe_blocks_table_not_found_1146():
    """验证当写探测目标表不存在 (code=1146) 时，不能误判为只读通过，必须抛出 ADCReadOnlyViolationError。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT SELECT ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    table_err = pymysql.MySQLError(1146, "Table 'tushare_stock.meta_data_release' doesn't exist")
    mock_cursor.execute.side_effect = [None, table_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_info:
            client.verify_server_side_readonly()
        assert "只读探测目标表不存在 (code=1146)" in str(exc_info.value)


def test_server_side_readonly_probe_blocks_connection_lost_2006():
    """验证当写探测遭遇连接中断 (code=2006) 时，必须抛出 ADCConnectionError。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT SELECT ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    conn_err = pymysql.MySQLError(2006, "MySQL server has gone away")
    mock_cursor.execute.side_effect = [None, conn_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCConnectionError) as exc_info:
            client.verify_server_side_readonly()
        assert "只读验证期间数据库连接中断" in str(exc_info.value)


def test_server_side_readonly_probe_blocks_global_readonly_1290():
    """验证当服务端返回 1290 (全局只读模式) 时，不能误判为账号具有最小只读权限，必须抛出 ADCReadOnlyViolationError。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT SELECT ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    ro_server_err = pymysql.MySQLError(1290, "The MySQL server is running with the --read-only option so it cannot execute this statement")
    mock_cursor.execute.side_effect = [None, ro_server_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_info:
            client.verify_server_side_readonly()
        assert "数据库实例处于全局只读模式 (code=1290" in str(exc_info.value)
        assert "不能单独证明账号 [fake_ro] 具有账号级最小只读权限" in str(exc_info.value)


def test_server_side_readonly_probe_succeeds_on_permitted_code_1142():
    """验证当写探测被 MySQL 权威权限拒绝 (code=1142: ER_TABLEACCESS_DENIED_ERROR) 时，判定只读验证通过。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"Grants": "GRANT SELECT ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    denied_err = pymysql.MySQLError(1142, "UPDATE command denied to user 'fake_ro'@'%' for table 'meta_data_release'")
    mock_cursor.execute.side_effect = [None, denied_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        res = client.verify_server_side_readonly()
        assert res["readonly_verified"] is True
        assert res["rejection_code"] == 1142
        assert res["verified_method"] == "SERVER_SIDE_ACCESS_DENIED_PROBE"


# ---------------------------------------------------------------------------
# Phase 2E: P0-02 Live Tracker 严格阻断与杜绝原生 SQL 绕过
# ---------------------------------------------------------------------------

def test_live_tracker_fail_closed_on_empty_prices():
    """验证在生产模式下，当统一接口返回空 DataFrame 时，严格 Fail-Closed 抛出 RuntimeError。"""
    mock_adc = MagicMock()
    mock_adc.data.get_latest_data_date.return_value = 20261106
    mock_adc.data.get_daily_bars_batch.return_value = pd.DataFrame()

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
            )
        assert "Live Tracker 生产行情获取返回空数据集" in str(exc_info.value)
        assert "严格禁止使用未认证备用数据" in str(exc_info.value)


def test_live_tracker_fail_closed_on_missing_symbol_price():
    """验证在生产模式下，当统一接口返回数据缺少请求的标的或价格为 0 时，严格 Fail-Closed。"""
    mock_adc = MagicMock()
    mock_adc.data.get_latest_data_date.return_value = 20261106
    # 返回了 000002，但请求的是 000001
    mock_df = pd.DataFrame([{"symbol": "000002", "close": 15.0}])
    mock_adc.data.get_daily_bars_batch.return_value = mock_df

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
            )
        assert "Live Tracker 生产行情获取缺失有效价格" in str(exc_info.value)
        assert "000001" in str(exc_info.value)


def test_live_tracker_get_latest_price_raises_without_sql_fallback():
    """验证 get_latest_price 在价格缺失时直接抛出 ValueError，绝不回退原生 SQL。"""
    with patch("sina.live_tracker.live_tracker_db.get_latest_prices_from_kline", return_value={}):
        with pytest.raises(ValueError) as exc_info:
            get_latest_price("000001")
        assert "标的 000001 行情缺失或价格为 0" in str(exc_info.value)
        assert "拒绝返回未认证数据" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 2E: P0-03 Release Pointer 缺失与关键数据集快照缺失阻断
# ---------------------------------------------------------------------------

def test_check_release_gate_blocks_missing_pointer():
    """验证当 Release 存在但 meta_data_release_pointer 缺失时，判定为 POINTER_MISSING 强制阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # 模拟 Release 记录存在，但未关联任何 active_pointer
    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_v1",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": None,
        "pointer_matched": False,
        "pointer_status": "POINTER_MISSING",
    }):
        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "POINTER_MISSING" in res["blocked_datasets"]
        assert "正式发布缺少有效的活动指针" in res["reason"]


def test_check_release_gate_blocks_critical_dataset_snapshot_missing():
    """验证当 Critical 数据集的 snapshot_id 为空/缺失时，判定为 SNAPSHOT_MISSING 强制阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    datasets = [
        {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": ""},  # 空 snapshot_id
        {"dataset_name": "security_lifecycle_daily", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_2"},
        {"dataset_name": "equity_daily_bar_raw", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_3"},
        {"dataset_name": "equity_adjustment_factor", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_4"},
        {"dataset_name": "corporate_action_event", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_5"},
        {"dataset_name": "daily_basic", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "snap_6"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_v1",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_v1"},
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=datasets):
        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "trade_calendar(SNAPSHOT_MISSING)" in res["reason"]


def test_check_release_gate_blocks_non_whitelist_top_level_states():
    """验证顶层状态严格白名单：BUILDING、PENDING、UNKNOWN、SUPERSEDED、空状态一律 Fail-Closed 阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    for invalid_state in ("BUILDING", "PENDING", "UNKNOWN", "SUPERSEDED", "", "PROCESSING"):
        with patch.object(release_client, "get_certified_release", return_value={
            "release_id": "rel_20261106_v1",
            "state": invalid_state,
            "data_version_id": "dv_v1",
            "pit_snapshot_id": "pit_v1",
            "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_v1"},
            "pointer_matched": True,
        }):
            res = release_client.check_release_gate(20261106)
            assert res["eligible"] is False
            assert "未通过白名单校验" in res["reason"]


def test_check_release_gate_enforces_all_six_canonical_critical_products():
    """验证必须包含全部 6 大 Canonical Critical 产品，严禁仅提供旧三表缩减集合。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # 仅提供了旧三表，缺少 security_lifecycle_daily 等
    old_three_datasets = [
        {"dataset_name": "dwd_daily", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s1"},
        {"dataset_name": "dwd_stock_daily_standard", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s2"},
        {"dataset_name": "dim_stock", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s3"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_v1",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_v1"},
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=old_three_datasets):
        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        # 6 大产品中缺失的均必须报错
        assert "trade_calendar(MISSING)" in res["reason"]
        assert "security_lifecycle_daily(MISSING)" in res["reason"]


def test_check_release_gate_allows_ready_positive_integration():
    """验证正向集成路径：READY 状态且全部 6 大 Canonical 产品达标时，正确放行。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    valid_datasets = [
        {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s1"},
        {"dataset_name": "security_lifecycle_daily", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s2"},
        {"dataset_name": "equity_daily_bar_raw", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s3"},
        {"dataset_name": "equity_adjustment_factor", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s4"},
        {"dataset_name": "corporate_action_event", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s5"},
        {"dataset_name": "daily_basic", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s6"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_v1",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_v1"},
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=valid_datasets):
        res = release_client.check_release_gate(20261106)
        assert res["eligible"] is True
        assert res["state"] == "READY"
        assert res["critical_datasets_ok"] is True
        assert "正式日版认证通过" in res["reason"]


def test_get_latest_price_threads_execution_context():
    """验证 get_latest_price 正确透传 ExecutionContext 到底层行情查询。"""
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_20261106_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)
    with patch("sina.live_tracker.live_tracker_db.get_latest_prices_from_kline", return_value={"000001": 12.34}) as mock_kline:
        p = get_latest_price("000001", execution_context=ctx)
        assert p == 12.34
        mock_kline.assert_called_once_with(["000001"], execution_context=ctx)


def test_check_release_gate_cannot_shrink_canonical_critical_products():
    """验证外部参数 required_critical_datasets 只能追加，绝不能缩减 6 大 Canonical 产品底线。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # 尝试通过传参仅要求 trade_calendar，绕过其余 5 项关键产品
    partial_datasets = [
        {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s1"},
    ]

    with patch.object(release_client, "get_certified_release", return_value={
        "release_id": "rel_20261106_v1",
        "state": "READY",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "active_pointer": {"pointer_name": "CERTIFIED_DAILY", "release_id": "rel_20261106_v1"},
        "pointer_matched": True,
    }), patch.object(release_client, "get_release_datasets", return_value=partial_datasets):
        # 外部仅传 trade_calendar，但底线不可缩减，必须失败
        res = release_client.check_release_gate(20261106, required_critical_datasets=["trade_calendar"])
        assert res["eligible"] is False
        assert res["state"] == "BLOCKED"
        assert "security_lifecycle_daily(MISSING)" in res["reason"]
        assert "equity_daily_bar_raw(MISSING)" in res["reason"]


def test_live_tracker_class_threads_execution_context_to_db():
    """验证 LiveTracker 真实类实例将 ExecutionContext 完整透传至 record_buy 与 sync_prices 底层。"""
    from contextlib import contextmanager
    from sina.live_tracker.live_tracker import LiveTracker

    @contextmanager
    def fake_txn():
        yield MagicMock()

    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_20261106_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch("sina.live_tracker.live_tracker.db.get_all_positions", return_value=[]), \
         patch("sina.live_tracker.live_tracker.db.get_latest_snapshot", return_value={"cash": 100000.0, "total_equity": 100000.0, "snapshot_date": date(2026, 11, 5)}), \
         patch("sina.live_tracker.live_tracker.db.get_latest_price", return_value=10.0) as mock_get_price, \
         patch("sina.live_tracker.live_tracker.db.get_stock_name", return_value="平安银行"), \
         patch("sina.live_tracker.live_tracker.db.transaction", side_effect=fake_txn), \
         patch("sina.live_tracker.live_tracker.db.insert_trade"), \
         patch("sina.live_tracker.live_tracker.db.upsert_position"), \
         patch("sina.live_tracker.live_tracker.db.upsert_daily_snapshot"), \
         patch("sina.live_tracker.live_tracker.db.get_latest_prices_from_kline", return_value={"000001": 10.5}) as mock_kline, \
         patch("sina.live_tracker.live_tracker.db.batch_update_prices"):

        tracker = LiveTracker(initial_capital=100000.0, execution_context=ctx)
        assert tracker.execution_context is ctx

        # record_buy 传入 price=0 触发从 db 获取价格，必须携带 ctx
        tracker.record_buy(symbol="000001", price=0, shares=100)
        mock_get_price.assert_called_once_with("000001", execution_context=ctx)

        # sync_prices 必须携带 ctx
        tracker.sync_prices()
        mock_kline.assert_called_once_with(["000001"], None, execution_context=ctx)




# ---------------------------------------------------------------------------
# Phase 2E: P1-01 & P1-02 DataProvenanceProof 绑定与严格执行模式隔离
# ---------------------------------------------------------------------------

def test_backtest_feed_blocks_research_fallback_when_mode_is_production():
    """验证 TushareDailyFeed 在 mode='production' 下，即使传入 allow_research_fallback=True 也绝对禁止回退。"""
    feed = TushareDailyFeed(mode="production", allow_research_fallback=True)
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 不可用")):
        with pytest.raises(RuntimeError) as exc_info:
            list(feed.iter_bars(
                start="2026-11-01",
                end="2026-11-06",
                universe=["000001.SZ"],
                fields=None,
                freq="1d",
            ))
        assert "可信回测行情数据源获取失败" in str(exc_info.value)
        assert "严格禁止静默回退未认证数据库表" in str(exc_info.value)


def test_provenance_proof_bound_to_dataframe():
    """验证 ADCDataClient.get_daily_bars_batch 在扁平表场景生成 RELEASE_DECLARED_TABLE_UNPARTITIONED 证明。"""
    client = ADCClient()
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount": 510000.0,
    }]

    with patch.object(client, "_query", return_value=mock_rows), \
         patch.object(client, "_table_ready", return_value=False), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "dwd_stock_daily_standard", "snapshot_id": "snap_20261106_v1", "visible_date_max": 20261106, "gate_evidence_sha256": "sha_proof_123"}
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            pit_snapshot_id="pit_20261106_v1",
        )

        assert not df.empty
        assert "provenance_proof" in df.attrs
        proof: DataProvenanceProof = df.attrs["provenance_proof"]
        assert isinstance(proof, DataProvenanceProof)
        assert proof.release_id == "rel_20261106_v1"
        assert proof.snapshot_id == "snap_20261106_v1"
        assert proof.pit_snapshot_id == "pit_20261106_v1"
        assert proof.visible_date_max == 20261106
        assert proof.gate_evidence_sha256 == "sha_proof_123"
        assert proof.certified is True
        assert proof.status == "RELEASE_DECLARED_TABLE_UNPARTITIONED"
        assert proof.snapshot_bound is False
        assert proof.source_table == "dwd_stock_daily_standard"
        assert proof.row_count == 1
        assert len(proof.data_hash) == 64


def test_provenance_proof_snapshot_bound_v2_table():
    """验证当存在不可变物理表 dwd_equity_daily_bar_v2 时，SQL 强制绑定 snapshot_id 并标记 SNAPSHOT_BOUND_CERTIFIED。"""
    client = ADCClient()
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount": 510000.0,
    }]

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "dwd_stock_daily_standard", "snapshot_id": "snap_formal_v2_001", "visible_date_max": 20261106, "gate_evidence_sha256": "sha_proof_456"}
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            pit_snapshot_id="pit_20261106_v1",
        )

        assert not df.empty
        proof: DataProvenanceProof = df.attrs["provenance_proof"]
        assert proof.certified is True
        assert proof.snapshot_bound is True
        assert proof.status == "SNAPSHOT_BOUND_CERTIFIED"
        assert proof.source_table == "dwd_equity_daily_bar_v2"
        assert proof.snapshot_id == "snap_formal_v2_001"

        # 核心验证：验证实际发出的 SQL 确实包含 snapshot_id = %s 且第一个参数为 snap_formal_v2_001
        called_sql, called_params = mock_query.call_args[0]
        assert "snapshot_id = %s" in called_sql
        assert called_params[0] == "snap_formal_v2_001"
        assert "dwd_equity_daily_bar_v2" in called_sql


def test_production_admission_blocked_when_reading_bars_without_release():
    """验证在正式生产执行模式下，读取行情数据未提供已认证 release_id 时直接抛出 ProductionAdmissionBlockedError。"""
    client = ADCClient()
    # 模拟一个没有 release_id 的生产执行上下文
    prod_ctx = MagicMock()
    prod_ctx.mode = "PRODUCTION"
    prod_ctx.release_id = None
    prod_ctx.pit_snapshot_id = None

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=prod_ctx,
        )
    assert "安全红线阻断：生产执行模式下读取日线行情必须绑定有效经过认证的 release_id" in str(exc_info.value)


def test_amount_cny_unit_conversion_divided_by_1000():
    """验证物理快照表 dwd_equity_daily_bar_v2 查询时，amount_cny 严格除以 1000.0 换算为千元口径。"""
    client = ADCClient()
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount": 929098.4382,
    }]

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "dwd_stock_daily_standard", "snapshot_id": "snap_v2", "visible_date_max": 20261106}
         ]):
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
        )
        called_sql, _ = mock_query.call_args[0]
        assert "(amount_cny / 1000.0) AS amount" in called_sql
        assert "volume_hands AS volume" in called_sql


def test_bj_symbols_fail_closed_in_production():
    """验证当标的包含北交所 (.BJ) 标的时，生产执行模式严格 Fail-Closed 阻断。"""
    client = ADCClient()
    prod_ctx = MagicMock()
    prod_ctx.mode = "PRODUCTION"
    prod_ctx.release_id = "rel_20261106_v1"
    prod_ctx.pit_snapshot_id = "pit_20261106_v1"

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001.SZ", "830946.BJ"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            execution_context=prod_ctx,
        )
    assert "标的未覆盖阻断" in str(exc_info.value)
    assert "北交所标的" in str(exc_info.value)
