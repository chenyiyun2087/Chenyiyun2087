"""MySQL 8.0 Integration Tests for ASDC Formal Data V2 and Historical Release Chain.

Applies the authoritative ASDC DDL from sql/migrations/20260728_003_formal_data_v2.sql
and sql/migrations/20260922_001_stability_platform_v1.sql on real MySQL:
  1. meta_formal_data_snapshot_v2 with CHECK (status IN ('BUILDING','READY','BLOCKED','FAILED','REJECTED'))
  2. dwd_equity_daily_bar_v2
  3. meta_data_release (with superseded_from_state, supersedes_release_id)
  4. meta_data_release_dataset
  5. meta_data_release_pointer

Requires ORDER_TEST_DB_URL env var. If not set, all tests skip cleanly.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from cross_project.adc_client import ADCClient, ADCReleaseClient
from cross_project.execution_context import ExecutionContext, ProductionAdmissionBlockedError

TEST_DB_URL = os.environ.get("ORDER_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="ORDER_TEST_DB_URL not set (real MySQL needed)")

ASDC_DATABASE = "tushare_stock"
VALID_SHA256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
MANIFEST_SHA = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
DATASET_SHA = "9999999999888888888877777777776666666666555555555544444444443333"


def _admin_engine():
    return create_engine(TEST_DB_URL, future=True)


def _exec_admin(sql: str, params: dict | None = None):
    with _admin_engine().begin() as conn:
        conn.execute(text(sql), params or {})


@pytest.fixture(scope="module", autouse=True)
def setup_asdc_mysql_schema():
    """Create tushare_stock database and authoritative ASDC DDL tables on real MySQL."""
    _exec_admin(f"CREATE DATABASE IF NOT EXISTS {ASDC_DATABASE} DEFAULT CHARACTER SET utf8mb4;")

    ddls = [
        f"""
        CREATE TABLE IF NOT EXISTS {ASDC_DATABASE}.meta_formal_data_snapshot_v2 (
          snapshot_id VARCHAR(64) NOT NULL,
          start_date INT NOT NULL,
          end_date INT NOT NULL,
          market_scope VARCHAR(16) NOT NULL DEFAULT 'SSE_SZSE',
          status VARCHAR(16) NOT NULL DEFAULT 'BUILDING',
          calendar_source VARCHAR(128) NOT NULL DEFAULT 'tushare_stock.dim_trade_cal',
          adjustment_anchor_date INT NULL,
          source_manifest_sha256 CHAR(64) NULL,
          blocking_reasons_json JSON NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at DATETIME NULL,
          PRIMARY KEY (snapshot_id),
          KEY idx_formal_snapshot_status (status, end_date),
          CONSTRAINT chk_formal_snapshot_status CHECK ( status IN ( 'BUILDING', 'READY', 'BLOCKED', 'FAILED', 'REJECTED' ) )
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {ASDC_DATABASE}.dwd_equity_daily_bar_v2 (
          snapshot_id VARCHAR(64) NOT NULL,
          trade_date INT NOT NULL,
          ts_code VARCHAR(16) NOT NULL,
          open DECIMAL(20,6) NOT NULL,
          high DECIMAL(20,6) NOT NULL,
          low DECIMAL(20,6) NOT NULL,
          close DECIMAL(20,6) NOT NULL,
          pre_close DECIMAL(20,6) NOT NULL,
          volume_hands DECIMAL(24,6) NOT NULL,
          volume_shares DECIMAL(26,6) NOT NULL,
          amount_cny DECIMAL(28,6) NOT NULL,
          adj_factor DECIMAL(24,8) NOT NULL,
          adj_open DECIMAL(24,8) NOT NULL,
          adj_high DECIMAL(24,8) NOT NULL,
          adj_low DECIMAL(24,8) NOT NULL,
          adj_close DECIMAL(24,8) NOT NULL,
          adjustment_anchor_date INT NOT NULL,
          circ_mv_cny DECIMAL(28,4) NULL,
          adv20_amount_cny DECIMAL(28,6) NULL,
          available_at DATETIME(6) NOT NULL,
          ingested_at DATETIME(6) NOT NULL,
          source VARCHAR(128) NULL,
          availability_basis VARCHAR(32) NOT NULL DEFAULT 'FORMAL_DAILY_PASS',
          lineage_status VARCHAR(16) NOT NULL DEFAULT 'VERIFIED',
          data_version VARCHAR(128) NOT NULL,
          source_record_id VARCHAR(128) NOT NULL,
          source_record_sha256 CHAR(64) NOT NULL,
          source_payload_sha256 CHAR(64) NOT NULL,
          source_complete TINYINT(1) NOT NULL DEFAULT 1,
          PRIMARY KEY (snapshot_id, trade_date, ts_code),
          KEY idx_equity_bar_v2_date_code (trade_date, ts_code),
          KEY idx_equity_bar_v2_code_date (ts_code, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {ASDC_DATABASE}.meta_data_release (
          release_id VARCHAR(128) NOT NULL,
          release_type VARCHAR(32) NOT NULL DEFAULT 'CERTIFIED_DAILY',
          trade_date INT NOT NULL,
          state VARCHAR(16) NOT NULL,
          data_version_id VARCHAR(128) NULL,
          source_snapshot_id VARCHAR(128) NULL,
          pit_snapshot_id VARCHAR(128) NULL,
          git_commit VARCHAR(64) NULL,
          config_sha256 CHAR(64) NULL,
          scheduler_config_sha256 CHAR(64) NULL,
          migration_set_hash CHAR(64) NULL,
          gate_evidence_sha256 CHAR(64) NULL,
          critical_status_json JSON NOT NULL,
          optional_status_json JSON NOT NULL,
          evidence_json JSON NULL,
          supersedes_release_id VARCHAR(128) NULL,
          superseded_from_state VARCHAR(16) NULL,
          published_at DATETIME NULL,
          created_by VARCHAR(128) NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (release_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {ASDC_DATABASE}.meta_data_release_dataset (
          release_id VARCHAR(128) NOT NULL,
          dataset_name VARCHAR(128) NOT NULL,
          snapshot_id VARCHAR(128) NOT NULL,
          criticality VARCHAR(16) NOT NULL,
          state VARCHAR(16) NOT NULL,
          gate_run_id VARCHAR(128) NULL,
          gate_evidence_sha256 CHAR(64) NULL,
          visible_date_max INT NULL,
          detail_json JSON NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (release_id, dataset_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {ASDC_DATABASE}.meta_data_release_pointer (
          pointer_name VARCHAR(64) NOT NULL,
          release_id VARCHAR(128) NOT NULL,
          pointer_version BIGINT NOT NULL DEFAULT 1,
          fencing_token VARCHAR(128) NULL,
          updated_by VARCHAR(128) NOT NULL,
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (pointer_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
    ]
    for ddl in ddls:
        _exec_admin(ddl)

    # Clean up tables between test runs
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.dwd_equity_daily_bar_v2")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_formal_data_snapshot_v2")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release_dataset")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release_pointer")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release")

    yield

    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.dwd_equity_daily_bar_v2")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_formal_data_snapshot_v2")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release_dataset")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release_pointer")
    _exec_admin(f"TRUNCATE TABLE {ASDC_DATABASE}.meta_data_release")


def _get_adc_client() -> ADCClient:
    """Instantiate real ADCClient connected to MySQL."""
    parsed = urlparse(TEST_DB_URL)
    return ADCClient(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        database=ASDC_DATABASE,
        readonly_only=False,
    )


def test_asdc_schema_check_constraint_rejects_non_conforming_status():
    """集成测试 1: 真实 MySQL CHECK 约束直接拒写未定义状态（如 ONLINE, ACTIVE）。"""
    from sqlalchemy.exc import OperationalError, ProgrammingError

    # 尝试写入未在 CHECK 约束中的 'ONLINE' 或 'ACTIVE' -> MySQL 8.0 抛出 CHECK 约束破坏异常
    with pytest.raises((OperationalError, ProgrammingError, Exception)) as exc_info:
        _exec_admin(f"""
            INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
              (snapshot_id, start_date, end_date, status, source_manifest_sha256)
            VALUES ('snap_test_online', 20261106, 20261106, 'ONLINE', '{MANIFEST_SHA}')
        """)
    assert any(err in str(exc_info.value) for err in ("Check constraint", "chk_formal_snapshot_status", "CONSTRAINT"))

    # 写入符合 ASDC Schema 的 'READY' -> 成功入库
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
          (snapshot_id, start_date, end_date, status, source_manifest_sha256)
        VALUES ('snap_test_ready', 20261106, 20261106, 'READY', '{MANIFEST_SHA}')
        ON DUPLICATE KEY UPDATE status='READY'
    """)


def test_asdc_real_mysql_historical_release_ready_success():
    """集成测试 2: 真实 MySQL 中具有 status=READY、有效 manifest 及物理保留数据的历史版本成功认证。"""
    client = _get_adc_client()
    trade_date = 20261106
    snap_id = "snap_real_hist_01"
    rel_id = "rel_real_hist_01"

    # 1. 插入快照元数据 (READY)
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
          (snapshot_id, start_date, end_date, status, source_manifest_sha256)
        VALUES ('{snap_id}', {trade_date}, {trade_date}, 'READY', '{MANIFEST_SHA}')
        ON DUPLICATE KEY UPDATE status='READY', source_manifest_sha256='{MANIFEST_SHA}';
    """)

    # 2. 插入物理日线快照数据
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.dwd_equity_daily_bar_v2
          (snapshot_id, trade_date, ts_code, open, high, low, close, pre_close,
           volume_hands, volume_shares, amount_cny, adj_factor, adj_open, adj_high,
           adj_low, adj_close, adjustment_anchor_date, available_at, ingested_at,
           data_version, source_record_id, source_record_sha256, source_payload_sha256)
        VALUES
          ('{snap_id}', {trade_date}, '000001.SZ', 10.0, 10.5, 9.9, 10.2, 10.0,
           500.0, 50000.0, 510000.0, 1.0, 10.0, 10.5, 9.9, 10.2, {trade_date},
           '2026-11-06 18:00:00', '2026-11-06 18:05:00', 'dv_v1', 'rec_1', '{VALID_SHA256}', '{VALID_SHA256}')
        ON DUPLICATE KEY UPDATE close=10.2;
    """)

    # 3. 插入 Release 记录
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_data_release
          (release_id, release_type, trade_date, state, data_version_id, pit_snapshot_id,
           gate_evidence_sha256, critical_status_json, optional_status_json)
        VALUES
          ('{rel_id}', 'CERTIFIED_DAILY', {trade_date}, 'READY', 'dv_v1', 'pit_v1',
           '{VALID_SHA256}', '{{"status":"OK"}}', '{{"status":"OK"}}')
        ON DUPLICATE KEY UPDATE state='READY';
    """)

    # 4. 插入 6 大 Canonical Critical 数据集
    for ds_name in ("trade_calendar", "security_lifecycle_daily", "equity_daily_bar_raw",
                    "equity_adjustment_factor", "corporate_action_event", "daily_basic"):
        _exec_admin(f"""
            INSERT INTO {ASDC_DATABASE}.meta_data_release_dataset
              (release_id, dataset_name, snapshot_id, criticality, state, gate_evidence_sha256, visible_date_max)
            VALUES
              ('{rel_id}', '{ds_name}', '{snap_id}', 'CRITICAL', 'PASS', '{DATASET_SHA}', {trade_date})
            ON DUPLICATE KEY UPDATE state='PASS';
        """)

    # 执行历史归档 Gate 认证
    try:
        gate = client.release.check_release_gate(trade_date=trade_date, release_id=rel_id, is_historical=True)
        assert gate["eligible"] is True
        assert gate["state"] == "READY"
        assert gate["pointer_status"] == "CERTIFIED_HISTORICAL_ARCHIVE"
        assert "正式日版认证通过" in gate["reason"]
    finally:
        client.close()


def test_asdc_real_mysql_historical_superseded_origin_ready_success():
    """集成测试 3: 真实 MySQL 中 state=SUPERSEDED 且 superseded_from_state=READY 正确放行。"""
    client = _get_adc_client()
    trade_date = 20261106
    snap_id = "snap_real_sup_01"
    rel_id = "rel_real_sup_01"

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
          (snapshot_id, start_date, end_date, status, source_manifest_sha256)
        VALUES ('{snap_id}', {trade_date}, {trade_date}, 'READY', '{MANIFEST_SHA}')
        ON DUPLICATE KEY UPDATE status='READY', source_manifest_sha256='{MANIFEST_SHA}';
    """)

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.dwd_equity_daily_bar_v2
          (snapshot_id, trade_date, ts_code, open, high, low, close, pre_close,
           volume_hands, volume_shares, amount_cny, adj_factor, adj_open, adj_high,
           adj_low, adj_close, adjustment_anchor_date, available_at, ingested_at,
           data_version, source_record_id, source_record_sha256, source_payload_sha256)
        VALUES
          ('{snap_id}', {trade_date}, '000001.SZ', 10.0, 10.5, 9.9, 10.2, 10.0,
           500.0, 50000.0, 510000.0, 1.0, 10.0, 10.5, 9.9, 10.2, {trade_date},
           '2026-11-06 18:00:00', '2026-11-06 18:05:00', 'dv_v1', 'rec_sup', '{VALID_SHA256}', '{VALID_SHA256}')
        ON DUPLICATE KEY UPDATE close=10.2;
    """)

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_data_release
          (release_id, release_type, trade_date, state, superseded_from_state, supersedes_release_id,
           data_version_id, pit_snapshot_id, gate_evidence_sha256, critical_status_json, optional_status_json)
        VALUES
          ('{rel_id}', 'CERTIFIED_DAILY', {trade_date}, 'SUPERSEDED', 'READY', 'rel_newer_v2',
           'dv_v1', 'pit_v1', '{VALID_SHA256}', '{{"status":"OK"}}', '{{"status":"OK"}}')
        ON DUPLICATE KEY UPDATE state='SUPERSEDED', superseded_from_state='READY';
    """)

    for ds_name in ("trade_calendar", "security_lifecycle_daily", "equity_daily_bar_raw",
                    "equity_adjustment_factor", "corporate_action_event", "daily_basic"):
        _exec_admin(f"""
            INSERT INTO {ASDC_DATABASE}.meta_data_release_dataset
              (release_id, dataset_name, snapshot_id, criticality, state, gate_evidence_sha256, visible_date_max)
            VALUES
              ('{rel_id}', '{ds_name}', '{snap_id}', 'CRITICAL', 'PASS', '{DATASET_SHA}', {trade_date})
            ON DUPLICATE KEY UPDATE state='PASS';
        """)

    try:
        # 1. check_release_gate(is_historical=True) 成功通过并保留原状态
        gate = client.release.check_release_gate(trade_date=trade_date, release_id=rel_id, is_historical=True)
        assert gate["eligible"] is True
        assert gate["state"] == "SUPERSEDED"
        assert gate.get("superseded_from_state") == "READY"
        assert gate["is_production_eligible"] is True
        assert gate["pointer_status"] == "CERTIFIED_HISTORICAL_ARCHIVE"
        assert gate.get("active_pointer") is None  # 不伪造数据库活动指针
        assert "历史归档认证通过 (原状态: READY)" in gate["reason"]

        # 2. 贯穿 ExecutionContext.create_production() 成功创建且正确继承 CERTIFIED_DAILY
        ctx = ExecutionContext.create_production(
            trade_date=trade_date,
            release_gate_result=gate,
            is_historical=True,
        )
        assert ctx.mode == "PRODUCTION"
        assert ctx.is_historical is True
        assert ctx.certification_status == "CERTIFIED_DAILY"

        # 3. 贯穿 get_daily_bars_batch() 成功拉取经认证的物理历史日线
        bars = client.data.get_daily_bars_batch(
            symbols=["000001"],
            start_date=trade_date,
            end_date=trade_date,
            execution_context=ctx,
        )
        assert not bars.empty
        assert bars.attrs["certified"] is True
        assert bars.attrs["release_id"] == rel_id
        assert bars.attrs["provenance_proof"].certification_mode == "CERTIFIED_HISTORICAL_ARCHIVE"

        # 4. 空标的 symbols=[] 在物理快照存在时也产出已认证的空集
        empty_bars = client.data.get_daily_bars_batch(
            symbols=[],
            start_date=trade_date,
            end_date=trade_date,
            execution_context=ctx,
        )
        assert empty_bars.empty
        assert empty_bars.attrs["certified"] is True

        # 5. 严格验证 is_historical=False 时 SUPERSEDED 绝不放行
        # 5a. Gate 层拒绝
        gate_non_hist = client.release.check_release_gate(trade_date=trade_date, release_id=rel_id, is_historical=False)
        assert gate_non_hist["eligible"] is False
        assert "TOP_LEVEL_STATE_NOT_PERMITTED" in gate_non_hist.get("blocked_datasets", [])

        # 5b. 上下文工厂层拒绝
        with pytest.raises(ProductionAdmissionBlockedError):
            ExecutionContext.create_production(trade_date=trade_date, release_gate_result=gate, is_historical=False)

        # 5c. 取数执行层拒绝未标记 is_historical 的生产上下文
        fake_live_ctx = ExecutionContext(
            mode="PRODUCTION",
            trade_date=trade_date,
            release_id=rel_id,
            data_version_id="dv_v1",
            pit_snapshot_id="pit_v1",
            certification_status="CERTIFIED_DAILY",
            research_only=False,
            is_historical=False,
        )
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000001"],
                start_date=trade_date,
                end_date=trade_date,
                execution_context=fake_live_ctx,
            )
        assert "未通过 ASDC 生产准入核验" in str(exc_info.value)
    finally:
        client.close()


def test_asdc_real_mysql_physical_snapshot_purged_fails_closed():
    """集成测试 4: 元数据为 READY 但物理表 dwd_equity_daily_bar_v2 数据已被清理 -> 严格 Fail-Closed 阻断。"""
    client = _get_adc_client()
    trade_date = 20261106
    snap_id = "snap_purged_01"
    rel_id = "rel_purged_01"

    # 元数据存在且为 READY
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
          (snapshot_id, start_date, end_date, status, source_manifest_sha256)
        VALUES ('{snap_id}', {trade_date}, {trade_date}, 'READY', '{MANIFEST_SHA}')
        ON DUPLICATE KEY UPDATE status='READY', source_manifest_sha256='{MANIFEST_SHA}';
    """)

    # 物理表确保没有任何 snap_purged_01 的数据（模拟物理数据被清理/未保留）
    _exec_admin(f"DELETE FROM {ASDC_DATABASE}.dwd_equity_daily_bar_v2 WHERE snapshot_id='{snap_id}'")

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_data_release
          (release_id, release_type, trade_date, state, data_version_id, pit_snapshot_id,
           gate_evidence_sha256, critical_status_json, optional_status_json)
        VALUES
          ('{rel_id}', 'CERTIFIED_DAILY', {trade_date}, 'READY', 'dv_v1', 'pit_v1',
           '{VALID_SHA256}', '{{"status":"OK"}}', '{{"status":"OK"}}')
        ON DUPLICATE KEY UPDATE state='READY';
    """)

    for ds_name in ("trade_calendar", "security_lifecycle_daily", "equity_daily_bar_raw",
                    "equity_adjustment_factor", "corporate_action_event", "daily_basic"):
        _exec_admin(f"""
            INSERT INTO {ASDC_DATABASE}.meta_data_release_dataset
              (release_id, dataset_name, snapshot_id, criticality, state, gate_evidence_sha256, visible_date_max)
            VALUES
              ('{rel_id}', '{ds_name}', '{snap_id}', 'CRITICAL', 'PASS', '{DATASET_SHA}', {trade_date})
            ON DUPLICATE KEY UPDATE state='PASS';
        """)

    try:
        gate = client.release.check_release_gate(trade_date=trade_date, release_id=rel_id, is_historical=True)
        assert gate["eligible"] is False
        assert "HISTORICAL_PHYSICAL_DATA_PURGED" in gate["blocked_datasets"]
        assert "无任何物理数据留存" in gate["reason"]
    finally:
        client.close()


def test_asdc_real_mysql_empty_symbol_distinguishes_purged_from_legitimate_empty():
    """集成测试 5: get_daily_bars_batch 在真实 MySQL 下区分合法无行情与物理快照已清理。"""
    client = _get_adc_client()
    trade_date = 20261106
    snap_id = "snap_real_batch_01"
    rel_id = "rel_real_batch_01"

    # 插入有效元数据及平安银行 (000001.SZ) 行情
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_formal_data_snapshot_v2
          (snapshot_id, start_date, end_date, status, source_manifest_sha256)
        VALUES ('{snap_id}', {trade_date}, {trade_date}, 'READY', '{MANIFEST_SHA}')
        ON DUPLICATE KEY UPDATE status='READY', source_manifest_sha256='{MANIFEST_SHA}';
    """)

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.dwd_equity_daily_bar_v2
          (snapshot_id, trade_date, ts_code, open, high, low, close, pre_close,
           volume_hands, volume_shares, amount_cny, adj_factor, adj_open, adj_high,
           adj_low, adj_close, adjustment_anchor_date, available_at, ingested_at,
           data_version, source_record_id, source_record_sha256, source_payload_sha256)
        VALUES
          ('{snap_id}', {trade_date}, '000001.SZ', 10.0, 10.5, 9.9, 10.2, 10.0,
           500.0, 50000.0, 510000.0, 1.0, 10.0, 10.5, 9.9, 10.2, {trade_date},
           '2026-11-06 18:00:00', '2026-11-06 18:05:00', 'dv_v1', 'rec_batch', '{VALID_SHA256}', '{VALID_SHA256}')
        ON DUPLICATE KEY UPDATE close=10.2;
    """)

    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_data_release
          (release_id, release_type, trade_date, state, data_version_id, pit_snapshot_id,
           gate_evidence_sha256, critical_status_json, optional_status_json)
        VALUES
          ('{rel_id}', 'CERTIFIED_DAILY', {trade_date}, 'READY', 'dv_v1', 'pit_v1',
           '{VALID_SHA256}', '{{"status":"OK"}}', '{{"status":"OK"}}')
        ON DUPLICATE KEY UPDATE state='READY';
    """)

    # 插入规范活动指针
    _exec_admin(f"""
        INSERT INTO {ASDC_DATABASE}.meta_data_release_pointer
          (pointer_name, release_id, pointer_version, updated_by)
        VALUES
          ('CERTIFIED_DAILY', '{rel_id}', 1, 'integration_test')
        ON DUPLICATE KEY UPDATE release_id='{rel_id}';
    """)

    # 插入 6 大 Canonical Critical 数据集
    for ds_name in ("trade_calendar", "security_lifecycle_daily", "equity_daily_bar_raw",
                    "equity_adjustment_factor", "corporate_action_event", "daily_basic"):
        _exec_admin(f"""
            INSERT INTO {ASDC_DATABASE}.meta_data_release_dataset
              (release_id, dataset_name, snapshot_id, criticality, state, gate_evidence_sha256, visible_date_max)
            VALUES
              ('{rel_id}', '{ds_name}', '{snap_id}', 'CRITICAL', 'PASS', '{DATASET_SHA}', {trade_date})
            ON DUPLICATE KEY UPDATE state='PASS';
        """)

    ctx = ExecutionContext(
        mode="PRODUCTION",
        trade_date=trade_date,
        release_id=rel_id,
        data_version_id="dv_v1",
        pit_snapshot_id="pit_v1",
        certification_status="CERTIFIED_DAILY",
        research_only=False,
    )

    try:
        # 情况 1: 物理快照存在，查询不存在标的（如 000002.SZ）-> 正常返回 certified=True 的空 DataFrame
        empty_df = client.data.get_daily_bars_batch(
            symbols=["000002"],
            start_date=trade_date,
            end_date=trade_date,
            execution_context=ctx,
        )
        assert empty_df.empty
        assert empty_df.attrs["certified"] is True
        assert empty_df.attrs["release_id"] == rel_id

        # 情况 2: 将物理快照中的所有数据删除（模拟快照已被物理清理）-> 触发 ProductionAdmissionBlockedError
        client.close()
        _exec_admin(f"DELETE FROM {ASDC_DATABASE}.dwd_equity_daily_bar_v2 WHERE snapshot_id='{snap_id}'")
        with pytest.raises(ProductionAdmissionBlockedError) as exc_info:
            client.data.get_daily_bars_batch(
                symbols=["000002"],
                start_date=trade_date,
                end_date=trade_date,
                execution_context=ctx,
            )
        assert "物理快照数据缺失阻断" in str(exc_info.value)
        assert snap_id in str(exc_info.value)
    finally:
        client.close()
