import json

import pandas as pd
import pytest

from scripts.research.build_strict_corporate_action_snapshot import build


def test_snapshot_is_versioned_and_hashes_each_event(tmp_path):
    source = tmp_path / "source.csv"
    pd.DataFrame([{
        "symbol": "000001.SZ", "action_type": "dividend_stock", "effective_date": "2026-01-02",
        "source_event_id": "tushare:1", "as_of_timestamp": "2026-01-01T18:00:00+08:00", "source_complete": True,
        "cash_per_share": .1,
    }]).to_csv(source, index=False)
    manifest = build(source, tmp_path / "snapshot", "tushare_ca_v1")
    saved = pd.read_csv(tmp_path / "snapshot/strict_corporate_actions.csv")
    assert manifest["dataset_version"] == "tushare_ca_v1"
    assert saved.loc[0, "event_hash"]
    assert json.loads((tmp_path / "snapshot/manifest.json").read_text())["event_count"] == 1


def test_snapshot_rejects_missing_timing_contract(tmp_path):
    source = tmp_path / "bad.csv"
    pd.DataFrame([{"symbol": "000001"}]).to_csv(source, index=False)
    with pytest.raises(RuntimeError, match="missing fields"):
        build(source, tmp_path / "snapshot", "v1")


def test_snapshot_preserves_false_source_completeness(tmp_path):
    source = tmp_path / "source.csv"
    pd.DataFrame([{
        "symbol": "000001", "action_type": "split", "effective_date": "2026-01-02",
        "source_event_id": "tushare:2", "as_of_timestamp": "2026-01-01T18:00:00+08:00", "source_complete": "false",
    }]).to_csv(source, index=False)
    build(source, tmp_path / "snapshot", "v1")
    assert pd.read_csv(tmp_path / "snapshot/strict_corporate_actions.csv").loc[0, "source_complete"] == False


def test_snapshot_optionally_captures_security_lifecycle(tmp_path):
    source, lifecycle = tmp_path / "actions.csv", tmp_path / "lifecycle.csv"
    pd.DataFrame([{"symbol": "000001", "action_type": "split", "effective_date": "2026-01-02", "source_event_id": "1", "as_of_timestamp": "2026-01-01T18:00:00+08:00", "source_complete": True}]).to_csv(source, index=False)
    pd.DataFrame([{"symbol": "000001.SZ", "effective_date": "2026-01-02", "is_listed": 1, "is_suspended": 0}]).to_csv(lifecycle, index=False)
    manifest = build(source, tmp_path / "snapshot", "v1", lifecycle)
    assert manifest["lifecycle_row_count"] == 1
    assert (tmp_path / "snapshot/strict_security_lifecycle.csv").exists()


def test_snapshot_atomizes_combined_source_event(tmp_path):
    source = tmp_path / "actions.csv"
    pd.DataFrame([{"symbol": "000001", "action_type": "dividend_stock", "effective_date": "2026-01-02", "source_event_id": "1", "as_of_timestamp": "2026-01-01T10:00:00+08:00", "source_complete": True, "cash_per_share": .1, "stock_ratio": .2}]).to_csv(source, index=False)
    build(source, tmp_path / "snapshot", "v1")
    saved = pd.read_csv(tmp_path / "snapshot/strict_corporate_actions.csv")
    assert set(saved["action_type"]) == {"dividend_cash", "stock_bonus"}
    assert saved["parent_source_event_id"].nunique() == 1
