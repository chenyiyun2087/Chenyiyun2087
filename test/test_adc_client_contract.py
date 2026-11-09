"""跨项目数据访问层 (ADCClient) 契约、细粒度门禁与安全单元测试。"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cross_project.adc_client import (
    ADCClient,
    ADCConnectionError,
    ADCDataReleaseBlockedError,
    ADCPITIntegrityError,
    ADCReadOnlyViolationError,
)


def test_config_resolution_explicit_params():
    client = ADCClient(
        host="127.0.0.1",
        port=3307,
        user="test_ro",
        password="test_pwd",
        database="custom_stock",
    )
    conf = client._resolve_config()
    assert conf["host"] == "127.0.0.1"
    assert conf["port"] == 3307
    assert conf["user"] == "test_ro"
    assert conf["password"] == "test_pwd"
    assert conf["database"] == "custom_stock"


def test_config_resolution_env_vars(monkeypatch):
    monkeypatch.setenv("ADC_MYSQL_HOST", "10.0.0.1")
    monkeypatch.setenv("ADC_MYSQL_PORT", "3308")
    monkeypatch.setenv("ADC_MYSQL_USER", "env_user")
    monkeypatch.setenv("ADC_MYSQL_PASSWORD", "env_pwd")
    monkeypatch.setenv("ADC_MYSQL_DATABASE", "env_db")

    client = ADCClient()
    conf = client._resolve_config()
    assert conf["host"] == "10.0.0.1"
    assert conf["port"] == 3308
    assert conf["user"] == "env_user"
    assert conf["password"] == "env_pwd"
    assert conf["database"] == "env_db"


def test_config_resolution_dedicated_ro(monkeypatch):
    monkeypatch.setenv("ADC_MYSQL_HOST", "10.0.0.1")
    monkeypatch.setenv("ADC_MYSQL_RO_USER", "ro_dedicated_user")
    monkeypatch.setenv("ADC_MYSQL_RO_PASSWORD", "ro_pwd")

    client = ADCClient(readonly_only=True)
    conf = client._resolve_config()
    assert conf["user"] == "ro_dedicated_user"
    assert conf["password"] == "ro_pwd"
    assert conf["_is_dedicated_ro"] is True


def test_config_resolution_missing_fails(monkeypatch):
    monkeypatch.delenv("ADC_MYSQL_HOST", raising=False)
    monkeypatch.delenv("ASDC_MYSQL_HOST", raising=False)
    monkeypatch.delenv("ADC_CONFIG_PATH", raising=False)
    monkeypatch.delenv("ASDC_CONFIG_PATH", raising=False)

    with patch("os.path.exists", return_value=False):
        client = ADCClient(config_path="/non/existent/path.ini")
        with pytest.raises(ADCConnectionError) as exc_info:
            client._resolve_config()
        assert "未能解析 AShareDataCenter 数据库配置" in str(exc_info.value)


def test_readonly_violation():
    client = ADCClient(host="127.0.0.1", user="ro_user", readonly_only=True)
    for forbidden in [
        "DELETE FROM dim_stock WHERE 1=1",
        "UPDATE dwd_stock_daily_standard SET close = 0",
        "DROP TABLE meta_data_release",
        "INSERT INTO meta_data_release VALUES (1)",
        "ALTER TABLE dim_stock ADD COLUMN hack INT",
        "TRUNCATE TABLE dws_feature_snapshot_di",
    ]:
        with pytest.raises(ADCReadOnlyViolationError):
            client._validate_readonly_sql(forbidden)

    # 允许合规只读查询
    client._validate_readonly_sql("SELECT * FROM dim_stock LIMIT 10")
    client._validate_readonly_sql("SHOW TABLES")
    client._validate_readonly_sql("EXPLAIN SELECT 1")
    client._validate_readonly_sql("DESCRIBE dim_stock")


def test_stock_status_pit_scd_hit():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)
    client._query = MagicMock(return_value=[{"status": "st"}])

    res = client.pit.stock_status("000001.SZ", 20261106)
    assert res["status"] == "st"
    assert res["is_listed"] is True
    assert res["is_st"] is True
    assert res["is_st_unknown"] is False
    assert res["quality_status"] == "CERTIFIED_PIT"


def test_stock_status_dim_stock_fallback_prevents_st_leakage():
    client = ADCClient(host="127.0.0.1", user="ro_user")

    def fake_table_ready(table_name):
        return table_name != "dim_stock_status_scd"

    client._table_ready = MagicMock(side_effect=fake_table_ready)
    client._query = MagicMock(return_value=[{"list_date": 20200101, "delist_date": None}])

    res = client.pit.stock_status("000001.SZ", 20261106)
    assert res["status"] == "listed_st_unknown"
    assert res["is_listed"] is True
    # 核心安全约束：禁止默认为 False！必须为 None
    assert res["is_st"] is None
    assert res["is_st_unknown"] is True
    assert res["quality_status"] == "DEGRADED_UNKNOWN_ST"


def test_stock_status_strict_mode_blocks_unknown_st():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=False)
    client._query = MagicMock(return_value=[{"list_date": 20200101, "delist_date": None}])

    with pytest.raises(ADCPITIntegrityError) as exc_info:
        client.pit.stock_status("000001.SZ", 20261106, strict=True)
    assert "无法验证 ST 状态" in str(exc_info.value)


def test_release_gate_missing():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)
    client._query = MagicMock(return_value=[])

    gate = client.release.check_release_gate(20261106)
    assert gate["eligible"] is False
    assert gate["is_production_eligible"] is False
    assert gate["state"] == "RELEASE_MISSING"


def test_release_gate_blocked_by_asdc():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)
    client._query = MagicMock(return_value=[{
        "release_id": "rel_20261106_01",
        "release_type": "CERTIFIED_DAILY",
        "trade_date": 20261106,
        "state": "BLOCKED",
        "data_version_id": "dv_v1",
        "pit_snapshot_id": "pit_v1",
        "gate_evidence_sha256": "abc123sha",
        "published_at": None,
    }])

    gate = client.release.check_release_gate(20261106)
    assert gate["eligible"] is False
    assert gate["is_production_eligible"] is False
    assert gate["state"] == "BLOCKED"

    with pytest.raises(ADCDataReleaseBlockedError) as exc_info:
        client.release.require_certified_release(20261106)
    assert "未通过 ASDC 质量门禁" in str(exc_info.value)


def test_release_gate_superseded_rejected():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)
    client._query = MagicMock(return_value=[{
        "release_id": "rel_20261106_old",
        "release_type": "CERTIFIED_DAILY",
        "trade_date": 20261106,
        "state": "SUPERSEDED",
        "data_version_id": "dv_v0",
        "pit_snapshot_id": "pit_v0",
        "gate_evidence_sha256": "abc123sha",
        "published_at": "2026-11-06 20:00:00",
    }])

    gate = client.release.check_release_gate(20261106)
    assert gate["eligible"] is False
    assert gate["state"] == "SUPERSEDED"


def test_release_gate_degraded_critical_dataset_fails():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)

    def fake_query(sql, params=None):
        if "meta_data_release" in sql and "meta_data_release_dataset" not in sql:
            return [{
                "release_id": "rel_20261106_deg",
                "release_type": "CERTIFIED_DAILY",
                "trade_date": 20261106,
                "state": "DEGRADED",
                "data_version_id": "dv_v1",
                "pit_snapshot_id": "pit_v1",
                "published_at": "2026-11-06 21:05:00",
            }]
        elif "meta_data_release_dataset" in sql:
            # 关键数据集 dwd_daily 处于 DEGRADED
            return [
                {"dataset_name": "dwd_daily", "criticality": "CRITICAL", "state": "DEGRADED"},
                {"dataset_name": "dim_stock", "criticality": "CRITICAL", "state": "PASS"},
            ]
        return []

    client._query = MagicMock(side_effect=fake_query)

    # 即使设置了 allow_degraded=True，因为关键数据集降级，也坚决不允许放行
    gate = client.release.check_release_gate(20261106, allow_degraded=True)
    assert gate["eligible"] is False
    assert gate["is_production_eligible"] is False
    assert "必需关键数据集未通过验证" in gate["reason"]


def test_release_gate_degraded_optional_only_allowed_when_flagged():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)

    def fake_query(sql, params=None):
        if "meta_data_release" in sql and "meta_data_release_dataset" not in sql:
            return [{
                "release_id": "rel_20261106_deg_opt",
                "release_type": "CERTIFIED_DAILY",
                "trade_date": 20261106,
                "state": "DEGRADED",
                "data_version_id": "dv_v1",
                "pit_snapshot_id": "pit_v1",
                "published_at": "2026-11-06 21:05:00",
            }]
        elif "meta_data_release_dataset" in sql:
            # 6 大 Canonical 关键数据集全 PASS，仅可选数据集 industry_history 处于 DEGRADED
            return [
                {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "security_lifecycle_daily", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "equity_daily_bar_raw", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "equity_adjustment_factor", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "corporate_action_event", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "daily_basic", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "industry_history", "criticality": "OPTIONAL", "state": "DEGRADED"},
            ]
        return []

    client._query = MagicMock(side_effect=fake_query)

    # 默认不允许
    gate_disallowed = client.release.check_release_gate(20261106, allow_degraded=False)
    assert gate_disallowed["eligible"] is False

    # 显式允许且仅 Optional 降级
    gate_allowed = client.release.check_release_gate(20261106, allow_degraded=True)
    assert gate_allowed["eligible"] is True
    assert "industry_history" in gate_allowed["degraded_optional_datasets"]


def test_release_gate_ready():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    client._table_ready = MagicMock(return_value=True)

    def fake_query(sql, params=None):
        if "meta_data_release" in sql and "meta_data_release_dataset" not in sql:
            return [{
                "release_id": "rel_20261106_03",
                "release_type": "CERTIFIED_DAILY",
                "trade_date": 20261106,
                "state": "READY",
                "data_version_id": "dv_v1",
                "pit_snapshot_id": "pit_v1",
                "gate_evidence_sha256": "abc123sha",
                "published_at": "2026-11-06 21:05:00",
            }]
        elif "meta_data_release_dataset" in sql:
            return [
                {"dataset_name": "trade_calendar", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "security_lifecycle_daily", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "equity_daily_bar_raw", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "equity_adjustment_factor", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "corporate_action_event", "criticality": "CRITICAL", "state": "PASS"},
                {"dataset_name": "daily_basic", "criticality": "CRITICAL", "state": "PASS"},
            ]
        return []

    client._query = MagicMock(side_effect=fake_query)

    gate = client.release.check_release_gate(20261106)
    assert gate["eligible"] is True
    assert gate["state"] == "READY"
    assert gate["release_id"] == "rel_20261106_03"


def test_daily_bars_batch_contract():
    client = ADCClient(host="127.0.0.1", user="ro_user")
    mock_rows = [
        {
            "ts_code": "000001.SZ",
            "trade_date": 20261106,
            "open": 10.0,
            "high": 10.5,
            "low": 9.9,
            "close": 10.2,
            "volume": 50000.0,
            "amount": 510000.0,
        },
        {
            "ts_code": "600000.SH",
            "trade_date": 20261106,
            "open": 8.0,
            "high": 8.2,
            "low": 7.9,
            "close": 8.1,
            "volume": 30000.0,
            "amount": 243000.0,
        },
    ]
    client._query = MagicMock(return_value=mock_rows)

    df = client.data.get_daily_bars_batch(["000001", "600000"], start_date=20261106, end_date=20261106)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    # 严格验证包含 ScoreRank 必需的 8 大字段
    expected_cols = ["symbol", "ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    for col in expected_cols:
        assert col in df.columns
    assert set(df["symbol"].tolist()) == {"000001", "600000"}
