#!/usr/bin/env python3
"""PR Chain Binding — enforce formal run identity across PR-B through PR-I.

Every stage must bind the same formal_pit_run_id.  Each stage records the
SHA-256 of its predecessor's output.  PR-I verifies the complete chain.

Chain:  PR-B (Readiness) → PR-C (Immutable Run) → PR-D (OOS) → PR-E (Capacity) → PR-I (Gate)

PR-I requires ALL of PR-B, PR-C, PR-D, PR-E to exist and pass verification.
A missing or blocked layer always produces BLOCKED — there is no
"chain intact where defined" escape hatch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report


def _load_json(path: Path | None) -> dict[str, Any] | None:
    """Load and validate JSON from path. Returns None on any error or None path."""
    if path is None:
        return None
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _verify_self_hash(node: dict[str, Any], hash_field: str = "content_sha256") -> bool:
    """Verify self-hash using the given hash field name.

    Supports multiple hash conventions:
      - content_sha256 (PR bindings, new artifacts)
      - manifest_sha256 (Formal Run manifest)
      - evidence_sha256 (OOS/Capacity reports)
    """
    declared = node.get(hash_field)
    if not isinstance(declared, str) or len(declared) != 64:
        return False
    payload = {k: v for k, v in node.items() if k != hash_field}
    return canonical_sha(payload) == declared


def _check_base_requirements(
    node: dict[str, Any],
    stage: str,
    expected_pit_run_id: str,
) -> list[str]:
    """Base checks common to all PR layers. Returns list of blockers."""
    blockers = []
    if node.get("capital_authority") is not False:
        blockers.append(f"{stage}_capital_authority_not_false")
    if node.get("status") is None:
        blockers.append(f"{stage}_status_missing")
    if node.get("formal_pit_run_id") != expected_pit_run_id:
        blockers.append(f"{stage}_pit_run_id_mismatch")
    if not _verify_self_hash(node):
        blockers.append(f"{stage}_content_sha_invalid")
    if node.get("fixture_mode") is not False:
        blockers.append(f"{stage}_fixture_mode_not_false")
    return blockers


def bind_pr_b(
    *,
    formal_pit_run_id: str,
    package_sha256: str,
    readiness_report_path: Path,
    output_dir: Path,
    release_id: str,
    strategy_set: str,
) -> dict[str, Any]:
    """Create PR-B binding: formal_pit_run_id → package + readiness evidence."""
    if not readiness_report_path.exists():
        return blocked_report("pr_b_binding", "input", "readiness_report_not_found")

    readiness = json.loads(readiness_report_path.read_text(encoding="utf-8"))
    if readiness.get("status") != "PASS":
        return blocked_report("pr_b_binding", "validate", "readiness_not_pass")

    pr_b = {
        "schema_version": "pr_chain_binding_v5_1",
        "stage": "PR_B",
        "status": "PASS",
        "formal_pit_run_id": formal_pit_run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "package_sha256": package_sha256,
        "readiness_evidence_sha256": readiness.get("evidence_sha256", ""),
        "readiness_report_sha256": canonical_sha(readiness),
        "fixture_mode": False,
        "capital_authority": False,
    }
    pr_b["content_sha256"] = canonical_sha(
        {k: v for k, v in pr_b.items() if k != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pr_b_binding.json").write_text(
        json.dumps(pr_b, ensure_ascii=False, indent=2, sort_keys=True))
    return pr_b


def _file_sha(path: Path) -> str:
    """Compute SHA-256 of file bytes."""
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_and_verify_artifact(
    path: Path,
    stage: str,
    expected_status: str | set[str] | None = "PASS",
    hash_field: str = "content_sha256",
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read an artifact file, verify self-hash and status.

    v5.1.2: Supports configurable hash_field for artifacts with different
    self-hash conventions (content_sha256, manifest_sha256, evidence_sha256).

    Returns (payload, blockers).  payload is None on failure.
    """
    blockers: list[str] = []
    if path is None or not path.exists():
        blockers.append(f"{stage}_artifact_not_found:{path}")
        return None, blockers

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        blockers.append(f"{stage}_artifact_unreadable:{type(exc).__name__}")
        return None, blockers

    if not isinstance(payload, dict):
        blockers.append(f"{stage}_artifact_not_object")
        return None, blockers

    # Verify self-hash using the configured field name
    if not _verify_self_hash(payload, hash_field):
        blockers.append(f"{stage}_artifact_{hash_field}_invalid")

    # Verify status
    actual_status = payload.get("status", "")
    if expected_status is not None:
        if isinstance(expected_status, set):
            if actual_status not in expected_status:
                blockers.append(f"{stage}_artifact_status_invalid:{actual_status}")
        elif actual_status != expected_status:
            blockers.append(f"{stage}_artifact_status_not_{expected_status}:{actual_status}")

    return payload, blockers


def bind_pr_c(
    *,
    pr_b_binding_path: Path,
    formal_run_id: str,
    formal_run_manifest_path: Path,
    frozen_bundle_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create PR-C binding: formal run must reference PR-B.

    v5.1.1: Accepts real artifact paths, not bare SHA strings.
    Reads, verifies, and records file SHAs from actual files.
    """
    pr_b, b_blockers = _read_and_verify_artifact(
        pr_b_binding_path, "pr_b_input", "PASS",
    )
    if b_blockers:
        return blocked_report("pr_c_binding", "input", "pr_b_verification_failed",
                              extra={"blockers": b_blockers})

    pr_b_sha = canonical_sha({k: v for k, v in pr_b.items() if k != "content_sha256"})
    pit_run_id = pr_b["formal_pit_run_id"]

    # Read and verify formal run manifest (uses manifest_sha256, status=VERIFIED)
    run_manifest, rm_blockers = _read_and_verify_artifact(
        formal_run_manifest_path, "formal_run_manifest",
        {"VERIFIED", "PASS"},
        hash_field="manifest_sha256",
    )
    if rm_blockers:
        return blocked_report("pr_c_binding", "input", "run_manifest_verification_failed",
                              extra={"blockers": rm_blockers})

    # Verify PIT Run ID consistency — MUST match PR-B's formal_pit_run_id
    manifest_pit_id = run_manifest.get("formal_pit_run_id")
    if not manifest_pit_id:
        rm_blockers.append("run_manifest_formal_pit_run_id_missing")
    elif manifest_pit_id != pit_run_id:
        rm_blockers.append("run_manifest_pit_run_id_mismatch")

    # Verify formal_run_id matches (use formal_run_id from runner, not run_id)
    runner_run_id = run_manifest.get("formal_run_id") or run_manifest.get("run_id")
    if runner_run_id != formal_run_id:
        rm_blockers.append("run_manifest_run_id_mismatch")
    if rm_blockers:
        return blocked_report("pr_c_binding", "input", "run_manifest_checks_failed",
                              extra={"blockers": rm_blockers})

    # Read and verify frozen bundle (uses content_sha256)
    bundle, fb_blockers = _read_and_verify_artifact(
        frozen_bundle_path, "frozen_bundle", "PASS",
    )
    if fb_blockers:
        return blocked_report("pr_c_binding", "input", "frozen_bundle_verification_failed",
                              extra={"blockers": fb_blockers})

    # Compute file SHAs
    run_manifest_file_sha = _file_sha(formal_run_manifest_path)
    frozen_bundle_file_sha = _file_sha(frozen_bundle_path)

    pr_c = {
        "schema_version": "pr_chain_binding_v5_1",
        "stage": "PR_C",
        "status": "PASS",
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": formal_run_id,
        "pr_b_file_sha256": pr_b_sha,
        "pr_b_evidence_sha256": pr_b.get("readiness_evidence_sha256"),
        "formal_run_manifest_sha256": run_manifest_file_sha,
        "formal_run_manifest_path": str(formal_run_manifest_path),
        "frozen_bundle_sha256": frozen_bundle_file_sha,
        "frozen_bundle_path": str(frozen_bundle_path),
        "fixture_mode": False,
        "capital_authority": False,
    }
    pr_c["content_sha256"] = canonical_sha(
        {k: v for k, v in pr_c.items() if k != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pr_c_binding.json").write_text(
        json.dumps(pr_c, ensure_ascii=False, indent=2, sort_keys=True))
    return pr_c


def bind_pr_d(
    *,
    pr_c_binding_path: Path,
    oos_report_path: Path,
    output_dir: Path,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Create PR-D binding: OOS must reference PR-C.

    v5.1.1: Accepts real OOS report path, not bare SHA string.
    Reads, verifies, and records file SHA from actual file.
    """
    pr_c, c_blockers = _read_and_verify_artifact(
        pr_c_binding_path, "pr_c_input", "PASS",
    )
    if c_blockers:
        return blocked_report("pr_d_binding", "input", "pr_c_verification_failed",
                              extra={"blockers": c_blockers})

    valid_statuses = {"PASS", "ECONOMIC_FAILED"}
    oos_report, oos_blockers = _read_and_verify_artifact(
        oos_report_path, "oos_report", valid_statuses,
        hash_field="evidence_sha256",
    )
    if oos_blockers:
        return blocked_report("pr_d_binding", "input", "oos_report_verification_failed",
                              extra={"blockers": oos_blockers})

    # Verify formal_pit_run_id consistency
    pit_run_id = pr_c["formal_pit_run_id"]
    if oos_report.get("formal_pit_run_id") != pit_run_id:
        oos_blockers.append("oos_report_pit_run_id_mismatch")
    if oos_blockers:
        return blocked_report("pr_d_binding", "input", "oos_checks_failed",
                              extra={"blockers": oos_blockers})

    oos_report_file_sha = _file_sha(oos_report_path)

    pr_d = {
        "schema_version": "pr_chain_binding_v5_1",
        "stage": "PR_D",
        "status": oos_report["status"],
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": pr_c.get("formal_run_id"),
        "pr_c_manifest_sha256": pr_c.get("formal_run_manifest_sha256"),
        "oos_manifest_sha256": oos_report_file_sha,
        "oos_report_path": str(oos_report_path),
        "fixture_mode": fixture_mode,
        "capital_authority": False,
    }
    pr_d["content_sha256"] = canonical_sha(
        {k: v for k, v in pr_d.items() if k != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pr_d_binding.json").write_text(
        json.dumps(pr_d, ensure_ascii=False, indent=2, sort_keys=True))
    return pr_d


def bind_pr_e(
    *,
    pr_c_binding_path: Path,
    capacity_report_path: Path,
    output_dir: Path,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Create PR-E binding: Capacity must reference PR-C.

    v5.1.1: Accepts real capacity report path, not bare status string.
    Reads, verifies, and records file SHA from actual file.
    """
    pr_c, c_blockers = _read_and_verify_artifact(
        pr_c_binding_path, "pr_c_input", "PASS",
    )
    if c_blockers:
        return blocked_report("pr_e_binding", "input", "pr_c_verification_failed",
                              extra={"blockers": c_blockers})

    valid_statuses = {"PASS", "ECONOMIC_FAILED"}
    cap_report, cap_blockers = _read_and_verify_artifact(
        capacity_report_path, "capacity_report", valid_statuses,
        hash_field="evidence_sha256",
    )
    if cap_blockers:
        return blocked_report("pr_e_binding", "input", "capacity_report_verification_failed",
                              extra={"blockers": cap_blockers})

    # Verify formal_pit_run_id consistency
    pit_run_id = pr_c["formal_pit_run_id"]
    if cap_report.get("formal_pit_run_id") != pit_run_id:
        cap_blockers.append("capacity_report_pit_run_id_mismatch")
    if cap_blockers:
        return blocked_report("pr_e_binding", "input", "capacity_checks_failed",
                              extra={"blockers": cap_blockers})

    capacity_report_file_sha = _file_sha(capacity_report_path)

    pr_e = {
        "schema_version": "pr_chain_binding_v5_1",
        "stage": "PR_E",
        "status": cap_report["status"],
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": pr_c.get("formal_run_id"),
        "pr_c_manifest_sha256": pr_c.get("formal_run_manifest_sha256"),
        "capacity_manifest_sha256": capacity_report_file_sha,
        "capacity_report_path": str(capacity_report_path),
        "fixture_mode": fixture_mode,
        "capital_authority": False,
    }
    pr_e["content_sha256"] = canonical_sha(
        {k: v for k, v in pr_e.items() if k != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pr_e_binding.json").write_text(
        json.dumps(pr_e, ensure_ascii=False, indent=2, sort_keys=True))
    return pr_e


def verify_pr_i_chain(
    *,
    pr_b_path: Path,
    pr_c_path: Path,
    pr_d_path: Path,
    pr_e_path: Path,
) -> dict[str, Any]:
    """PR-I: Verify the complete chain. ALL layers must exist and be verified.

    Every layer (B/C/D/E) is mandatory.  A missing or blocked layer
    always produces BLOCKED — there is no "chain intact where defined"
    escape hatch.

    Per-layer verification includes:
      - JSON parseable
      - Schema version correct
      - content_sha256 self-hash correct
      - Status semantics correct (B=PASS, C=VERIFIED, D/E∈{PASS,ECONOMIC_FAILED})
      - formal_pit_run_id consistent across all layers
      - formal_run_id consistent across C/D/E
      - Predecessor artifact SHA matches binding
      - fixture_mode is False for all layers
      - capital_authority is False for all layers
    """
    blockers: list[str] = []

    # ── Load all layers ──
    pr_b = _load_json(pr_b_path)
    pr_c = _load_json(pr_c_path)
    pr_d = _load_json(pr_d_path)
    pr_e = _load_json(pr_e_path)

    # ── Check existence ──
    for label, node in [("PR_B", pr_b), ("PR_C", pr_c), ("PR_D", pr_d), ("PR_E", pr_e)]:
        if node is None:
            blockers.append(f"{label.lower()}_missing_or_invalid_json")

    # If any layer is missing, we cannot proceed with cross-checks
    if blockers:
        return _pr_i_report(blockers)

    # ── PR-B verification ──
    pr_b_blockers = _check_base_requirements(pr_b, "pr_b", pr_b["formal_pit_run_id"])
    if pr_b.get("status") != "PASS":
        pr_b_blockers.append("pr_b_status_not_pass")
    if pr_b.get("schema_version") != "pr_chain_binding_v5_1":
        pr_b_blockers.append("pr_b_schema_version_wrong")
    blockers.extend(pr_b_blockers)

    # ── PR-C verification ──
    pr_c_blockers = _check_base_requirements(pr_c, "pr_c", pr_b["formal_pit_run_id"])
    if pr_c.get("status") != "PASS":
        pr_c_blockers.append("pr_c_status_not_pass")
    if pr_c.get("schema_version") != "pr_chain_binding_v5_1":
        pr_c_blockers.append("pr_c_schema_version_wrong")
    if not pr_c.get("formal_run_id"):
        pr_c_blockers.append("pr_c_formal_run_id_missing")
    # PR-B file SHA verification
    pr_b_actual_sha = canonical_sha(
        {k: v for k, v in pr_b.items() if k != "content_sha256"}
    )
    if pr_c.get("pr_b_file_sha256") != pr_b_actual_sha:
        pr_c_blockers.append("pr_b_file_sha_mismatch")
    blockers.extend(pr_c_blockers)

    # ── PR-D verification ──
    VALID_PR_DE_STATUSES = {"PASS", "ECONOMIC_FAILED"}
    pr_d_blockers = _check_base_requirements(pr_d, "pr_d", pr_b["formal_pit_run_id"])
    if pr_d.get("status") not in VALID_PR_DE_STATUSES:
        pr_d_blockers.append("pr_d_status_invalid")
    if pr_d.get("status") == "BLOCKED":
        pr_d_blockers.append("pr_d_status_blocked")
    if pr_d.get("schema_version") != "pr_chain_binding_v5_1":
        pr_d_blockers.append("pr_d_schema_version_wrong")
    # PR-C/PR-D formal_run_id match
    if pr_c.get("formal_run_id") != pr_d.get("formal_run_id"):
        pr_d_blockers.append("run_id_pr_c_pr_d_mismatch")
    # PR-C manifest SHA binding
    pr_c_manifest = pr_c.get("formal_run_manifest_sha256")
    if not pr_c_manifest or not isinstance(pr_c_manifest, str) or len(pr_c_manifest) != 64:
        pr_d_blockers.append("pr_c_manifest_sha_invalid_for_pr_d_binding")
    elif pr_d.get("pr_c_manifest_sha256") != pr_c_manifest:
        pr_d_blockers.append("pr_c_manifest_sha_mismatch_in_pr_d")
    blockers.extend(pr_d_blockers)

    # ── PR-E verification ──
    pr_e_blockers = _check_base_requirements(pr_e, "pr_e", pr_b["formal_pit_run_id"])
    if pr_e.get("status") not in VALID_PR_DE_STATUSES:
        pr_e_blockers.append("pr_e_status_invalid")
    if pr_e.get("status") == "BLOCKED":
        pr_e_blockers.append("pr_e_status_blocked")
    if pr_e.get("schema_version") != "pr_chain_binding_v5_1":
        pr_e_blockers.append("pr_e_schema_version_wrong")
    # PR-C/PR-E formal_run_id match
    if pr_c.get("formal_run_id") != pr_e.get("formal_run_id"):
        pr_e_blockers.append("run_id_pr_c_pr_e_mismatch")
    # PR-C manifest SHA binding
    if pr_c_manifest and pr_e.get("pr_c_manifest_sha256") != pr_c_manifest:
        pr_e_blockers.append("pr_c_manifest_sha_mismatch_in_pr_e")
    blockers.extend(pr_e_blockers)

    # ── PR-C formal_run_id must be non-empty ──
    pr_c_run_id = pr_c.get("formal_run_id")
    if not pr_c_run_id or not isinstance(pr_c_run_id, str):
        blockers.append("pr_c_formal_run_id_empty_or_invalid")

    # ── v5.1.6: TOCTOU full-chain re-validation ──
    # Re-read ALL underlying artifact files and verify SHA + cross-chain identity.
    TOCTOU_CHECKS: list[tuple[str, str | None, str]] = [
        # (binding_source, path_key_in_binding, sha_key_in_binding)
    ]

    # PR-C: formal_run_manifest, frozen_bundle
    for path_key, label, sha_key in [
        ("formal_run_manifest_path", "formal_run_manifest", "formal_run_manifest_sha256"),
        ("frozen_bundle_path", "frozen_bundle", "frozen_bundle_sha256"),
    ]:
        artifact_path_str = pr_c.get(path_key)
        if artifact_path_str:
            ap = Path(artifact_path_str)
            if not ap.exists():
                blockers.append(f"{label}_artifact_missing")
            else:
                current_sha = _file_sha(ap)
                binding_sha = pr_c.get(sha_key, "")
                if binding_sha and current_sha != binding_sha:
                    blockers.append(f"{label}_tampered_after_binding")

    # PR-D: OOS report
    for path_key, label, sha_key in [
        ("oos_report_path", "oos_report", "oos_manifest_sha256"),
    ]:
        artifact_path_str = pr_d.get(path_key)
        if artifact_path_str:
            ap = Path(artifact_path_str)
            if not ap.exists():
                blockers.append(f"{label}_artifact_missing")
            else:
                if _file_sha(ap) != pr_d.get(sha_key, ""):
                    blockers.append(f"{label}_tampered_after_binding")

    # PR-E: Capacity report
    for path_key, label, sha_key in [
        ("capacity_report_path", "capacity_report", "capacity_manifest_sha256"),
    ]:
        artifact_path_str = pr_e.get(path_key)
        if artifact_path_str:
            ap = Path(artifact_path_str)
            if not ap.exists():
                blockers.append(f"{label}_artifact_missing")
            else:
                if _file_sha(ap) != pr_e.get(sha_key, ""):
                    blockers.append(f"{label}_tampered_after_binding")

    # ── v5.1.6: Cross-chain identity consistency ──
    # All layers must agree on formal_pit_run_id and formal_run_id
    pit_id = pr_b["formal_pit_run_id"]
    chain_ids: dict[str, str | None] = {
        "pr_c": pr_c.get("formal_pit_run_id"),
        "pr_d": pr_d.get("formal_pit_run_id"),
        "pr_e": pr_e.get("formal_pit_run_id"),
    }
    for layer, cid in chain_ids.items():
        if cid and cid != pit_id:
            blockers.append(f"cross_chain_pit_id_mismatch:{layer}")

    run_id = pr_c.get("formal_run_id")
    for layer, rid in [("pr_d", pr_d.get("formal_run_id")), ("pr_e", pr_e.get("formal_run_id"))]:
        if rid and rid != run_id:
            blockers.append(f"cross_chain_run_id_mismatch:{layer}")

    return _pr_i_report(blockers)


def _pr_i_report(blockers: list[str]) -> dict[str, Any]:
    """Build a standardized PR-I verification report."""
    unique = sorted(set(blockers))
    status = "PASS" if not unique else "BLOCKED"
    report = {
        "schema_version": "pr_chain_binding_v5_1",
        "stage": "PR_I",
        "status": status,
        "blockers": unique,
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report
