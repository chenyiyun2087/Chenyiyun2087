"""Wave 2 governance regression coverage (pure fixtures, no exports writes)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from runtime.capital_governance import evaluate_capital_stage
from runtime.epoch_governance import (
    SOAK_REQUIRED_HASHES,
    SOAK_REQUIRED_METRICS,
    freeze_forward_epoch,
    update_engineering_soak,
)
from runtime.formal_status_semantics import (
    ArtifactStatus,
    ContractStatus,
    GateEconomicStatus,
    make_gate_status,
)
from runtime.verifier_contracts import check_e4_contract, check_event_projection_contract
from scripts.ops.build_e4_evidence_package import build_e4_evidence_package
from runtime.epoch_governance import canonical_sha


def test_gate_file_presence_does_not_imply_economic_pass():
    gate = make_gate_status(
        artifact_status=ArtifactStatus.ARTIFACT_PRESENT,
        contract_status=ContractStatus.CONTRACT_VALID,
        economic_status=GateEconomicStatus.FAIL,
    )
    assert gate.resolved_status == "BLOCKED"


def test_gate_serialization_uses_exact_canonical_values_and_reads_legacy_values():
    assert {member.value for member in ArtifactStatus} == {
        "ARTIFACT_PRESENT", "ARTIFACT_MISSING", "ARTIFACT_INVALID",
    }
    assert {member.value for member in ContractStatus} == {
        "CONTRACT_VALID", "CONTRACT_INVALID", "CONTRACT_NOT_EVALUATED",
    }
    assert {member.value for member in GateEconomicStatus} == {
        "ECONOMIC_PASS", "ECONOMIC_FAIL", "ECONOMIC_NOT_EVALUATED", "ECONOMIC_NOT_APPLICABLE",
    }
    gate = make_gate_status(
        artifact_status="MISSING",
        contract_status="INVALID",
        economic_status="FAIL",
    )
    assert gate.to_dict()["artifact_status"] == "ARTIFACT_MISSING"
    assert gate.to_dict()["contract_status"] == "CONTRACT_INVALID"
    assert gate.to_dict()["economic_status"] == "ECONOMIC_FAIL"
    parsed = type(gate).from_dict({
        "artifact_status": "MISSING",
        "contract_status": "NOT_EVALUATED",
        "economic_status": "NOT_APPLICABLE",
    })
    assert parsed.to_dict()["artifact_status"] == "ARTIFACT_MISSING"
    assert parsed.to_dict()["contract_status"] == "CONTRACT_NOT_EVALUATED"
    assert parsed.to_dict()["economic_status"] == "ECONOMIC_NOT_APPLICABLE"


def test_canary_is_50k_and_never_auto_authorizes_capital():
    evidence = {
        "e3_passed": True,
        "formal_new_epoch": True,
        "formal_shadow_days": 60,
        "completed_round_trips": 30,
        "alpha_t": 2,
        "adjusted_p": 0.05,
        "positive_excess_ratio": 0.60,
        "sharpe": 1,
        "mdd": 0.25,
        "cost_2x_passed": True,
        "shadow_zero_difference": True,
        "manual_approval": True,
        "reconciliation_errors": 0,
    }
    decision = evaluate_capital_stage("CANARY", evidence)
    assert decision.eligible and decision.maximum_capital == 50_000
    assert decision.capital_authority is False
    assert decision.allowed_new_capital == 0


def test_soak_hash_drift_resets_and_freeze_is_forward_only():
    hashes = {key: "a" * 64 for key in SOAK_REQUIRED_HASHES}
    state: dict = {}
    current = date(2026, 1, 1)
    open_dates: list[str] = []
    for _ in range(21):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        day = current.isoformat()
        open_dates.append(day)
        state = update_engineering_soak(state, day, hashes, open_dates=open_dates)
        current += timedelta(days=1)
    assert state["consecutive_zero_defect_days"] >= 20
    drifted = update_engineering_soak(state, current.isoformat(), {key: "b" * 64 for key in SOAK_REQUIRED_HASHES}, open_dates=open_dates + [current.isoformat()])
    assert drifted["consecutive_zero_defect_days"] == 0
    manifest = freeze_forward_epoch(
        state,
        freeze_date="2026-02-01",
        open_dates=["2026-02-02"],
        epoch_id="formal-epoch",
        release_id="release",
        git_sha="a" * 64,
        config_sha="b" * 64,
        dependency_sha="c" * 64,
        candidate_sha="d" * 64,
        stat_plan_sha="e" * 64,
        pit_contract_sha="f" * 64,
        test_result_sha="0" * 64,
    )
    assert manifest["start"] == "2026-02-02"


def test_freeze_rejects_non_sha256_bindings():
    hashes = {key: "a" * 64 for key in SOAK_REQUIRED_HASHES}
    state: dict = {}
    current = date(2026, 1, 1)
    open_dates: list[str] = []
    for _ in range(20):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        day = current.isoformat()
        open_dates.append(day)
        state = update_engineering_soak(state, day, hashes, open_dates=open_dates)
        current += timedelta(days=1)
    kwargs = {
        "soak_state": state,
        "freeze_date": "2026-02-01",
        "open_dates": ["2026-02-02"],
        "epoch_id": "formal-epoch",
        "release_id": "release",
        "git_sha": "a" * 64,
        "config_sha": "b" * 64,
        "dependency_sha": "c" * 64,
        "candidate_sha": "d" * 64,
        "stat_plan_sha": "e" * 64,
        "pit_contract_sha": "f" * 64,
        "test_result_sha": "0" * 64,
    }
    for field in ("git_sha", "config_sha", "dependency_sha", "candidate_sha", "stat_plan_sha", "pit_contract_sha", "test_result_sha"):
        invalid = dict(kwargs, **{field: "short"})
        with pytest.raises(ValueError, match="freeze_sha_binding_invalid"):
            freeze_forward_epoch(**invalid)
    with pytest.raises(ValueError, match="freeze_seal_sha_invalid"):
        freeze_forward_epoch(**dict(kwargs, seal_sha="short"))


def test_each_explicit_soak_metric_resets_and_is_saved():
    hashes = {key: "a" * 64 for key in SOAK_REQUIRED_HASHES}
    for metric in SOAK_REQUIRED_METRICS:
        first = update_engineering_soak({}, "2026-01-02", hashes, open_dates=["2026-01-02"])
        second = update_engineering_soak(
            first,
            "2026-01-05",
            hashes,
            open_dates=["2026-01-02", "2026-01-05"],
            metrics={metric: 1},
        )
        assert second["consecutive_zero_defect_days"] == 0
        assert second["days"][-1]["metrics"][metric] == 1


def test_old_epoch_and_event_projection_are_blocked_or_proved():
    ok, _ = check_e4_contract("2026-08-05", "v5.5.3-2026-08-05", ["2026-08-06"], 1)
    assert not ok
    events = [
        {"event_type": "ORDER_PRECOMMITTED", "order_id": "o1", "signal_date": "2027-01-01", "execution_date": "2027-01-02", "challenger_id": "C", "symbol": "000001", "side": "BUY"},
        {"event_type": "BUY_FILLED", "order_id": "o1"},
    ]
    projected = [{"order_id": "o1", "signal_date": "2027-01-01", "execution_date": "2027-01-02", "challenger_id": "C", "symbol": "000001", "side": "BUY", "state": "BUY_FILLED"}]
    assert check_event_projection_contract(events, projected)[0]


def _e4_fixture(tmp_path):
    start = date(2027, 1, 1)
    dates = [(start + timedelta(days=index)).isoformat() for index in range(60)]
    epoch_unsigned = {
        "schema_version": "forward_epoch_manifest_v1",
        "epoch_id": "formal-fixture",
        "kind": "FORMAL_BLIND",
        "status": "FROZEN",
        "immutable": True,
        "start": dates[0],
        "release_id": "release-fixture",
        "e4_status": "ACCUMULATING",
    }
    epoch = dict(epoch_unsigned, manifest_sha256=canonical_sha(epoch_unsigned))
    epoch_path = tmp_path / "epoch.json"
    epoch_path.write_text(json.dumps(epoch), encoding="utf-8")
    packages = []
    for index, trade_date in enumerate(dates):
        package = tmp_path / f"package-{index:03d}"
        package.mkdir()
        manifest = {
            "package_status": "SEALED",
            "package_sha256": f"{index + 1:064x}",
            "epoch_id": "formal-fixture",
            "release_id": "release-fixture",
            "signal_date": trade_date,
        }
        (package / "signal_package_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        packages.append(package)
    event_path = tmp_path / "events.jsonl"
    event_path.write_text("\n".join(json.dumps({"round_trip_completed": True}) for _ in range(30)) + "\n", encoding="utf-8")
    nav_path = tmp_path / "nav.json"
    nav_path.write_text(json.dumps({"status": "VERIFIED"}), encoding="utf-8")
    recon_path = tmp_path / "recon.json"
    recon_path.write_text(json.dumps({"reconciliation_errors": 0, "conservation_errors": 0}), encoding="utf-8")
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(json.dumps(dates), encoding="utf-8")
    return epoch_path, packages, event_path, nav_path, recon_path, calendar_path


def test_e4_builder_requires_real_bound_sources_and_complete_round_trips(tmp_path):
    epoch, packages, events, nav, recon, calendar = _e4_fixture(tmp_path)
    package = build_e4_evidence_package(
        epoch,
        release_id="release-fixture",
        signal_packages=packages,
        event_ledger=events,
        nav_snapshots=nav,
        reconciliation=recon,
        sse_calendar=calendar,
    )
    assert package["status"] == "ECONOMIC_PASS"
    assert package["gate"]["contract_status"] == "CONTRACT_VALID"
    assert package["completed_round_trips"] == 30


def test_e4_builder_rejects_missing_unsealed_fake_and_incomplete_sources(tmp_path):
    epoch, packages, events, nav, recon, calendar = _e4_fixture(tmp_path)
    missing = build_e4_evidence_package(
        epoch,
        release_id="release-fixture",
        signal_packages=[tmp_path / "does-not-exist"],
        event_ledger=events,
        nav_snapshots=nav,
        reconciliation=recon,
        sse_calendar=calendar,
    )
    assert missing["status"] == "ACCUMULATING"
    assert missing["gate"]["contract_status"] == "CONTRACT_INVALID"

    unsealed_manifest = packages[0] / "signal_package_manifest.json"
    unsealed = json.loads(unsealed_manifest.read_text(encoding="utf-8"))
    unsealed["package_status"] = "DRAFT"
    unsealed_manifest.write_text(json.dumps(unsealed), encoding="utf-8")
    unsealed_result = build_e4_evidence_package(
        epoch,
        release_id="release-fixture",
        signal_packages=packages,
        event_ledger=events,
        nav_snapshots=nav,
        reconciliation=recon,
        sse_calendar=calendar,
    )
    assert unsealed_result["gate"]["contract_status"] == "CONTRACT_INVALID"

    fake_date = json.loads((packages[1] / "signal_package_manifest.json").read_text(encoding="utf-8"))
    fake_date["signal_date"] = "2099-01-01"
    (packages[1] / "signal_package_manifest.json").write_text(json.dumps(fake_date), encoding="utf-8")
    incomplete_events = tmp_path / "incomplete.jsonl"
    incomplete_events.write_text(json.dumps({"event_type": "SELL_FILLED", "round_trip_completed": False}) + "\n", encoding="utf-8")
    incomplete = build_e4_evidence_package(
        epoch,
        release_id="release-fixture",
        signal_packages=packages,
        event_ledger=incomplete_events,
        nav_snapshots=nav,
        reconciliation=recon,
        sse_calendar=calendar,
    )
    assert incomplete["status"] == "ACCUMULATING"
    assert incomplete["completed_round_trips"] == 0

    bad_epoch = json.loads(epoch.read_text(encoding="utf-8"))
    bad_epoch["manifest_sha256"] = "0" * 64
    epoch.write_text(json.dumps(bad_epoch), encoding="utf-8")
    bad = build_e4_evidence_package(
        epoch,
        release_id="release-fixture",
        signal_packages=packages,
        event_ledger=events,
        nav_snapshots=nav,
        reconciliation=recon,
        sse_calendar=calendar,
    )
    assert bad["status"] == "ACCUMULATING"
    assert bad["gate"]["contract_status"] == "CONTRACT_INVALID"
