import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.research.build_strict_corporate_action_snapshot import build
from scripts.research.run_strict_reliability_matrix import run
from scripts.research.verify_strict_ledger_evidence import verify
from scripts.research_trusted_strategy_account_backtest import (
    _apply_lifecycle_snapshot,
    _load_corporate_action_snapshot,
    _load_lifecycle_snapshot,
)


def _snapshots(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source, lifecycle, calendar = tmp_path / "actions.csv", tmp_path / "lifecycle.csv", tmp_path / "calendar.csv"
    pd.DataFrame([{"symbol": "000001", "action_type": "dividend_cash", "effective_date": "2026-01-02", "source_event_id": "ca-1", "as_of_timestamp": "2026-01-01T10:00:00+08:00", "source_complete": True, "cash_per_share": .1}]).to_csv(source, index=False)
    pd.DataFrame([{"symbol": "000001", "effective_date": "2026-01-01", "is_listed": 1, "is_suspended": 0}]).to_csv(lifecycle, index=False)
    pd.DataFrame({"trade_date": ["2026-01-01", "2026-01-02"]}).to_csv(calendar, index=False)
    build(source, tmp_path / "out", "fixture-v1", lifecycle, calendar)
    return tmp_path / "out"


def test_snapshot_manifest_tampering_and_missing_provenance_are_rejected(tmp_path):
    out = _snapshots(tmp_path)
    manifest_path = out / "manifest.json"
    actions = out / "strict_corporate_actions.csv"
    actions.write_text(actions.read_text() + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        _load_corporate_action_snapshot(actions, manifest_path)

    _snapshots(tmp_path / "fresh")
    fresh = tmp_path / "fresh/out"
    manifest = json.loads((fresh / "manifest.json").read_text())
    manifest.pop("dataset_version")
    (fresh / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing provenance"):
        _load_corporate_action_snapshot(fresh / "strict_corporate_actions.csv", fresh / "manifest.json")


def test_lifecycle_daily_panel_requires_exact_price_coverage(tmp_path):
    out = _snapshots(tmp_path)
    lifecycle, _, _ = _load_lifecycle_snapshot(out / "strict_security_lifecycle.csv", out / "manifest.json")
    prices = pd.DataFrame({"symbol": ["000001", "000001"], "trade_date": [pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-01-02").date()], "raw_volume": [1, 1]})
    assert len(_apply_lifecycle_snapshot(prices, lifecycle)) == 2
    with pytest.raises(RuntimeError, match="missing a price/session status"):
        _apply_lifecycle_snapshot(pd.DataFrame({"symbol": ["000002"], "trade_date": [pd.Timestamp("2026-01-01").date()], "raw_volume": [1]}), lifecycle)


def test_matrix_audit_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    from scripts.research import run_strict_reliability_matrix as matrix

    missing_report = tmp_path / "missing" / "trusted_account_backtest_report.json"
    monkeypatch.setattr(matrix.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps({"files": {"json": str(missing_report)}}), stderr=""))
    result = run(SimpleNamespace(output_dir=tmp_path / "matrix", corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=False, max_runs=1, initial_cash=500000.0))
    assert result["runs"][0]["status"] == "AUDIT_ERROR"
    assert (tmp_path / "matrix" / "001_development_no_cap_750_0bp" / "cell_manifest.json").exists()


def test_evidence_verifier_rejects_tampering(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    payload = evidence / "payload.txt"
    payload.write_text("original", encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (evidence / "manifest.json").write_text(json.dumps({"commit": "abc", "files": {"payload.txt": digest}}), encoding="utf-8")
    (evidence / "SHA256SUMS").write_text(f"{digest}  payload.txt\n", encoding="utf-8")
    assert verify(evidence)["verified"] is True
    payload.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="verification failed"):
        verify(evidence)


def test_evidence_verifier_rejects_missing_or_mismatched_sha256sums(tmp_path):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    payload = evidence / "payload.txt"
    payload.write_text("original", encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    (evidence / "manifest.json").write_text(json.dumps({"commit": "abc", "files": {"payload.txt": digest}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing SHA256SUMS"):
        verify(evidence)
    (evidence / "SHA256SUMS").write_text("0" * 64 + "  payload.txt\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest/SHA256SUMS mismatch"):
        verify(evidence)
