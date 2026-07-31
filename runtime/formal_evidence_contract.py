#!/usr/bin/env python3
"""Formal Evidence Contract — unified evidence identity, levels, and run identity.

This module defines the single source of truth for evidence semantics in
Chenyiyun2087 Formal Evidence Backbone v5.0.  All formal components MUST
use this module for evidence classification; no component may define its
own evidence levels or bypass these contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


# ── Canonical Hash ───────────────────────────────────────────────────────────

def canonical_sha(payload: object) -> str:
    """Deterministic SHA-256 of JSON-canonical representation.

    Keys are sorted, separators are compact, encoding is UTF-8.
    Same input always produces the same hash.
    """
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


# ── Evidence Levels ──────────────────────────────────────────────────────────

class DataEvidence(str, Enum):
    """Point-in-Time data evidence levels."""
    E0 = "no_qualified_data"
    E1 = "frozen_snapshot_and_lineage"
    E2 = "pit_semantic_and_coverage_audit_passed"
    E3 = "formal_panel_deterministically_replayable"


class AlphaEvidence(str, Enum):
    """Economic alpha evidence levels."""
    E0 = "no_valid_economic_evidence"
    E1 = "factor_ic_and_long_short_diagnostics"
    E2 = "cost_adjusted_walkforward_oos_passed"
    E3 = "full_history_stability_attribution_independent_replay"


class ExecutionEvidence(str, Enum):
    """Execution and trading evidence levels."""
    E0 = "no_execution_evidence"
    E1 = "simulation_ledger_and_trading_rules_passed"
    E2 = "stress_capacity_and_dual_ledger_passed"
    E3 = "live_shadow_or_canary_evidence"


# ── Evidence Status ──────────────────────────────────────────────────────────

@dataclass
class EvidenceStatus:
    """Decomposed evidence status.  Capital authority is NEVER auto-derived."""
    data_evidence: DataEvidence = DataEvidence.E0
    alpha_evidence: AlphaEvidence = AlphaEvidence.E0
    execution_evidence: ExecutionEvidence = ExecutionEvidence.E0
    capital_authority: bool = False  # Capital Firewall + human approval only

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def content_sha(self) -> str:
        return canonical_sha(self.as_dict())


# ── Run Identity ─────────────────────────────────────────────────────────────

def compute_formal_pit_run_id(
    *,
    release_id: str,
    strategy_set: str,
    git_commit_sha: str,
    dependency_lock_sha: str,
    acceptance_profile_sha: str,
    adapter_config_sha: str,
    query_bundle_sha: str,
    field_semantics_sha: str,
    database_snapshot_identity: str,
) -> str:
    """Content-addressed formal PIT run ID.

    Same inputs → same run ID.  Any input change → different run ID.
    Run IDs are never reusable.
    """
    payload = {
        "release_id": release_id,
        "strategy_set": strategy_set,
        "git_commit_sha": git_commit_sha,
        "dependency_lock_sha": dependency_lock_sha,
        "acceptance_profile_sha": acceptance_profile_sha,
        "adapter_config_sha": adapter_config_sha,
        "query_bundle_sha": query_bundle_sha,
        "field_semantics_sha": field_semantics_sha,
        "database_snapshot_identity": database_snapshot_identity,
    }
    return canonical_sha(payload)


# ── Blocked Report ───────────────────────────────────────────────────────────

def blocked_report(
    component: str,
    stage: str,
    error_code: str,
    *,
    exception: Exception | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Standardized BLOCKED report.  Never throws.

    All components must use this helper for consistent fail-closed reporting.
    """
    report: dict[str, Any] = {
        "schema_version": "formal_evidence_backbone_v5_0",
        "status": "BLOCKED",
        "component": component,
        "stage": stage,
        "error_code": error_code,
        "capital_authority": False,
        "evidence_status": EvidenceStatus().as_dict(),
    }
    if exception is not None:
        report["exception_type"] = type(exception).__name__
        report["exception_message"] = str(exception)
    if extra:
        report["extra"] = extra
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report


# ── Version Manifest ─────────────────────────────────────────────────────────

@dataclass
class VersionManifest:
    """Six-dimensional version identity."""
    strategy_version: str
    data_contract_version: str
    field_semantic_version: str
    factor_formula_version: str
    execution_model_version: str
    acceptance_profile_version: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionManifest:
        return cls(
            strategy_version=str(data["strategy_version"]),
            data_contract_version=str(data["data_contract_version"]),
            field_semantic_version=str(data["field_semantic_version"]),
            factor_formula_version=str(data["factor_formula_version"]),
            execution_model_version=str(data["execution_model_version"]),
            acceptance_profile_version=str(data["acceptance_profile_version"]),
        )

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    def content_sha(self) -> str:
        return canonical_sha(self.as_dict())
