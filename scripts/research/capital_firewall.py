"""Evidence governance and capital firewall controls for Alpha validation.

The controls in this module classify evidence and explain promotion readiness.
They do not enable a broker route or create a capital approval.
"""

from __future__ import annotations

from typing import Any

from runtime.acceptance_config import canonical_sha


EVIDENCE_LEVELS = {
    "E0": 0,  # missing or blocked
    "E1": 1,  # code/test/replay evidence
    "E2": 2,  # deterministic simulation or paper evidence
    "E3": 3,  # release-scoped historical real-data evidence
    "E4": 4,  # release-scoped live/Shadow trading evidence
}

CODE_EVIDENCE_GATES = {
    "research_replay",
    "replay_diff",
    "correctness_synthetic_suite",
    "failure_injection",
}
SIMULATION_EVIDENCE_GATES = {
    "execution_simulation",
}
LIVE_EVIDENCE_GATES = {
    "economic_shadow",
}
GOVERNANCE_GATES = {
    "manual_approval",
}
ALPHA_EVIDENCE_GATES = {
    "core_history",
    "benchmark_excess",
    "alpha_attribution",
    "factor_ic",
    "alpha_proof_guard",
    "factor_compute_lineage",
    "walk_forward",
}
TRADING_EVIDENCE_GATES = {
    "execution_cost_stress",
    "economic_shadow",
}


def _strength_for_gate(gate: str, status: str) -> str:
    if status != "PASS":
        return "E0"
    if gate in LIVE_EVIDENCE_GATES:
        return "E4"
    if gate in SIMULATION_EVIDENCE_GATES:
        return "E2"
    if gate in CODE_EVIDENCE_GATES or gate in GOVERNANCE_GATES:
        return "E1"
    return "E3"


def build_evidence_strength_report(
    matrix: dict[str, Any],
) -> dict[str, Any]:
    """Classify every evidence contract row on a non-substitutable E0-E4 scale."""
    rows: list[dict[str, Any]] = []
    for row in matrix.get("rows", []):
        gate = str(row.get("gate") or "unknown")
        status = str(row.get("status") or "BLOCKED")
        level = _strength_for_gate(gate, status)
        rows.append(
            {
                "gate": gate,
                "status": status,
                "evidence_level": level,
                "evidence_level_rank": EVIDENCE_LEVELS[level],
                "evidence_sha256": row.get("evidence_sha256"),
                "release_id": row.get("release_id"),
                "timestamp": row.get("timestamp"),
                "gap_id": row.get("gap_id"),
                "impact_scope": row.get("impact_scope"),
                "capital_minimum_level": (
                    "E4" if gate in LIVE_EVIDENCE_GATES else "E3"
                ),
                "capital_qualified": (
                    EVIDENCE_LEVELS[level]
                    >= (4 if gate in LIVE_EVIDENCE_GATES else 3)
                ),
            }
        )
    level_counts = {
        level: sum(row["evidence_level"] == level for row in rows)
        for level in EVIDENCE_LEVELS
    }
    blockers = [
        f"evidence_strength_insufficient:{row['gate']}:{row['evidence_level']}"
        for row in rows
        if row["gate"] in ALPHA_EVIDENCE_GATES | TRADING_EVIDENCE_GATES
        and not row["capital_qualified"]
    ]
    return {
        "schema_version": "alpha_v4_0_evidence_strength_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "capital_authority": False,
        "levels": {
            "E0": "missing_or_blocked",
            "E1": "code_test_or_replay",
            "E2": "simulation_or_paper",
            "E3": "release_scoped_historical_real_data",
            "E4": "release_scoped_live_or_shadow_trading",
        },
        "levels_are_non_substitutable": True,
        "level_counts": level_counts,
        "blockers": blockers,
        "rows": rows,
    }


def build_capital_firewall(
    promotion: dict[str, Any],
    strength: dict[str, Any],
) -> dict[str, Any]:
    """Apply a fail-closed decision firewall to every capital path."""
    gates = {
        str(row.get("gate")): str(row.get("status"))
        for row in promotion.get("gates", [])
    }
    levels = {
        str(row.get("gate")): str(row.get("evidence_level"))
        for row in strength.get("rows", [])
    }

    alpha_missing = sorted(
        gate
        for gate in ALPHA_EVIDENCE_GATES
        if gates.get(gate) != "PASS"
        or EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0) < 3
    )
    trading_missing = sorted(
        gate
        for gate in TRADING_EVIDENCE_GATES
        if gates.get(gate) != "PASS"
        or EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0)
        < (4 if gate == "economic_shadow" else 3)
    )
    requirements = [
        {
            "requirement": "investment_readiness",
            "status": (
                "PASS"
                if str(promotion.get("status")) == "PASS"
                and float(promotion.get("allowed_capital_cny") or 0) > 0
                else "BLOCKED"
            ),
        },
        {
            "requirement": "alpha_evidence_e3",
            "status": "PASS" if not alpha_missing else "BLOCKED",
            "missing_gates": alpha_missing,
        },
        {
            "requirement": "trading_evidence_e3",
            "status": "PASS" if not trading_missing else "BLOCKED",
            "missing_gates": trading_missing,
        },
        {
            "requirement": "shadow_e4",
            "status": (
                "PASS"
                if gates.get("economic_shadow") == "PASS"
                and levels.get("economic_shadow") == "E4"
                else "BLOCKED"
            ),
        },
        {
            "requirement": "manual_release_approval",
            "status": (
                "PASS" if gates.get("manual_approval") == "PASS" else "BLOCKED"
            ),
        },
    ]
    eligible = all(row["status"] == "PASS" for row in requirements)
    requested_capital = float(promotion.get("allowed_capital_cny") or 0)
    return {
        "schema_version": "alpha_v4_0_capital_decision_firewall_v1",
        "status": "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION"
        if eligible
        else "BLOCKED",
        "headline_warning": (
            "SIMULATION ONLY | NO CAPITAL AUTHORITY | NO BROKER ACTION"
        ),
        "simulation_result": "PASS" if eligible else "BLOCKED",
        "capital_authority": eligible,
        "broker_permission": False,
        "canary_enabled": False,
        "requested_capital_cny": requested_capital,
        "effective_allowed_capital_cny": requested_capital if eligible else 0.0,
        "requirements": requirements,
        "blocking_requirements": [
            row["requirement"] for row in requirements if row["status"] != "PASS"
        ],
        "interpretation": (
            "A PASS means only that a separate release-bound human authorization "
            "may be considered. This report cannot enable a broker route."
        ),
    }


def build_evidence_promotion_workflow(
    matrix: dict[str, Any],
    issue_tracker: dict[str, Any],
) -> dict[str, Any]:
    """Expose deterministic evidence lifecycle stages without auto-promotion."""
    issues = {
        str(row.get("gap_id")): row for row in issue_tracker.get("issues", [])
    }
    rows: list[dict[str, Any]] = []
    for contract in matrix.get("rows", []):
        gap_id = contract.get("gap_id")
        issue = issues.get(str(gap_id))
        if contract.get("status") == "PASS":
            stage = "VALIDATED_PENDING_PROMOTION_REVIEW"
        elif issue and issue.get("verification_status") == "PASS":
            stage = "VALIDATED_PENDING_PROMOTION_REVIEW"
        elif issue and issue.get("verification_run"):
            stage = "VALIDATION_FAILED"
        elif issue and issue.get("fix_commit"):
            stage = "EVIDENCE_ADDED"
        else:
            stage = "BLOCKED"
        rows.append(
            {
                "gate": contract.get("gate"),
                "gap_id": gap_id,
                "stage": stage,
                "allowed_transitions": {
                    "BLOCKED": ["EVIDENCE_ADDED"],
                    "EVIDENCE_ADDED": ["VALIDATION_FAILED", "VALIDATED_PENDING_PROMOTION_REVIEW"],
                    "VALIDATION_FAILED": ["EVIDENCE_ADDED"],
                    "VALIDATED_PENDING_PROMOTION_REVIEW": ["PASS", "BLOCKED"],
                    "PASS": [],
                }[stage],
                "automatic_promotion": False,
                "requires_release_bound_review": True,
            }
        )
    return {
        "schema_version": "alpha_v4_0_evidence_promotion_workflow_v1",
        "status": (
            "REVIEW_REQUIRED"
            if rows and all(row["stage"] == "VALIDATED_PENDING_PROMOTION_REVIEW" for row in rows)
            else "BLOCKED"
        ),
        "capital_authority": False,
        "automatic_promotion": False,
        "workflow": (
            "BLOCKED -> EVIDENCE_ADDED -> VALIDATION -> "
            "PROMOTION_REVIEW -> PASS"
        ),
        "rows": rows,
    }


def build_alpha_claim_registry(
    promotion: dict[str, Any],
    strength: dict[str, Any],
    firewall: dict[str, Any],
) -> dict[str, Any]:
    """Allow only claims supported by the current evidence strength."""
    gates = {
        str(row.get("gate")): str(row.get("status"))
        for row in promotion.get("gates", [])
    }
    levels = {
        str(row.get("gate")): str(row.get("evidence_level"))
        for row in strength.get("rows", [])
    }
    definitions = [
        ("RESEARCH_STRATEGY", True, []),
        (
            "REGRESSION_ALPHA",
            all(
                gates.get(gate) == "PASS"
                and EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0) >= 3
                for gate in {"benchmark_excess", "alpha_attribution", "alpha_proof_guard"}
            ),
            ["benchmark_excess", "alpha_attribution", "alpha_proof_guard"],
        ),
        (
            "STOCK_SELECTION_ALPHA",
            all(
                gates.get(gate) == "PASS"
                and EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0) >= 3
                for gate in {"alpha_attribution", "factor_ic"}
            ),
            ["alpha_attribution", "factor_ic"],
        ),
        (
            "TRADABLE_ALPHA",
            str(firewall.get("status"))
            == "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION",
            ["execution_cost_stress", "economic_shadow"],
        ),
        (
            "LIVE_ALPHA",
            bool(firewall.get("capital_authority")),
            ["economic_shadow", "manual_approval"],
        ),
        (
            "CAPITAL_READY",
            bool(firewall.get("capital_authority"))
            and float(firewall.get("effective_allowed_capital_cny") or 0) > 0,
            ["economic_shadow", "manual_approval"],
        ),
    ]
    claims = [
        {
            "claim": name,
            "status": "ALLOWED" if allowed else "DENIED",
            "required_gates": required,
            "claim_sha256": canonical_sha(
                {
                    "claim": name,
                    "status": "ALLOWED" if allowed else "DENIED",
                    "required_gates": required,
                }
            ),
        }
        for name, allowed, required in definitions
    ]
    return {
        "schema_version": "alpha_v4_0_alpha_claim_registry_v1",
        "status": "PASS",
        "capital_authority": False,
        "allowed_claims": [
            row["claim"] for row in claims if row["status"] == "ALLOWED"
        ],
        "denied_claims": [
            row["claim"] for row in claims if row["status"] == "DENIED"
        ],
        "claims": claims,
        "interpretation": (
            "A denied claim must not appear as a factual conclusion in release "
            "reports, investment memos, or capital requests."
        ),
    }
