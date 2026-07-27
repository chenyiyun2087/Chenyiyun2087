from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.research.evaluate_pr_i_trigger import evaluate


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _signed(payload: dict, hash_field: str = "evidence_sha256") -> dict:
    """Add canonical self-hash."""
    result = dict(payload)
    result[hash_field] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


RUN_ID = "formal-test-run-001"
FORMAL_STRATEGIES = (
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
)
# Pre-compute PR-C manifest SHA for PR-D/E binding
_prc = {"status": "VERIFIED", "formal_run_id": RUN_ID, "strategy_ids": list(FORMAL_STRATEGIES),
        "dual_ledger_results": [{"strategy": s, "status": "VERIFIED"} for s in FORMAL_STRATEGIES]}
PRC_MANIFEST_SHA = hashlib.sha256(json.dumps(_prc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _technical_sources(tmp_path: Path, *, economics_passed: bool) -> dict[str, Path]:
    return {
        "pr_a_equivalence": _write(tmp_path / "a.json", {"status": "PASS"}),
        "pr_b_formal_readiness": _write(
            tmp_path / "b.json", {"status": "READY_FOR_FORMAL_RUN"}
        ),
        "pr_c_formal_run": _write(
            tmp_path / "c.json",
            _signed({
                "status": "VERIFIED",
                "formal_run_id": RUN_ID,
                "strategy_ids": list(FORMAL_STRATEGIES),
                "dual_ledger_results": [
                    {"strategy": s, "status": "VERIFIED"} for s in FORMAL_STRATEGIES
                ],
            }, hash_field="manifest_sha256"),
        ),
        "pr_d_oos_robustness": _write(
            tmp_path / "d.json",
            _signed({
                "status": "PASS" if economics_passed else "ECONOMIC_FAILED",
                "formal_run_id": RUN_ID,
                "formal_manifest_sha256": PRC_MANIFEST_SHA,
                "technical_evidence_complete": True,
                "economic_gates_passed": economics_passed,
            }),
        ),
        "pr_e_execution_capacity": _write(
            tmp_path / "e.json",
            _signed({
                "status": "PASS",
                "formal_run_id": RUN_ID,
                "formal_manifest_sha256": PRC_MANIFEST_SHA,
                "technical_evidence_complete": True,
                "economic_gates_passed": True,
            }),
        ),
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
    assert "economic_failure_not_established" in result["blockers"]
