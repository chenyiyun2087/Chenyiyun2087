"""Canonical resolved-evidence registry.

The resolved registry is the only machine-readable source that may claim a
gate is resolved.  Legacy unified/seal/release/active indexes are accepted
only as migration inputs and compatibility views; they are never consulted to
grant a PASS in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from runtime.formal_status_semantics import (
    GateStatus,
    GateEconomicStatus,
    ArtifactStatus,
    ContractStatus,
    make_gate_status,
    resolve_gate_status,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESOLVED_REGISTRY_PATH = PROJECT_ROOT / "config" / "formal_resolved_registry.yaml"
SCHEMA_VERSION = "formal_resolved_registry_v1"


@dataclass(frozen=True)
class ResolvedEvidenceEntry:
    strategy_id: str
    gate: str
    release_id: str
    release_sha256: str
    seal_sha256: str
    epoch_id: str
    evidence_sha256: str
    gate_status: GateStatus
    notes: tuple[str, ...] = ()

    @property
    def resolved_status(self) -> str:
        return resolve_gate_status(self.gate_status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "gate": self.gate,
            "release_id": self.release_id,
            "release_sha256": self.release_sha256,
            "seal_sha256": self.seal_sha256,
            "epoch_id": self.epoch_id,
            "evidence_sha256": self.evidence_sha256,
            "gate_status": self.gate_status.to_dict(),
            "resolved_status": self.resolved_status,
            "notes": list(self.notes),
        }


def _read_payload(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": []}
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(payload, Mapping):
        raise ValueError("formal_resolved_registry_not_mapping")
    return payload


def _entry(raw: Mapping[str, Any]) -> ResolvedEvidenceEntry:
    status_raw = raw.get("gate_status") or raw.get("status") or {}
    if not isinstance(status_raw, Mapping):
        raise ValueError("resolved_entry_gate_status_not_mapping")
    return ResolvedEvidenceEntry(
        strategy_id=str(raw.get("strategy_id") or ""),
        gate=str(raw.get("gate") or raw.get("gate_id") or ""),
        release_id=str(raw.get("release_id") or ""),
        release_sha256=str(raw.get("release_sha256") or ""),
        seal_sha256=str(raw.get("seal_sha256") or ""),
        epoch_id=str(raw.get("epoch_id") or ""),
        evidence_sha256=str(raw.get("evidence_sha256") or ""),
        gate_status=GateStatus.from_dict(status_raw),
        notes=tuple(str(value) for value in (raw.get("notes") or [])),
    )


def load_formal_resolved_registry(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the canonical registry; fail closed on bad schema."""

    source = Path(path or DEFAULT_RESOLVED_REGISTRY_PATH)
    payload = _read_payload(source)
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError("formal_resolved_registry_schema_invalid")
    rows = payload.get("entries") or []
    if not isinstance(rows, list):
        raise ValueError("formal_resolved_registry_entries_invalid")
    entries: list[ResolvedEvidenceEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("formal_resolved_registry_entry_invalid")
        item = _entry(raw)
        key = (item.strategy_id, item.gate)
        if not item.strategy_id or not item.gate or key in seen:
            raise ValueError(f"formal_resolved_registry_duplicate_or_missing:{key}")
        seen.add(key)
        entries.append(item)
    return {
        "schema_version": SCHEMA_VERSION,
        "entries": entries,
        "source_path": source,
    }


def resolve_registry_entry(
    entry: ResolvedEvidenceEntry | Mapping[str, Any],
    *,
    expected_release_id: str | None = None,
    expected_release_sha256: str | None = None,
    expected_seal_sha256: str | None = None,
    expected_epoch_id: str | None = None,
    expected_evidence_sha256: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Resolve one entry only after all provenance bindings agree."""

    item = entry if isinstance(entry, ResolvedEvidenceEntry) else _entry(entry)
    reasons: list[str] = []
    for label, expected, actual in (
        ("release_id", expected_release_id, item.release_id),
        ("release_sha256", expected_release_sha256, item.release_sha256),
        ("seal_sha256", expected_seal_sha256, item.seal_sha256),
        ("epoch_id", expected_epoch_id, item.epoch_id),
        ("evidence_sha256", expected_evidence_sha256, item.evidence_sha256),
    ):
        if expected is not None and str(expected) != str(actual):
            reasons.append(f"{label}_mismatch")
        if not actual:
            reasons.append(f"{label}_missing")
    if item.resolved_status != "PASS":
        reasons.append("gate_not_resolved_pass")
    return not reasons, tuple(dict.fromkeys(reasons))


def migrate_legacy_entry(raw: Mapping[str, Any], *, release_sha256: str = "", seal_sha256: str = "", epoch_id: str = "", evidence_sha256: str = "") -> dict[str, Any]:
    """Convert a legacy unified/seal/index record to a blocked gate view.

    Migration intentionally does not treat a legacy economic label or a file
    path as economic PASS; a fresh contract/economic evaluation must populate
    those dimensions in the canonical registry.
    """

    old_status = raw.get("status") if isinstance(raw.get("status"), Mapping) else {}
    status = make_gate_status(
        artifact_status=ArtifactStatus.ARTIFACT_PRESENT if raw.get("manifest_path") or raw.get("artifact_path") else ArtifactStatus.MISSING,
        contract_status=ContractStatus.NOT_EVALUATED,
        economic_status=GateEconomicStatus.NOT_EVALUATED,
        reasons=("MIGRATED_LEGACY_VIEW_REQUIRES_REEVALUATION",),
    )
    return {
        "strategy_id": str(raw.get("strategy_id") or ""),
        "gate": str(raw.get("gate") or raw.get("cell") or "legacy"),
        "release_id": str(raw.get("release_id") or ""),
        "release_sha256": release_sha256,
        "seal_sha256": seal_sha256,
        "epoch_id": epoch_id,
        "evidence_sha256": evidence_sha256,
        "gate_status": status.to_dict(),
        "legacy_status": dict(old_status),
        "notes": ["legacy registry is migration input/read-only compatibility view"],
    }


# Concise aliases used by command-line/reporting integrations.
load_resolved_registry = load_formal_resolved_registry
resolve_entry = resolve_registry_entry


__all__ = [
    "SCHEMA_VERSION", "DEFAULT_RESOLVED_REGISTRY_PATH", "ResolvedEvidenceEntry",
    "load_formal_resolved_registry", "resolve_registry_entry", "migrate_legacy_entry",
]
