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
from pathlib import Path
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


# ── Formal Evidence Registry ──────────────────────────────────────────────────

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "exports" / "formal_evidence_registry" / "active_formal_run.json"


def load_active_formal_registry(
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any] | None:
    """Read and validate the active formal evidence registry.

    Returns None if the registry is missing, unparseable, or invalid.
    """
    try:
        reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(reg, dict):
        return None
    if reg.get("schema_version") != "formal_evidence_registry_v1":
        return None
    if reg.get("capital_authority") is not False:
        return None
    return reg


def update_active_formal_registry(
    payload: dict[str, Any],
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> None:
    """Atomically update the formal evidence registry.

    Raises ValueError on schema violations.
    """
    required = {
        "schema_version": "formal_evidence_registry_v1",
        "capital_authority": False,
    }
    for key, expected in required.items():
        if payload.get(key) != expected:
            raise ValueError(
                f"Registry update rejected: {key}={payload.get(key)}, expected={expected}"
            )

    reg_path = Path(registry_path)
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = reg_path.with_suffix(".tmp")
    reg_path_txt = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp.write_text(reg_path_txt, encoding="utf-8")
    tmp.replace(reg_path)


# ── Orphan Run Detection ──────────────────────────────────────────────────────

FORMAL_EVIDENCE_RUNS_ROOT = Path(__file__).resolve().parents[1] / "exports" / "formal_evidence_runs"


def detect_orphaned_runs(
    runs_root: Path = FORMAL_EVIDENCE_RUNS_ROOT,
) -> list[dict[str, Any]]:
    """Scan formal evidence runs for orphan directories.

    An orphan is a directory that has a seal_manifest.json but no
    run_manifest.json — meaning the run was sealed on an exception path
    before completing.
    """
    if not runs_root.is_dir():
        return []

    orphans = []
    for child in sorted(runs_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        has_seal = (child / "seal_manifest.json").exists()
        has_manifest = (child / "run_manifest.json").exists()
        if has_seal and not has_manifest:
            orphans.append({
                "run_dir": str(child.name),
                "full_path": str(child),
                "has_seal": True,
                "has_manifest": False,
            })
    return orphans


def quarantine_orphaned_run(
    run_name: str,
    runs_root: Path = FORMAL_EVIDENCE_RUNS_ROOT,
    quarantine_dir_name: str = ".quarantine",
) -> dict[str, Any]:
    """Quarantine an orphaned run directory.

    Moves the directory to a .quarantine/ subdirectory and writes
    a ORPHANED tombstone recording the original tree SHA.
    """
    import hashlib
    import shutil
    from datetime import datetime as dt, timezone as tz

    run_dir = runs_root / run_name
    if not run_dir.is_dir():
        return {"status": "NOT_FOUND", "run_name": run_name}

    # Compute original tree SHA from seal if available
    original_tree_sha = ""
    seal_path = run_dir / "seal_manifest.json"
    if seal_path.exists():
        try:
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
            original_tree_sha = seal.get("artifact_tree_sha256", "")
        except (OSError, json.JSONDecodeError):
            pass

    quarantine_root = runs_root / quarantine_dir_name
    quarantine_root.mkdir(parents=True, exist_ok=True)

    tombstone = {
        "schema_version": "evidence_tombstone_v5_1",
        "run_name": run_name,
        "status": "QUARANTINED",
        "evidence_level": "E0",
        "capital_authority": False,
        "original_tree_sha256": original_tree_sha,
        "quarantine_reason": "ORPHANED",
        "quarantined_at": dt.now(tz.utc).isoformat(),
    }
    tombstone["content_sha256"] = canonical_sha(
        {k: v for k, v in tombstone.items() if k != "content_sha256"}
    )

    # Make writable before moving (sealed dirs are read-only)
    def _make_writable(p: Path) -> None:
        import stat
        if p.is_dir():
            p.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            for child in p.rglob("*"):
                if child.is_dir():
                    child.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                elif child.is_file():
                    child.chmod(stat.S_IRUSR | stat.S_IWUSR)
    _make_writable(run_dir)

    # Move to quarantine
    target = quarantine_root / run_name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.move(str(run_dir), str(target))

    # Write tombstone at original location
    tombstone_path = runs_root / f"{run_name}_QUARANTINE_TOMBSTONE.json"
    tombstone_path.write_text(
        json.dumps(tombstone, ensure_ascii=False, indent=2, sort_keys=True))

    return tombstone
