#!/usr/bin/env python3
"""Independent strict qualifier for historical PIT E3 evidence.

The adapter and semantic audit can only claim E1/contract-valid evidence.  A
separate invocation of this module must bind the exact adapter manifest,
semantic-audit report, strict profile, nine frozen family files, and the
provider transaction identity before it can emit ``qualified_evidence_level``
``E3``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import (
    get_contract_sha256,
    get_lineage_columns,
    get_required_columns,
    get_source_families,
    validate_frame_schema,
    validate_lineage_frame,
)

DEFAULT_STRICT_PROFILE = PROJECT_ROOT / "config" / "validation_profiles" / "formal_e3_strict.yaml"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str, blockers: list[str]) -> dict[str, Any]:
    if not path.exists():
        blockers.append(f"{label}_missing")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blockers.append(f"{label}_unreadable:{type(exc).__name__}")
        return {}
    if not isinstance(payload, dict):
        blockers.append(f"{label}_not_object")
        return {}
    return payload


def _self_hash_valid(payload: dict[str, Any], label: str, blockers: list[str]) -> bool:
    declared = str(payload.get("content_sha256") or "")
    if not declared:
        blockers.append(f"{label}_content_sha256_missing")
        return False
    actual = canonical_sha({key: value for key, value in payload.items() if key != "content_sha256"})
    if declared != actual:
        blockers.append(f"{label}_content_sha256_invalid")
        return False
    return True


def _sources(manifest: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merged.update(manifest.get("sources") or {})
    merged.update(manifest.get("families") or {})
    return merged


def _identity(manifest: dict[str, Any]) -> dict[str, Any]:
    nested = manifest.get("snapshot_identity")
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return {
        key: manifest.get(key)
        for key in (
            "provider_snapshot_token",
            "snapshot_token",
            "transaction_started_at",
            "transaction_finished_at",
            "transaction_isolation",
            "consistent_snapshot",
            "server_identity",
            "gtid_provenance",
            "binlog_provenance",
            "gtid_or_binlog_position",
        )
        if manifest.get(key) is not None
    }


def _provider_token(identity: dict[str, Any]) -> str:
    return str(
        identity.get("provider_snapshot_token")
        or identity.get("snapshot_token")
        or ""
    ).strip()


def _token_is_provenance_only(token: str, identity: dict[str, Any]) -> bool:
    if not token:
        return True
    normalized = token.strip().lower()
    if normalized.startswith("gtid:") or normalized.startswith("binlog:"):
        return True
    gtid = identity.get("gtid_provenance") or {}
    if isinstance(gtid, dict):
        values = {str(value).strip() for value in gtid.values() if value not in (None, "")}
        if token in values:
            return True
    binlog = identity.get("binlog_provenance") or {}
    if isinstance(binlog, dict):
        values = {str(value).strip() for value in binlog.values() if value not in (None, "")}
        if token in values:
            return True
        file_name = str(binlog.get("file") or "")
        position = str(binlog.get("position") or "")
        if token in {f"{file_name}:{position}", file_name}:
            return True
    return False


def qualify_pit_e3(
    *,
    snapshots_dir: Path,
    adapter_manifest_path: Path,
    audit_report_path: Path,
    strict_profile_path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Qualify a fully-bound PIT run, or return a fail-closed E0 report."""
    blockers: list[str] = []
    families = list(get_source_families())
    strict_profile_path = strict_profile_path or DEFAULT_STRICT_PROFILE
    adapter = _read_json(adapter_manifest_path, "adapter_manifest", blockers)
    audit = _read_json(audit_report_path, "semantic_audit", blockers)
    _self_hash_valid(adapter, "adapter_manifest", blockers)
    _self_hash_valid(audit, "semantic_audit", blockers)

    profile: dict[str, Any] = {}
    if not strict_profile_path.exists():
        blockers.append("strict_profile_missing")
    else:
        try:
            profile = yaml.safe_load(strict_profile_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            blockers.append(f"strict_profile_unreadable:{type(exc).__name__}")
        if not isinstance(profile, dict):
            blockers.append("strict_profile_not_object")
    if profile:
        if profile.get("evidence_level") != "E3":
            blockers.append("strict_profile_not_e3")
        for field in (
            "require_point_in_time_consistency",
            "require_independent_consistent_snapshot",
            "forbid_e0_derived_fields",
            "require_benchmark_index_data",
        ):
            if profile.get(field) is not True:
                blockers.append(f"strict_profile_requirement_missing:{field}")

    if adapter.get("status") not in {"QUALIFIED", "PASS"}:
        blockers.append("adapter_manifest_not_qualified")
    if adapter.get("evidence_origin") != "HISTORICAL_REAL":
        blockers.append("adapter_manifest_not_historical_real")
    if str(adapter.get("claimed_evidence_level") or adapter.get("historical_evidence_level") or "") != "E1":
        blockers.append("adapter_claimed_level_not_e1")
    if audit.get("status") != "PASS":
        blockers.append("semantic_audit_not_pass")
    if audit.get("qualified_evidence_level") not in (None, ""):
        blockers.append("semantic_audit_must_never_qualify_e3")
    if str(audit.get("claimed_evidence_level") or "") not in {"E1", "E0"}:
        blockers.append("semantic_audit_claimed_level_invalid")
    if audit.get("semantic_contract_sha256") != get_contract_sha256():
        blockers.append("semantic_audit_contract_sha_mismatch")
    if adapter.get("field_definition_hash") != get_contract_sha256():
        blockers.append("adapter_contract_sha_mismatch")
    release_id = str(adapter.get("release_id") or "").strip()
    if not release_id:
        blockers.append("release_id_missing")
    if str(adapter.get("decision_contract_id") or "") != "ashare_t2130_t1_v1":
        blockers.append("decision_contract_mismatch")

    identity = _identity(adapter)
    token = _provider_token(identity)
    if not token:
        blockers.append("provider_snapshot_token_missing")
    elif _token_is_provenance_only(token, identity):
        blockers.append("provider_snapshot_token_is_gtid_or_binlog_provenance")
    if not identity.get("transaction_started_at"):
        blockers.append("transaction_started_at_missing")
    if not identity.get("transaction_finished_at"):
        blockers.append("transaction_finished_at_missing")
    if identity.get("transaction_isolation") != "REPEATABLE READ":
        blockers.append("transaction_isolation_not_repeatable_read")
    if identity.get("consistent_snapshot") is not True:
        blockers.append("consistent_snapshot_required")
    if not identity.get("server_identity"):
        blockers.append("server_identity_missing")
    if not identity.get("gtid_provenance"):
        blockers.append("gtid_provenance_missing")
    if not identity.get("binlog_provenance"):
        blockers.append("binlog_provenance_missing")

    sources = _sources(adapter)
    audit_details = audit.get("audit_details") or {}
    file_sha256: dict[str, str] = {}
    parameter_sha256: dict[str, str] = {}
    query_sha256: dict[str, str] = {}
    for family in families:
        info = sources.get(family) or {}
        if not info:
            blockers.append(f"source_family_missing:{family}")
            continue
        path = snapshots_dir / f"{family}.parquet"
        if not path.exists():
            blockers.append(f"snapshot_missing:{family}")
            continue
        actual_sha = _file_sha(path)
        declared_sha = str(info.get("content_sha256") or info.get("sha256") or "")
        if not declared_sha:
            blockers.append(f"source_sha_missing:{family}")
        elif declared_sha != actual_sha:
            blockers.append(f"source_sha_mismatch:{family}")
        detail = audit_details.get(family) or {}
        if detail.get("file_sha256") != actual_sha:
            blockers.append(f"audit_sha_mismatch:{family}")
        if detail.get("blockers"):
            blockers.append(f"audit_family_blocked:{family}")
        file_sha256[family] = actual_sha
        query_sha256[family] = str(info.get("query_sha256") or "")
        parameter_sha256[family] = str(info.get("parameter_sha256") or "")
        if not query_sha256[family]:
            blockers.append(f"query_sha_missing:{family}")
        if not parameter_sha256[family]:
            blockers.append(f"parameter_sha_missing:{family}")
        try:
            frame = pd.read_parquet(path)
            blockers.extend(validate_frame_schema(frame, family))
            blockers.extend(validate_lineage_frame(frame, family, strict=True))
            required = get_required_columns(family)
            if not required.issubset(frame.columns):
                blockers.append(f"schema_missing:{family}")
        except Exception as exc:
            blockers.append(f"snapshot_unreadable:{family}:{type(exc).__name__}")

    # Ensure the audit itself retained detail for every canonical family.
    for family in families:
        if family not in audit_details:
            blockers.append(f"audit_family_detail_missing:{family}")

    status = "PASS" if not blockers else "BLOCKED"
    result: dict[str, Any] = {
        "schema_version": "pit_e3_qualifier_v1",
        "component": "independent_pit_qualifier",
        "release_id": release_id or None,
        "decision_contract_id": "ashare_t2130_t1_v1",
        "status": status,
        "claimed_evidence_level": "E1" if status == "PASS" else "E0",
        "qualified_evidence_level": "E3" if status == "PASS" else None,
        "data_status": "DATA_E3_QUALIFIED" if status == "PASS" else "BLOCKED_DATA",
        "canonical_families": families,
        "lineage_columns": list(get_lineage_columns()),
        "strict_profile_path": str(strict_profile_path),
        "strict_profile_sha256": _file_sha(strict_profile_path) if strict_profile_path.exists() else None,
        "semantic_contract_sha256": get_contract_sha256(),
        "adapter_manifest_path": str(adapter_manifest_path),
        "source_manifest_sha256": _file_sha(adapter_manifest_path) if adapter_manifest_path.exists() else None,
        "audit_report_path": str(audit_report_path),
        "audit_sha256": _file_sha(audit_report_path) if audit_report_path.exists() else None,
        "snapshot_identity": identity,
        "file_sha256": file_sha256,
        "query_sha256": query_sha256,
        "parameter_sha256": parameter_sha256,
        "blockers": sorted(set(blockers)),
        "capital_authority": False,
    }
    result["content_sha256"] = canonical_sha(
        {key: value for key, value in result.items() if key != "content_sha256"}
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots-dir", type=Path, required=True)
    parser.add_argument("--adapter-manifest", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--strict-profile", type=Path, default=DEFAULT_STRICT_PROFILE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = qualify_pit_e3(
        snapshots_dir=args.snapshots_dir,
        adapter_manifest_path=args.adapter_manifest,
        audit_report_path=args.audit_report,
        strict_profile_path=args.strict_profile,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
