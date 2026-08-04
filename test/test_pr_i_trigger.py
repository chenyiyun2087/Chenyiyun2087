from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.research.evaluate_pr_i_trigger import evaluate


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


PIT_RUN_ID = "formal-pit-run-001"
RUN_ID = "formal-test-run-001"
FORMAL_STRATEGIES = (
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
)
# Binding constants (64-hex) shared by C/D/E per verify_pr_i_chain (v5.1.6)
PRC_MANIFEST_SHA = "b" * 64
PRC_FROZEN_BUNDLE = "e" * 64
PRC_ACCEPTANCE_SHA = "a" * 64


def _layer(status: str, **extra: object) -> dict:
    """Build a PR chain layer satisfying the v5.1.6 verify_pr_i_chain contract.

    Every layer requires: schema_version pr_chain_binding_v5_1,
    capital_authority False, fixture_mode False, consistent formal_pit_run_id,
    and a valid content_sha256 self-hash.
    """
    payload: dict[str, object] = {
        "schema_version": "pr_chain_binding_v5_1",
        "status": status,
        "capital_authority": False,
        "fixture_mode": False,
        "formal_pit_run_id": PIT_RUN_ID,
        **extra,
    }
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _technical_sources(tmp_path: Path, *, economics_passed: bool) -> dict[str, Path]:
    pr_b = _layer("PASS")
    pr_c = _layer(
        "PASS",
        formal_run_id=RUN_ID,
        formal_run_manifest_sha256=PRC_MANIFEST_SHA,
        frozen_bundle_sha256=PRC_FROZEN_BUNDLE,
        acceptance_config_sha256=PRC_ACCEPTANCE_SHA,
        # binds B's canonical self-hash (B minus its content_sha256)
        pr_b_file_sha256=hashlib.sha256(
            json.dumps(
                {k: v for k, v in pr_b.items() if k != "content_sha256"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    economics_status = "PASS" if economics_passed else "ECONOMIC_FAILED"
    pr_d = _layer(
        economics_status,
        formal_run_id=RUN_ID,
        pr_c_manifest_sha256=PRC_MANIFEST_SHA,
        technical_evidence_complete=True,
        economic_gates_passed=economics_passed,
    )
    pr_e = _layer(
        "PASS",
        formal_run_id=RUN_ID,
        pr_c_manifest_sha256=PRC_MANIFEST_SHA,
        technical_evidence_complete=True,
        economic_gates_passed=True,
    )
    return {
        "pr_a_equivalence": _write(tmp_path / "a.json", {"status": "PASS"}),
        "pr_b_formal_readiness": _write(tmp_path / "b.json", pr_b),
        "pr_c_formal_run": _write(tmp_path / "c.json", pr_c),
        "pr_d_oos_robustness": _write(tmp_path / "d.json", pr_d),
        "pr_e_execution_capacity": _write(tmp_path / "e.json", pr_e),
    }


def test_pr_i_triggers_only_for_pure_economic_failure(tmp_path: Path):
    result = evaluate(_technical_sources(tmp_path, economics_passed=False))
    assert result["decision"] == "PR_I_TRIGGERED"
    assert result["technical_evidence_complete"] is True
    assert result["economic_failure_only"] is True
    assert result["alpha_modified"] is False


def test_pr_i_does_not_trigger_when_technical_evidence_is_blocked():
    result = evaluate(
        {
            "pr_a_equivalence": Path("/missing/a.json"),
            "pr_b_formal_readiness": Path("/missing/b.json"),
            "pr_c_formal_run": Path("/missing/c.json"),
            "pr_d_oos_robustness": Path("/missing/d.json"),
            "pr_e_execution_capacity": Path("/missing/e.json"),
        }
    )
    assert result["decision"] == "PR_I_NOT_TRIGGERED"
    assert result["technical_evidence_complete"] is False
    assert result["current_allowed_risk_capital_cny"] == 0


def test_pr_i_does_not_trigger_when_all_economic_gates_pass(tmp_path: Path):
    result = evaluate(_technical_sources(tmp_path, economics_passed=True))
    assert result["decision"] == "PR_I_NOT_TRIGGERED"
    assert result["technical_evidence_complete"] is True
    assert result["pr_chain_status"] == "PASS"
    assert result["economic_status"] == "PASS"
    assert result["alpha_redesign_trigger"] == "NOT_TRIGGERED"
