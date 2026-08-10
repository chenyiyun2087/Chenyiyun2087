"""Wave 4 research contracts (pure local fixtures, no database/exports)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from scripts.research.advanced_statistical_validation import (
    benjamini_hochberg,
    block_bootstrap,
    cscv_pbo,
    deflated_sharpe_ratio,
    nested_walk_forward_splits,
    permutation_test,
)
from scripts.research.experiment_registry import ExperimentRegistry, RegistryError, make_record
from scripts.research.orthogonal_factor_attribution import (
    diagnostic_single_factor_combinations,
    orthogonal_factor_attribution,
)
from scripts.research.research_preregistration import (
    CONSUMED_DEVELOPMENT_SAMPLE,
    PreregistrationDriftError,
    canonical_definition_hash,
    load_preregistration,
    preregistration_hash,
    sha256_file,
    validate_formal_evidence,
    validate_preregistration,
    verify_preregistration_source_bindings,
    verify_preregistration_immutable,
)
from scripts.research.run_pure_alpha_challenge import run_pure_alpha_challenge
from scripts.research.run_smart_beta_research import run_smart_beta_research


ROOT = Path(__file__).resolve().parents[1]


def _cards():
    return [
        ROOT / "config/alpha_challengers/smart_beta_v1.yaml",
        ROOT / "config/alpha_challengers/pure_alpha_residual_v1.yaml",
    ]


def test_wave4_strategy_identity_and_consumed_sample():
    ids = set()
    for path in _cards():
        card = load_preregistration(path)
        ids.add(card["strategy_id"])
        assert validate_preregistration(card)["status"] == "PASS"
        assert card["sample_policy"]["status"] == CONSUMED_DEVELOPMENT_SAMPLE
        assert card["sample_policy"]["end"] == "2026-08-09"
        assert card["sample_policy"]["independent_oos"] is False
        assert card["capital_cny"] == 0.0
        assert card["promotion_status"] == "BLOCKED"
        assert card["availability"]["signal_time"] == "T_21:30_AFTER_DATA_COMPLETE"
        assert card["availability"]["decision_cutoff"] == "21:30:00+08:00"
        assert card["availability"]["execution_time"] == "T+1_SSE_RAW_OPEN"
        for component in ("commission_rate", "min_commission_cny", "stamp_duty_rate", "transfer_fee_rate", "open_auction_slippage_bps", "gap_bps", "spread_bps", "adv_impact_bps", "missed_fill_bps"):
            assert component in card["cost_model"]
    assert ids == {"smart_beta_v1_t2130", "pure_alpha_residual_v1_t2130"}


def test_preregistration_hash_is_immutable():
    card = load_preregistration(_cards()[0])
    clone = json.loads(json.dumps(card))
    assert verify_preregistration_immutable(card, clone)["immutable"]
    clone["top_n"] = 21
    with pytest.raises(PreregistrationDriftError):
        verify_preregistration_immutable(card, clone)
    assert len(preregistration_hash(card)) == 64


def test_preregistration_source_bindings_are_real_and_drift_blocks(tmp_path):
    source = _cards()[0]
    card = load_preregistration(source)
    binding = verify_preregistration_source_bindings(card, card_path=source)
    assert binding["status"] == "PASS"
    assert binding["code_hash"] == sha256_file(card["code_path"])
    assert binding["config_hash"] == canonical_definition_hash(card["definition_path"])

    changed_code = tmp_path / "runner.py"
    changed_code.write_bytes(Path(card["code_path"]).read_bytes() + b"\n# drift\n")
    changed_card = dict(card, code_path=str(changed_code))
    with pytest.raises(PreregistrationDriftError, match="code_hash_drift"):
        verify_preregistration_source_bindings(changed_card, card_path=source)

    changed_definition = tmp_path / "definition.yaml"
    changed_definition.write_text(
        Path(card["definition_path"]).read_text(encoding="utf-8").replace("top_n: 20", "top_n: 21"),
        encoding="utf-8",
    )
    changed_card = dict(card, definition_path=str(changed_definition))
    with pytest.raises(PreregistrationDriftError, match="config_hash_drift"):
        verify_preregistration_source_bindings(changed_card, card_path=source)


def test_nested_walk_forward_no_leakage_and_blocked_short_sample():
    splits = nested_walk_forward_splits(n_samples=160, n_splits=3, train_size=60, validation_size=20, test_size=20, purge=2, embargo=3)
    assert splits["status"] == "PASS" and len(splits) == 3
    for fold in splits:
        assert set(fold["train"]).isdisjoint(fold["validation"])
        assert set(fold["validation"]).isdisjoint(fold["test"])
        assert max(fold["train"]) < min(fold["validation"])
        assert max(fold["validation"]) < min(fold["test"])
    blocked = nested_walk_forward_splits(n_samples=10, n_splits=3, train_size=8, validation_size=8, test_size=8, strict=False)
    assert blocked["status"] == "BLOCKED"


def test_bootstrap_dsr_cscv_fdr_and_permutation_contracts():
    values = np.sin(np.arange(50) / 4.0) / 100
    first = block_bootstrap(values, n_bootstrap=5, block_size=4, seed=7)
    second = block_bootstrap(values, n_bootstrap=5, block_size=4, seed=7)
    assert first["status"] == "PASS" and first["samples"] == second["samples"]
    assert block_bootstrap([1.0, 2.0], n_bootstrap=3, strict=False)["status"] == "BLOCKED"
    assert deflated_sharpe_ratio(values, strict=False)["status"] == "PASS"
    assert cscv_pbo([values, values * 0.9], n_groups=5, strict=False)["status"] == "PASS"
    fdr = benjamini_hochberg([0.001, 0.03, 0.9])
    assert fdr["p_adjusted"][0] <= fdr["p_adjusted"][2]
    perm = permutation_test(values, n_permutations=11)
    assert perm["n_permutations"] == 11 and perm["seed"] == 20260810
    assert inspect.signature(permutation_test).parameters["n_permutations"].default == 9999


def test_registry_append_only_duplicate_and_consumed_oos_rejection(tmp_path):
    path = tmp_path / "registry.jsonl"
    registry = ExperimentRegistry(path)
    row = make_record(
        hypothesis_id="H-W4-REG-1", strategy_id="smart_beta_v1",
        dataset_status=CONSUMED_DEVELOPMENT_SAMPLE, params={"x": 1}, tests=["DSR"],
    )
    registry.append(row)
    with pytest.raises(RegistryError, match="duplicate_hypothesis"):
        registry.append(row)
    with pytest.raises(RegistryError, match="consumed_sample"):
        registry.append({**row, "hypothesis_id": "H-W4-REG-2", "independent_oos": True})
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_orthogonal_attribution_closes_and_single_factor_is_diagnostic():
    rng = np.random.default_rng(1)
    factors = {"f1": rng.normal(size=60), "f2": rng.normal(size=60)}
    values = 0.01 + 0.4 * factors["f1"] - 0.2 * factors["f2"]
    result = orthogonal_factor_attribution(values, factors)
    assert result["status"] == "PASS"
    assert result["closure_error"] <= 1e-10
    diagnostic = diagnostic_single_factor_combinations(values, factors)
    assert diagnostic["status"] == "DIAGNOSTIC_ONLY" and not diagnostic["formal"]


def test_runners_block_without_formal_epoch_and_zero_capital(tmp_path):
    smart = run_smart_beta_research(output=tmp_path / "smart.json")
    pure = run_pure_alpha_challenge()
    assert smart["status"] == "BLOCKED_FORWARD_EVIDENCE"
    assert pure["status"] == "BLOCKED_FORWARD_EVIDENCE"
    assert smart["capital_cny"] == pure["capital_cny"] == 0.0
    assert "E3" not in smart.get("conclusion", "")
    assert smart["data_status"] == "UNBOUND"


def test_runner_never_promotes_bare_e3_bool_and_requires_bound_formal_evidence(tmp_path):
    epoch_path = tmp_path / "forward_epochs.yaml"
    epoch_path.write_text(
        """schema_version: forward_epochs_v1\nactive_epoch_id: formal-fixture\nformal_epoch_id: formal-fixture\nepochs:\n  - epoch_id: formal-fixture\n    kind: FORMAL_BLIND\n    status: ACTIVE\n    start: '2026-08-10'\n""",
        encoding="utf-8",
    )
    bare = run_smart_beta_research(
        forward_epochs_path=epoch_path,
        e3_available=True,
        data_status="E3",
    )
    assert bare["status"] == "OBSERVE"
    assert bare["evidence_level"] == "E0"
    assert bare["data_status"] == "UNBOUND"
    pit = {
        "component": "pit_factor_panel",
        "status": "DATA_E3_QUALIFIED",
        "qualified_evidence_level": "E3",
        "content_sha256": "a" * 64,
    }
    forward = {
        "epoch_id": "formal-fixture",
        "start": "2026-08-10",
        "evidence_sha256": "b" * 64,
        "returns_dates": ["2026-08-10", "2026-08-11"],
    }
    bound = run_smart_beta_research(
        forward_epochs_path=epoch_path,
        e3_available=True,
        data_status="E3",
        pit_qualifier=pit,
        forward_evidence=forward,
    )
    assert bound["status"] == "OBSERVE"
    assert bound["evidence_level"] == "E0"
    assert bound["evidence_binding"]["status"] == "BLOCKED"
    assert "formal_evidence_paths_required" in bound["evidence_binding"]["reason"]
    bad = validate_formal_evidence(
        pit_qualifier=pit,
        forward_evidence={**forward, "returns_dates": ["2026-08-09"]},
        formal_epoch={"epoch_id": "formal-fixture", "start": "2026-08-10"},
    )
    assert bad["status"] == "BLOCKED" and bad["evidence_level"] == "E0"


def test_wave4_docs_and_manifest_boundaries():
    overview = (ROOT / "docs/00_project_overview/WAVE4_RESEARCH_EVIDENCE_PLATFORM.md").read_text(encoding="utf-8")
    task = (ROOT / "docs/tasks/2026-08-10_Wave4_Alpha_Smart_Beta研究与证据平台.md").read_text(encoding="utf-8")
    manifest = yaml.safe_load((ROOT / "config/experiments/wave4_research.yaml").read_text(encoding="utf-8"))
    for text in (overview, task):
        assert "CONSUMED_DEVELOPMENT_SAMPLE" in text
        assert "0 CNY" in text
        assert "FORMAL_BLIND" in text
    assert manifest["execution_and_risk"]["initial_capital_cny"] == 0.0
    assert manifest["registry"]["append_only"] is True
