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
    mock_adc_client.verify_server_side_readonly.return_value = {"readonly_verified": True}
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
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)
    with patch("cross_project.adc_client.ADCClient", side_effect=RuntimeError("ADC 数据库不可用")):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
                execution_context=ctx,
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
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    # 上下文 trade_date 设为 20261110
    ctx = ExecutionContext.create_production(trade_date=20261110, release_gate_result=gate_res)

    # 模拟该 Release 对日线数据的可见上限为 20261106
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1",
             "release_type": "CERTIFIED_DAILY",
             "state": "READY",
             "data_version_id": "dv_v1",
             "pit_snapshot_id": "pit_v1",
             "trade_date": 20261110,
         }), patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "PASS", "visible_date_max": 20261106}
         ]), patch.object(client, "_table_ready", return_value=True):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261101,
                end_date=20261110,  # 超过 visible_date_max 20261106 发生未来穿越
                release_id="rel_20261106_v1",
                execution_context=ctx,
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
    mock_cursor.execute.side_effect = [None, denied_err, denied_err, denied_err]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with patch.object(client, "_get_conn", return_value=mock_conn):
        res = client.verify_server_side_readonly()
        assert res["readonly_verified"] is True
        assert "SERVER_SIDE_ACCESS_DENIED" in res["verified_method"]


# ---------------------------------------------------------------------------
# Phase 2E: P0-02 Live Tracker 严格阻断与杜绝原生 SQL 绕过
# ---------------------------------------------------------------------------

def test_live_tracker_fail_closed_on_empty_prices():
    """验证在生产模式下，当统一接口返回空 DataFrame 时，严格 Fail-Closed 抛出 RuntimeError。"""
    mock_adc = MagicMock()
    mock_adc.data.get_latest_data_date.return_value = 20261106
    from cross_project.execution_context import DataProvenanceProof
    proof = DataProvenanceProof(
        release_id="rel_20261106_v1",
        dataset_name="dwd_equity_daily_bar_v2",
        snapshot_id="snap_20261106_v1",
        pit_snapshot_id="pit_v1",
        visible_date_max=20261106,
        gate_evidence_sha256="evidence_sha",
        row_count=0,
        data_hash="empty_hash",
        verified_at="2026-11-06T00:00:00",
        certified=True,
        status="SNAPSHOT_BOUND_CERTIFIED",
        source_table="dwd_equity_daily_bar_v2",
        snapshot_bound=True,
    )
    mock_empty_df = pd.DataFrame()
    mock_empty_df.attrs["certified"] = True
    mock_empty_df.attrs["provenance_proof"] = proof
    mock_adc.data.get_daily_bars_batch.return_value = mock_empty_df

    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
                execution_context=ctx,
            )
        assert "Live Tracker 生产行情获取返回空数据集" in str(exc_info.value)
        assert "严格禁止使用未认证备用数据" in str(exc_info.value)


def test_live_tracker_fail_closed_on_missing_symbol_price():
    """验证在生产模式下，当统一接口返回数据缺少请求的标的或价格为 0 时，严格 Fail-Closed。"""
    mock_adc = MagicMock()
    mock_adc.data.get_latest_data_date.return_value = 20261106
    # 返回了 000002，但请求的是 000001，附带合法认证证明
    from cross_project.execution_context import DataProvenanceProof
    proof = DataProvenanceProof(
        release_id="rel_20261106_v1",
        dataset_name="dwd_equity_daily_bar_v2",
        snapshot_id="snap_20261106_v1",
        pit_snapshot_id="pit_v1",
        visible_date_max=20261106,
        gate_evidence_sha256="evidence_sha",
        row_count=1,
        data_hash="abc",
        verified_at="2026-11-06T00:00:00",
        certified=True,
        status="SNAPSHOT_BOUND_CERTIFIED",
        source_table="dwd_equity_daily_bar_v2",
        snapshot_bound=True,
    )
    mock_df = pd.DataFrame([{"symbol": "000002", "close": 15.0}])
    mock_df.attrs["certified"] = True
    mock_df.attrs["provenance_proof"] = proof
    mock_adc.data.get_daily_bars_batch.return_value = mock_df

    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                allow_research_fallback=False,
                execution_context=ctx,
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
    """验证在显式隔离研究回退场景下，读取未分区旧表生成 RESEARCH_UNPARTITIONED_FALLBACK 证明且 certified 严格为 False。"""
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
    res_ctx = ExecutionContext.create_research(20261106, reason="FALLBACK_STUDY")

    with patch.object(client, "_query", return_value=mock_rows), \
         patch.object(client, "_table_ready", return_value=False), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_20261106_v1", "visible_date_max": 20261106, "gate_evidence_sha256": "sha_proof_123"}
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            pit_snapshot_id="pit_20261106_v1",
            execution_context=res_ctx,
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
        assert proof.certified is False
        assert proof.status == "RESEARCH_UNPARTITIONED_FALLBACK"
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
        "amount_cny": 510000000.0,
        "available_at": "2026-11-06T18:00:00",
        "adjustment_anchor_date": 20261106,
    }]
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_20261106_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_20261106_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_formal_v2_001", "state": "PASS", "visible_date_max": 20261106, "gate_evidence_sha256": "sha_proof_456"}
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            pit_snapshot_id="pit_20261106_v1",
            execution_context=ctx,
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
    prod_ctx.research_only = False
    prod_ctx.release_id = None
    prod_ctx.pit_snapshot_id = None

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=prod_ctx,
        )
    assert "安全红线阻断：生产执行模式下 ExecutionContext 必须预先绑定有效经过认证的 release_id" in str(exc_info.value)


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
        "amount_cny": 929098438.2,
        "available_at": "2026-11-06T18:00:00",
        "adjustment_anchor_date": 20261106,
    }]
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v2", "state": "PASS", "visible_date_max": 20261106}
         ]):
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            execution_context=ctx,
        )
        called_sql, _ = mock_query.call_args[0]
        assert "(amount_cny / 1000.0) AS amount" in called_sql
        assert "volume_hands AS volume" in called_sql


def test_bj_symbols_fail_closed_in_production():
    """验证当标的包含北交所 (.BJ) 标的时，生产执行模式严格 Fail-Closed 阻断。"""
    client = ADCClient()
    prod_ctx = MagicMock()
    prod_ctx.mode = "PRODUCTION"
    prod_ctx.research_only = False
    prod_ctx.release_id = "rel_20261106_v1"
    prod_ctx.pit_snapshot_id = "pit_20261106_v1"
    prod_ctx.data_version_id = "dv_v1"
    prod_ctx.trade_date = 20261106

    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_20261106_v1", "trade_date": 20261106
         }):
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


# ---------------------------------------------------------------------------
# Phase 2E 最终收敛六类核心负向准入测试
# ---------------------------------------------------------------------------

def test_live_tracker_fail_closed_when_missing_context_even_if_adc_returns_prices():
    """负向测试 1: 缺少 ExecutionContext 时，即使 ADC 返回正常价格，Live Tracker 也必须 Fail-Closed。"""
    mock_adc = MagicMock()
    mock_adc.data.get_latest_data_date.return_value = 20261106
    # 模拟 ADCClient 返回了完全合法的价格数据
    mock_df = pd.DataFrame([{"symbol": "000001", "close": 10.5}])
    mock_adc.data.get_daily_bars_batch.return_value = mock_df

    with patch("cross_project.adc_client.ADCClient", return_value=mock_adc):
        with pytest.raises(RuntimeError) as exc_info:
            get_latest_prices_from_kline(
                symbols=["000001"],
                execution_context=None,  # 显式缺失上下文
                allow_research_fallback=False,
            )
        assert "安全红线阻断：Live Tracker 生产/影子模式下获取行情必须提供有效认证的 ExecutionContext" in str(exc_info.value)


def test_adc_client_fail_closed_when_physical_snapshot_table_missing():
    """负向测试 2: 物理快照表 dwd_equity_daily_bar_v2 不存在时，生产模式严格阻断，严禁降级旧表并标记 certified。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch.object(client, "_table_ready", return_value=False), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "PASS", "visible_date_max": 20261106}
         ]):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                release_id="rel_20261106_v1",
                execution_context=ctx,
            )
        assert "物理快照表缺失阻断" in str(exc_info.value)
        assert "生产模式严禁降级未分区旧表" in str(exc_info.value)


def test_adc_client_uses_raw_bar_snapshot_not_adj_factor_snapshot():
    """负向测试 3: 复权因子与日线 Snapshot 不同时，日线行情必须严格取 equity_daily_bar_raw 快照，严禁混用复权因子快照。"""
    client = ADCClient()
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount_cny": 510000000.0,
        "available_at": "2026-11-06T18:00:00",
        "adjustment_anchor_date": 20261106,
    }]
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             # 复权因子的 snapshot 为 snap_adj_factor_999
             {"dataset_name": "equity_adjustment_factor", "snapshot_id": "snap_adj_factor_999", "state": "PASS", "visible_date_max": 20261106},
             # 日线行情的 snapshot 为 snap_raw_bars_111
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_raw_bars_111", "state": "PASS", "visible_date_max": 20261106},
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            adj_type="qfq",
            release_id="rel_20261106_v1",
            execution_context=ctx,
        )

        assert not df.empty
        proof = df.attrs["provenance_proof"]
        # 必须绑定日线快照，绝不能混用复权因子快照
        assert proof.snapshot_id == "snap_raw_bars_111"
        assert proof.snapshot_id != "snap_adj_factor_999"

        called_sql, called_params = mock_query.call_args[0]
        assert "snapshot_id = %s" in called_sql
        assert called_params[0] == "snap_raw_bars_111"
        assert called_params[0] != "snap_adj_factor_999"


def test_adc_client_end_date_none_constrained_by_context_and_visible_date_max():
    """负向测试 4: end_date=None 时必须由认证上下文确定明确上界，防无限制查询与未来越界。"""
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

    # Case A: 上下文 trade_date 正常约束 end_clause
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with patch.object(client, "_query", return_value=mock_rows) as mock_query, \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "PASS", "visible_date_max": 20261106}
         ]):
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261101,
            end_date=None,  # 未显式传 end_date
            release_id="rel_20261106_v1",
            execution_context=ctx,
        )
        called_sql, called_params = mock_query.call_args[0]
        # SQL 查询必须强制绑定 trade_date <= %s，防止无界查询
        assert "trade_date <= %s" in called_sql
        assert 20261106 in called_params

    # Case B: 若上下文 trade_date 超过 visible_date_max，严格触发未来函数拦截
    future_ctx = ExecutionContext.create_production(trade_date=20261109, release_gate_result=gate_res)
    with patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261109
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "PASS", "visible_date_max": 20261106}
         ]):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261101,
                end_date=None,  # 自动推导为 20261109 越界
                release_id="rel_20261106_v1",
                execution_context=future_ctx,
            )
        assert "未来函数拦截" in str(exc_info.value)
        assert "20261109 超过了该 Release [rel_20261106_v1] 的最大可见日期 20261106" in str(exc_info.value)


def test_adc_client_fail_closed_on_release_id_mismatch():
    """负向测试 5: 请求传入的 Release ID 与 ExecutionContext 不一致时严格阻断。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_diff_unmatched",  # 与 ctx 不一致
            execution_context=ctx,
        )
    assert "与 ExecutionContext 绑定的发布上下文 [rel_20261106_v1] 不一致" in str(exc_info.value)


def test_prescore_gate_blocks_when_readonly_verified_is_false():
    """负向测试 6: 当 verify_server_side_readonly 未明确返回 readonly_verified=True 时，PreScoreGate 必须阻断。"""
    mock_engine = MagicMock()
    mock_adc_client = MagicMock()
    # 模拟只读模式未启用或未通过校验
    mock_adc_client.verify_server_side_readonly.return_value = {
        "readonly_verified": False,
        "reason": "READONLY_MODE_DISABLED",
    }
    mock_adc_client.release.check_release_gate.return_value = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
    }

    gate = PreScoreGate(mock_engine, adc_client=mock_adc_client)
    res = gate.check_asdc_release_gate(date(2026, 11, 6))

    assert res["eligible"] is False
    assert res["passed"] is False
    assert res["state"] == "READONLY_UNVERIFIED_BLOCKED"
    assert "服务端只读权限未通过验证" in res["detail"]


# ---------------------------------------------------------------------------
# Phase 2E 第七轮最终安全闭环核心反例测试
# ---------------------------------------------------------------------------

def test_get_daily_bars_batch_fail_closed_on_unverified_release_record():
    """第七轮测试 1: 生产读取必须核验权威 Release 状态与日线产品质量，未认证一律阻断。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    # Case A: 发布记录在 meta_data_release 中未找到
    with patch.object(ADCReleaseClient, "get_release_by_id", return_value=None):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "未在 meta_data_release 中检索到" in str(exc_info.value)

    # Case B: 发布记录处于非正式状态 (BLOCKED / SUPERSEDED)
    with patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "get_active_release_pointer", return_value={"release_id": "rel_20261106_v1"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "BLOCKED", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "BLOCKED" in str(exc_info.value) or "未通过白名单校验" in str(exc_info.value)

    # Case C: equity_daily_bar_raw 产品质量状态为 FAIL
    with patch.object(client, "_table_ready", return_value=True), \
         patch.object(ADCReleaseClient, "get_active_release_pointer", return_value={"release_id": "rel_20261106_v1"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "FAIL", "visible_date_max": 20261106}
         ]):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "FAIL" in str(exc_info.value) or "未通过" in str(exc_info.value)


def test_get_daily_bars_batch_strict_pit_trade_date_bound():
    """第七轮测试 2: 显式 end_date 严禁越过 ExecutionContext 的交易日，确保历史回测 PIT 正确性。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_v1",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    # 执行上下文交易日为 20261106
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    # 数据集 visible_date_max 即使为 20261115，调用方显式传入 20261110 依然属于相对于上下文的未来数据，必须拦截！
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_v1", "state": "PASS", "visible_date_max": 20261115}
         ]):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261101,
                end_date=20261110,  # 显式 end_date 跨过上下文交易日 20261106
                execution_context=ctx,
            )
        assert "未来函数拦截" in str(exc_info.value)
        assert "超过了 ExecutionContext 绑定的交易日 20261106" in str(exc_info.value)


def test_get_daily_bars_batch_caller_cannot_substitute_missing_release_id():
    """第七轮测试 3: ExecutionContext 缺少 Release ID 时，严禁调用方代填身份；PIT 快照冲突阻断。"""
    client = ADCClient()
    # 模拟 ExecutionContext 缺少 release_id
    mock_ctx = MagicMock()
    mock_ctx.mode = "PRODUCTION"
    mock_ctx.research_only = False
    mock_ctx.release_id = None
    mock_ctx.data_version_id = "dv_v1"
    mock_ctx.pit_snapshot_id = "pit_ctx_111"
    mock_ctx.trade_date = 20261106

    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_caller_substituted",  # 调用方试图代填
            execution_context=mock_ctx,
        )
    assert "严禁调用方代填身份" in str(exc_info.value)

    # PIT 快照冲突检查
    mock_ctx.release_id = "rel_20261106_v1"
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info2:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            release_id="rel_20261106_v1",
            pit_snapshot_id="pit_caller_mismatched",  # 与 ctx pit_snapshot_id 不一致
            execution_context=mock_ctx,
        )
    assert "与 ExecutionContext 绑定的 PIT 快照 [pit_ctx_111] 不一致" in str(exc_info2.value)


def test_get_daily_bars_batch_strict_whitelist_blocks_missing_or_invalid_mode():
    """第七轮测试 4: 准入严格白名单，无上下文或未显式声明隔离研究严禁读取旧表。"""
    client = ADCClient()

    # Case A: 完全未传上下文
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=None,
        )
    assert "必须提供有效 ExecutionContext 准入上下文，禁止无上下文读取行情数据" in str(exc_info.value)

    # Case B: 上下文 mode 为未知模式
    bad_ctx = MagicMock()
    bad_ctx.mode = "CUSTOM_MODE"
    bad_ctx.research_only = False
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info2:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=bad_ctx,
        )
    assert "不在准入白名单" in str(exc_info2.value)

    # Case C: mode 为 RESEARCH 但未声明 research_only=True
    unisolated_res_ctx = MagicMock()
    unisolated_res_ctx.mode = "RESEARCH"
    unisolated_res_ctx.research_only = False
    with pytest.raises(ProductionAdmissionBlockedError) as exc_info3:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=unisolated_res_ctx,
        )
    assert "不在准入白名单" in str(exc_info3.value)


def test_check_release_gate_blocks_critical_dataset_completely_omitting_snapshot_field():
    """第七轮测试 5: Critical 数据集完全缺少 snapshot_id 字段键时，必须判定为 SNAPSHOT_MISSING 阻断。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # trade_calendar 完全没有 snapshot_id 键
    datasets = [
        {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS"},  # 缺失 snapshot_id 字段
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


def test_verify_server_side_readonly_blocks_complex_and_table_level_grants():
    """第七轮测试 6: 覆盖多权限组合及表级授权，确保 SHOW GRANTS 能完整识别各种复杂授权语句。"""
    client = ADCClient(user="fake_ro", readonly_only=True)
    mock_conn = MagicMock()

    # Case A: 多权限组合 GRANT SELECT, UPDATE, DELETE ON *.*
    mock_cursor_a = MagicMock()
    mock_cursor_a.fetchall.return_value = [{"Grants": "GRANT SELECT, UPDATE, DELETE ON *.* TO 'fake_ro'@'%'"}]
    mock_conn.cursor.return_value = mock_cursor_a
    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_a:
            client.verify_server_side_readonly()
        assert "被授予了写操作权限" in str(exc_a.value)

    # Case B: 库级多权限 GRANT SELECT, UPDATE ON tushare_stock.*
    mock_cursor_b = MagicMock()
    mock_cursor_b.fetchall.return_value = [{"Grants": "GRANT SELECT, UPDATE ON `tushare_stock`.* TO 'fake_ro'@'%'"}]
    mock_conn.cursor.return_value = mock_cursor_b
    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_b:
            client.verify_server_side_readonly()
        assert "被授予了写操作权限" in str(exc_b.value)

    # Case C: 表级/列级授权 GRANT UPDATE (close) ON tushare_stock.dwd_stock_daily_standard
    mock_cursor_c = MagicMock()
    mock_cursor_c.fetchall.return_value = [{"Grants": "GRANT UPDATE (close) ON `tushare_stock`.`dwd_stock_daily_standard` TO 'fake_ro'@'%'"}]
    mock_conn.cursor.return_value = mock_cursor_c
    with patch.object(client, "_get_conn", return_value=mock_conn):
        with pytest.raises(ADCReadOnlyViolationError) as exc_c:
            client.verify_server_side_readonly()
        assert "被授予了写操作权限" in str(exc_c.value)


# ---------------------------------------------------------------------------
# Phase 2E 第八轮专项闭环与反例测试
# ---------------------------------------------------------------------------

def test_phase2e_r8_degraded_blocked_without_allow_degraded():
    """第八轮测试 1: DEGRADED 发布在未获得显式 allow_degraded=True 授权时，底层读取坚决阻断。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "DEGRADED",
        "release_id": "rel_20261106_deg",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    # 未传 allow_degraded，默认 False
    ctx_disallowed = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_deg",
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="DEGRADED_PERMITTED",
        research_only=False,
        allow_degraded=False,
    )

    with patch.object(ADCReleaseClient, "check_release_gate", return_value={
        "eligible": False, "state": "DEGRADED", "reason": "数据处于降级状态 (DEGRADED)，需显式配置 allow_degraded=True 消费"
    }):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx_disallowed,
            )
        assert "权威发布门禁阻断" in str(exc_info.value)
        assert "DEGRADED" in str(exc_info.value)

    # 显式授权 allow_degraded=True 且门禁通过时放行
    ctx_allowed = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_deg",
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="DEGRADED_PERMITTED",
        research_only=False,
        allow_degraded=True,
    )
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount_cny": 510000000.0,
        "available_at": "2026-11-06T18:00:00",
        "adjustment_anchor_date": 20261106,
    }]
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "DEGRADED"}), \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(client, "_query", return_value=mock_rows), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_deg", "release_type": "CERTIFIED_DAILY", "state": "DEGRADED", "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "snap_deg_01", "state": "PASS", "visible_date_max": 20261106}
         ]):
        df = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=ctx_allowed,
        )
        assert not df.empty


def test_phase2e_r8_release_type_must_be_certified_daily():
    """第八轮测试 2: 严格验证 release_type='CERTIFIED_DAILY'，非正式类型一律阻断。"""
    client = ADCClient()
    gate_res = {
        "eligible": True,
        "state": "READY",
        "release_id": "rel_20261106_cand",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
    }
    ctx = ExecutionContext.create_production(trade_date=20261106, release_gate_result=gate_res)

    # 模拟 release_type 为 CANDIDATE
    with patch.object(ADCReleaseClient, "get_release_by_id", return_value=None):  # require_certified_daily 过滤掉
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "未在 meta_data_release 中检索到类型为 CERTIFIED_DAILY 的有效发布记录" in str(exc_info.value)


def test_phase2e_r8_active_pointer_enforcement_and_historical_archive():
    """第八轮测试 3: 生产消费验证活动指针匹配；历史回放验证历史存档准入并标记 CERTIFIED_HISTORICAL_ARCHIVE。"""
    mock_client = MagicMock()
    mock_client._table_ready.return_value = True
    release_client = ADCReleaseClient(mock_client)

    # 场景 A: 生产消费时活动指针不匹配
    with patch.object(release_client, "get_release_by_id", return_value={
        "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "trade_date": 20261106, "state": "READY",
        "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1",
    }), patch.object(release_client, "get_active_release_pointer", return_value={
        "pointer_name": "CERTIFIED_DAILY", "release_id": "rel_other_active"  # 指向其他版本
    }):
        res = release_client.check_release_gate(trade_date=20261106, release_id="rel_20261106_v1", is_historical=False)
        assert res["eligible"] is False
        assert "POINTER_MISMATCH" in res["blocked_datasets"]

    # 场景 B: 历史回放消费（is_historical=True）
    datasets = [
        {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s1"},
        {"dataset_name": "security_lifecycle_daily", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s2"},
        {"dataset_name": "equity_daily_bar_raw", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s3"},
        {"dataset_name": "equity_adjustment_factor", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s4"},
        {"dataset_name": "corporate_action_event", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s5"},
        {"dataset_name": "daily_basic", "criticality": "CRITICAL", "state": "PASS", "snapshot_id": "s6"},
    ]
    with patch.object(release_client, "get_release_by_id", return_value={
        "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "trade_date": 20261106, "state": "READY",
        "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1",
    }), patch.object(release_client, "get_active_release_pointer", return_value={
        "pointer_name": "CERTIFIED_DAILY", "release_id": "rel_future_latest"
    }), patch.object(release_client, "get_release_datasets", return_value=datasets):
        res_hist = release_client.check_release_gate(trade_date=20261106, release_id="rel_20261106_v1", is_historical=True)
        assert res_hist["eligible"] is True
        assert res_hist["state"] == "READY"


def test_phase2e_r8_strict_identity_equality_and_placeholder_rejection():
    """第八轮测试 4: 版本与 PIT 身份占位符或不一致逐项 100% 阻断。"""
    client = ADCClient()

    # Case A: 上下文 data_version_id 为占位符
    bad_ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_v1",
        data_version_id="UNVERIFIED",  # 占位符
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
    )
    with pytest.raises(ProductionAdmissionBlockedError) as exc_a:
        client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=20261106,
            end_date=20261106,
            execution_context=bad_ctx,
        )
    assert "必须预设非空且非占位符的 [data_version_id]" in str(exc_a.value)

    # Case B: 上下文与数据库 data_version_id 不一致
    good_ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_v1",
        data_version_id="dv_ctx_version",
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
    )
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY",
             "data_version_id": "dv_db_different", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_b:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=good_ctx,
            )
        assert "data_version_id [dv_db_different] 与 ExecutionContext [dv_ctx_version] 不一致" in str(exc_b.value)


def test_phase2e_r8_raw_bar_identity_strictly_equity_daily_bar_raw():
    """第八轮测试 5: 日线行情产品必须严格且唯一匹配 equity_daily_bar_raw，严禁混入物理表名。"""
    client = ADCClient()
    ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_v1",
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
    )
    # 模拟发布清单中只声明了物理表名 dwd_equity_daily_bar_v2，未声明正式产品 equity_daily_bar_raw
    datasets = [
        {"dataset_name": "dwd_equity_daily_bar_v2", "snapshot_id": "s1", "state": "PASS", "visible_date_max": 20261106}
    ]
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY",
             "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=datasets):
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "未找到标准日线行情产品 (equity_daily_bar_raw)" in str(exc_info.value)


def test_phase2e_r8_pit_available_at_future_leakage_blocked():
    """第八轮测试 6: 数据物理可用时间 available_at 晚于决策时点时，严格触发未来函数拦截。"""
    client = ADCClient()
    ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_v1",
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
        decision_time="2026-11-06T15:00:00",  # 盘中决策点 15:00:00
    )
    # 模拟数据直到盘后 18:30 才物理可用
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount_cny": 510000000.0,
        "available_at": "2026-11-06T18:30:00",  # 晚于决策时点 15:00:00
        "adjustment_anchor_date": 20261106,
    }]
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(client, "_query", return_value=mock_rows), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY",
             "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "s1", "state": "PASS", "visible_date_max": 20261106}
         ]):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                execution_context=ctx,
            )
        assert "未来函数拦截" in str(exc_info.value)
        assert "available_at" in str(exc_info.value)
        assert "晚于决策时点" in str(exc_info.value)


def test_phase2e_r8_pit_adj_anchor_date_future_leakage_blocked():
    """第八轮测试 7: 前复权调整锚点跨越决策交易日时，严格触发未来函数拦截。"""
    client = ADCClient()
    ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=20261106,
        release_id="rel_20261106_v1",
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
    )
    # 模拟数据前复权锚点定在了未来日期 20261110
    mock_rows = [{
        "ts_code": "000001.SZ",
        "trade_date": 20261106,
        "open": 10.0,
        "high": 10.5,
        "low": 9.9,
        "close": 10.2,
        "volume": 50000.0,
        "amount_cny": 510000000.0,
        "available_at": "2026-11-06T18:00:00",
        "adjustment_anchor_date": 20261110,  # 跨过决策交易日 20261106
    }]
    with patch.object(ADCReleaseClient, "check_release_gate", return_value={"eligible": True, "state": "READY"}), \
         patch.object(client, "_table_ready", return_value=True), \
         patch.object(client, "_query", return_value=mock_rows), \
         patch.object(ADCReleaseClient, "get_release_by_id", return_value={
             "release_id": "rel_20261106_v1", "release_type": "CERTIFIED_DAILY", "state": "READY",
             "data_version_id": "dv_v1", "pit_snapshot_id": "pit_v1", "trade_date": 20261106
         }), \
         patch.object(ADCReleaseClient, "get_release_datasets", return_value=[
             {"dataset_name": "equity_daily_bar_raw", "snapshot_id": "s1", "state": "PASS", "visible_date_max": 20261106}
         ]):
        with pytest.raises(ValueError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=20261106,
                end_date=20261106,
                adj_type="qfq",
                execution_context=ctx,
            )
        assert "未来函数拦截" in str(exc_info.value)
        assert "前复权调整锚点" in str(exc_info.value)
        assert "超过了决策交易日" in str(exc_info.value)
