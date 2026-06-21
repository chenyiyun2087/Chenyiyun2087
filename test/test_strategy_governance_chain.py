from pathlib import Path

import pytest

from runtime.decision_engine import PortfolioState, generate_targets, validate_deterministic_replay
from runtime.governance import REQUIRED_EVIDENCE_FIELDS, canonical_sha, write_evidence_package
from runtime.ledger_runtime import OrderStatus, reconcile_daily, validate_order_transition
from runtime.release_manifest import ReleaseManifest
from scripts.ops.evaluate_shadow_promotion import evaluate_disabled_shadow_gates
from scripts.ops.verify_strict_ledger_gate import HARD_METRICS, strict_ledger_config_coverage


def _release() -> ReleaseManifest:
    return ReleaseManifest(
        release_id="governance-test-20260620", strategy_wrapper_id="production_governed_vol_position",
        selection_engine_id="baseline", risk_governor_id="adaptive", execution_model_id="t_plus_1_open",
        config_sha="test-config", git_commit_sha="abc123", data_snapshot_hash="snapshot123",
        feature_schema_version="1", signal_date="2026-06-19", execution_date="2026-06-22",
    )


def test_strict_ledger_config_hard_thresholds_are_all_implemented():
    coverage = strict_ledger_config_coverage()
    assert coverage and all(coverage.values())
    assert {"max_t_plus_1_fill_violations", "max_order_conservation_errors", "max_rounding_cash_residual_pct_target_gross"}.issubset(HARD_METRICS)


def test_release_rejects_unknown_or_non_t_plus_one_provenance():
    with pytest.raises(ValueError, match="data_snapshot_hash"):
        _release().__class__(**{**_release().__dict__, "data_snapshot_hash": ""}).validate_promotable()
    with pytest.raises(ValueError, match="execution_not_t_plus_1"):
        _release().__class__(**{**_release().__dict__, "execution_date": "2026-06-19"}).validate_promotable()


def test_runtime_order_state_machine_and_reconciliation_are_fail_closed():
    validate_order_transition(OrderStatus.PLANNED, OrderStatus.SUBMITTED)
    with pytest.raises(ValueError, match="illegal_order_transition"):
        validate_order_transition(OrderStatus.FILLED, OrderStatus.PARTIAL)
    report = reconcile_daily([{"intent_id": "a", "planned_price": 10}], [{"intent_id": "b", "filled_price": 10}], {"000001": 100}, {"000001": 90}, 1000, 990)
    assert not report["theoretical_vs_actual_orders"]["passed"]
    assert not report["theoretical_vs_actual_positions"]["passed"]


def test_evidence_package_has_tamper_evident_manifest(tmp_path: Path):
    uri, sha = write_evidence_package("r1", {"gate": {"pass": True}}, tmp_path)
    assert (Path(uri) / "SHA256SUMS.json").exists()
    assert sha == canonical_sha(__import__("json").loads((Path(uri) / "SHA256SUMS.json").read_text()))
    assert "release_id" in REQUIRED_EVIDENCE_FIELDS


class _NoDataEngine:
    def connect(self):
        raise RuntimeError("no database")


def test_shadow_promotion_requires_release_scoped_source_data():
    gates = evaluate_disabled_shadow_gates(_NoDataEngine(), "strategy_a", release_id="release_a", start_date="2026-06-01", end_date="2026-06-20")
    assert len(gates) == 1 and not gates[0].passed

