"""Minimal regression locks for the v3 PIT evidence contract."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import (
    get_contract_sha256,
    get_required_columns,
    get_source_families,
    validate_lineage_frame,
)
from scripts.pit import run_snapshot_extract as extractor
from scripts.research.pit_data_adapter import build_pit_adapter_manifest
from scripts.research.pit_factor_panel_builder import build_pit_factor_panel
from scripts.research.qualify_pit_e3 import qualify_pit_e3


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Conn:
    def __init__(self) -> None:
        self.closed = False
        self.rollback_count = 0

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


def test_benchmark_is_read_on_the_same_connection_as_all_families(tmp_path, monkeypatch):
    """The formal extractor must issue all nine reads through one connection."""
    connection = _Conn()
    seen: list[object] = []

    monkeypatch.setattr(extractor, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(extractor, "_load_config", lambda: {})
    monkeypatch.setattr(extractor, "_get_connection", lambda config: connection)
    monkeypatch.setattr(
        extractor,
        "_begin_consistent_snapshot",
        lambda conn, config: {
            "snapshot_started_at": "2026-08-10T12:00:00+00:00",
            "transaction_started_at": "2026-08-10T12:00:00+00:00",
            "provider_snapshot_token": "gtid:abc",
            "gtid_executed": "gtid:abc",
            "binlog_file": "binlog.1",
            "binlog_position": 42,
            "transaction_isolation": "REPEATABLE READ",
            "server_identity": {"server_uuid": "srv"},
            "gtid_provenance": {"gtid_executed": "gtid:abc"},
            "binlog_provenance": {"file": "binlog.1", "position": 42},
            "consistent_snapshot": True,
        },
    )

    def _read_sql(query, conn, *args, **kwargs):
        seen.append(conn)
        raise RuntimeError("fixture read only")

    monkeypatch.setattr(extractor.pd, "read_sql", _read_sql)
    monkeypatch.setattr(
        "scripts.pit.post_extract_enrich.enrich_release",
        lambda release_dir: {"enriched": [], "errors": []},
    )

    result = extractor.extract_all("same_txn", skip_consistency_snapshot=False)

    assert len(seen) == len(get_source_families()) == 9
    assert all(item is connection for item in seen)
    assert "benchmark_index" in extractor.FAMILY_QUERIES
    assert result["data_status"] == "BLOCKED_DATA"


def test_missing_provider_token_writes_blocked_e0_manifest(tmp_path, monkeypatch):
    connection = _Conn()
    seen: list[object] = []
    monkeypatch.setattr(extractor, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(extractor, "_load_config", lambda: {})
    monkeypatch.setattr(extractor, "_get_connection", lambda config: connection)
    monkeypatch.setattr(
        extractor,
        "_begin_consistent_snapshot",
        lambda conn, config: {
            "snapshot_started_at": "2026-08-10T12:00:00+00:00",
            "transaction_started_at": "2026-08-10T12:00:00+00:00",
            "provider_snapshot_token": "",
            "gtid_executed": "gtid:abc",
            "binlog_file": "binlog.1",
            "binlog_position": 42,
            "transaction_isolation": "REPEATABLE READ",
            "server_identity": {"server_uuid": "srv"},
            "gtid_provenance": {"gtid_executed": "gtid:abc"},
            "binlog_provenance": {"file": "binlog.1", "position": 42},
            "consistent_snapshot": True,
        },
    )
    monkeypatch.setattr(
        extractor.pd,
        "read_sql",
        lambda query, conn, *args, **kwargs: seen.append(conn)
        or (_ for _ in ()).throw(RuntimeError("diagnostic fixture")),
    )
    monkeypatch.setattr(
        "scripts.pit.post_extract_enrich.enrich_release",
        lambda release_dir: {"enriched": [], "errors": []},
    )

    result = extractor.extract_all("missing_token")

    assert result["status"] == "BLOCKED"
    assert result["data_status"] == "BLOCKED_DATA"
    assert result["qualified_evidence_level"] is None
    assert "provider_snapshot_token_missing" in result["blockers"]
    assert result["gtid_provenance"]["gtid_executed"] == "gtid:abc"
    assert result["snapshot_token"] == ""
    assert result["provider_snapshot_token"] == ""
    assert set(result["file_sha256"]) == set(get_source_families())


def test_file_adapter_and_panel_never_self_qualify_e3(tmp_path):
    # Reuse the established long synthetic fixture; it is intentionally FILE
    # transport but remains diagnostic and cannot satisfy an independent E3
    # qualifier.
    from test.test_pit_factor_panel_builder import _write_qualified_inputs

    paths, _ = _write_qualified_inputs(tmp_path)
    config = {
        "adapter_type": "FILE",
        "evidence_origin": "SYNTHETIC",
        "release": "fixture-release",
        "provider": "fixture-provider",
        "retrieved_at": "2019-02-01T10:00:00+08:00",
        "schema_semantic_version": "fixture-v1",
        "field_definition_hash": "fixture-field-definition-sha",
        "sources": {
            name: {"path": str(path), "version": "fixture-v1"}
            for name, path in paths.items()
        },
    }
    config_path = tmp_path / "adapter.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    adapter = build_pit_adapter_manifest(config_path, tmp_path / "adapter_output")
    assert adapter["status"] == "PASS"
    assert adapter["qualified_evidence_level"] is None

    manifest = Path(adapter["manifest_path"])
    panel = build_pit_factor_panel(
        market_path=paths["market"],
        universe_path=paths["universe"],
        financial_path=paths["financial"],
        industry_path=paths["industry"],
        adjustment_path=paths["adjustment"],
        source_manifest_path=manifest,
        output_dir=tmp_path / "panel_output",
        profile_name="formal_v5_0",
    )
    assert panel["status"] == "PASS"
    assert panel["qualified_evidence_level"] is None
    assert panel["evidence_level"] == "E0"
    assert panel["data_status"] != "DATA_E3_QUALIFIED"


def test_lineage_timestamp_after_decision_cutoff_blocks():
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-10"],
            "market_available_at": ["2026-08-10T20:00:00+08:00"],
            "source_published_at": ["2026-08-10T20:00:00+08:00"],
            "warehouse_loaded_at": ["2026-08-10T20:00:00+08:00"],
            "decision_cutoff": ["2026-08-10T19:00:00+08:00"],
            "availability_source": ["provider:fixture"],
        }
    )
    blockers = validate_lineage_frame(frame, "market")
    assert "lineage_market_available_at_after_decision_cutoff:market" in blockers


def test_independent_qualifier_rejects_gtid_only_identity(tmp_path):
    adapter = {
        "status": "QUALIFIED",
        "evidence_origin": "HISTORICAL_REAL",
        "claimed_evidence_level": "E1",
        "field_definition_hash": "not-contract",
        "snapshot_identity": {
            "provider_snapshot_token": "",
            "transaction_started_at": "2026-08-10T12:00:00+00:00",
            "transaction_finished_at": "2026-08-10T12:01:00+00:00",
            "transaction_isolation": "REPEATABLE READ",
            "consistent_snapshot": True,
            "server_identity": {"server_uuid": "srv"},
            "gtid_provenance": {"gtid_executed": "gtid:abc"},
            "binlog_provenance": {"file": "binlog.1", "position": 42},
        },
    }
    adapter["content_sha256"] = canonical_sha(
        {key: value for key, value in adapter.items() if key != "content_sha256"}
    )
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps(adapter, sort_keys=True), encoding="utf-8")
    audit = {
        "status": "PASS",
        "qualified_evidence_level": None,
        "claimed_evidence_level": "E1",
        "semantic_contract_sha256": "not-contract",
        "audit_details": {},
    }
    audit["content_sha256"] = canonical_sha(
        {key: value for key, value in audit.items() if key != "content_sha256"}
    )
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
    profile_path = tmp_path / "strict.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "evidence_level": "E3",
                "require_point_in_time_consistency": True,
                "require_independent_consistent_snapshot": True,
                "forbid_e0_derived_fields": True,
                "require_benchmark_index_data": True,
            }
        ),
        encoding="utf-8",
    )

    result = qualify_pit_e3(
        snapshots_dir=tmp_path / "snapshots",
        adapter_manifest_path=adapter_path,
        audit_report_path=audit_path,
        strict_profile_path=profile_path,
    )
    assert result["status"] == "BLOCKED"
    assert result["qualified_evidence_level"] is None
    assert "provider_snapshot_token_missing" in result["blockers"]


def test_independent_qualifier_binds_all_nine_families(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    day = "2026-08-10"
    lineage = {
        "source_published_at": f"{day}T15:00:00+08:00",
        "warehouse_loaded_at": f"{day}T15:10:00+08:00",
        "decision_cutoff": f"{day}T21:30:00+08:00",
        "availability_source": "provider:read_view",
    }
    common = {"trade_date": day, **lineage}
    rows = {
        "market": {**common, "symbol": "000001", "open": 1.0, "close": 1.1,
                   "pre_close": 1.0, "amount": 100.0, "circ_mv": 1000.0,
                   "market_return": 0.01, "market_regime": "NEUTRAL",
                   "market_available_at": f"{day}T15:00:00+08:00"},
        "universe": {**common, "symbol": "000001", "is_listed": 1, "is_st": 0,
                     "is_suspended": 0, "limit_status": "NORMAL",
                     "security_status_transition": "LISTED",
                     "universe_available_at": f"{day}T15:00:00+08:00"},
        "financial": {**common, "symbol": "000001", "pb": 1.0,
                       "financial_period_end": "2026-06-30", "announcement_date": "2026-08-01",
                       "financial_available_at": f"{day}T15:00:00+08:00",
                       "revision_id": "r1", "revision_sequence": 1,
                       "financial_source_snapshot_sha": "a" * 64},
        "industry": {**common, "symbol": "000001", "industry": "I1",
                     "industry_available_at": f"{day}T15:00:00+08:00",
                     "valid_from": "2026-01-01", "valid_to": "2027-01-01",
                     "industry_code": "I1", "industry_name": "I1"},
        "adjustment": {**common, "symbol": "000001", "adj_factor": 1.0,
                       "corporate_action_type": "NONE", "ex_date": day,
                       "record_date": day, "adjustment_factor_version": 1,
                       "adjustment_available_at": f"{day}T15:00:00+08:00"},
        "trade_calendar": {"cal_date": day, "exchange": "SSE", "is_open": 1,
                           "source": "provider", "available_at": f"{day}T15:00:00+08:00", **lineage},
        "security_lifecycle": {**common, "symbol": "000001", "is_listed": 1,
                               "is_st": 0, "is_suspended": 0, "listed_date": "2020-01-01",
                               "security_status_transition": "LISTED",
                               "lifecycle_available_at": f"{day}T15:00:00+08:00"},
        "corporate_actions": {**common, "symbol": "000001", "corporate_action_type": "DIVIDEND",
                              "source_event_id": "src-1", "ex_date": day, "record_date": day,
                              "event_id": "event-1", "effective_date": day,
                              "as_of_timestamp": f"{day}T09:00:00+08:00", "source_complete": True,
                              "event_hash": "hash-1", "corporate_action_available_at": f"{day}T09:00:00+08:00"},
        "benchmark_index": {"trade_date": day, "index_code": "000300.SH", "index_label": "csi300",
                            "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05,
                            "pre_close": 1.0, "pct_chg": 0.05, "vol": 10.0, "amount": 10.0,
                            "benchmark_available_at": f"{day}T15:00:00+08:00", "ret_5d": 0.01,
                            "ret_10d": 0.02, "ret_20d": 0.03, "ret_60d": 0.04, **lineage},
    }
    sources = {}
    audit_details = {}
    for family in get_source_families():
        frame = pd.DataFrame([rows[family]])
        assert get_required_columns(family).issubset(frame.columns)
        path = snapshots / f"{family}.parquet"
        frame.to_parquet(path, index=False)
        sha = _sha(path)
        sources[family] = {
            "path": str(path),
            "sha256": sha,
            "content_sha256": sha,
            "query_sha256": "q" * 64,
            "parameter_sha256": "p" * 64,
            "schema_hash": "s" * 64,
            "rows": 1,
        }
        audit_details[family] = {"file_sha256": sha, "blockers": []}
        adapter = {
            "status": "QUALIFIED", "evidence_origin": "HISTORICAL_REAL",
            "release_id": "release-fixture",
            "decision_contract_id": "ashare_t2130_t1_v1",
        "claimed_evidence_level": "E1", "field_definition_hash": get_contract_sha256(),
        "sources": sources,
        "snapshot_identity": {
            "provider_snapshot_token": "provider-read-view-1",
            "transaction_started_at": f"{day}T12:00:00+00:00",
            "transaction_finished_at": f"{day}T12:01:00+00:00",
            "transaction_isolation": "REPEATABLE READ", "consistent_snapshot": True,
            "server_identity": {"server_uuid": "srv"},
            "gtid_provenance": {"gtid_executed": "gtid:abc"},
            "binlog_provenance": {"file": "binlog.1", "position": 42},
        },
    }
    adapter["content_sha256"] = canonical_sha({k: v for k, v in adapter.items() if k != "content_sha256"})
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps(adapter, sort_keys=True), encoding="utf-8")
    audit = {
        "status": "PASS", "qualified_evidence_level": None,
        "claimed_evidence_level": "E1", "semantic_contract_sha256": get_contract_sha256(),
        "audit_details": audit_details,
    }
    audit["content_sha256"] = canonical_sha({k: v for k, v in audit.items() if k != "content_sha256"})
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit, sort_keys=True), encoding="utf-8")
    result = qualify_pit_e3(
        snapshots_dir=snapshots,
        adapter_manifest_path=adapter_path,
        audit_report_path=audit_path,
        strict_profile_path=Path("config/validation_profiles/formal_e3_strict.yaml"),
    )
    assert result["status"] == "PASS", result["blockers"]
    assert result["qualified_evidence_level"] == "E3"
    assert result["source_manifest_sha256"] == _sha(adapter_path)
    assert result["audit_sha256"] == _sha(audit_path)
