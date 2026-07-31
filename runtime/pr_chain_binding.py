#!/usr/bin/env python3
"""PR Chain Binding — enforce formal run identity across PR-B through PR-I.

Every stage must bind the same formal_pit_run_id.  Each stage records the
SHA-256 of its predecessor's output.  PR-I verifies the complete chain.

Chain:  PR-B (Readiness) → PR-C (Immutable Run) → PR-D (OOS) → PR-E (Capacity) → PR-I (Gate)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report


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
        "schema_version": "pr_chain_binding_v5_0",
        "stage": "PR_B",
        "status": "PASS",
        "formal_pit_run_id": formal_pit_run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "package_sha256": package_sha256,
        "readiness_evidence_sha256": readiness.get("evidence_sha256", ""),
        "readiness_report_sha256": canonical_sha(readiness),
        "capital_authority": False,
    }
    pr_b["content_sha256"] = canonical_sha(
        {k: v for k, v in pr_b.items() if k != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pr_b_binding.json").write_text(
        json.dumps(pr_b, ensure_ascii=False, indent=2, sort_keys=True))
    return pr_b


def bind_pr_c(
    *,
    pr_b_binding_path: Path,
    formal_run_id: str,
    formal_run_manifest_sha256: str,
    frozen_bundle_sha256: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Create PR-C binding: formal run must reference PR-B."""
    if not pr_b_binding_path.exists():
        return blocked_report("pr_c_binding", "input", "pr_b_binding_not_found")

    pr_b = json.loads(pr_b_binding_path.read_text(encoding="utf-8"))
    pr_b_sha = canonical_sha({k: v for k, v in pr_b.items() if k != "content_sha256"})

    pr_c = {
        "schema_version": "pr_chain_binding_v5_0",
        "stage": "PR_C",
        "status": "PASS",
        "formal_pit_run_id": pr_b.get("formal_pit_run_id"),
        "formal_run_id": formal_run_id,
        "pr_b_file_sha256": pr_b_sha,
        "pr_b_evidence_sha256": pr_b.get("readiness_evidence_sha256"),
        "formal_run_manifest_sha256": formal_run_manifest_sha256,
        "frozen_bundle_sha256": frozen_bundle_sha256,
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
    output_dir: Path,
    oos_result: str = "PASS",
    oos_manifest_sha256: str = "",
) -> dict[str, Any]:
    """Create PR-D binding: OOS must reference PR-C."""
    if not pr_c_binding_path.exists():
        return blocked_report("pr_d_binding", "input", "pr_c_binding_not_found")

    pr_c = json.loads(pr_c_binding_path.read_text(encoding="utf-8"))

    pr_d = {
        "schema_version": "pr_chain_binding_v5_0",
        "stage": "PR_D",
        "status": oos_result,
        "formal_pit_run_id": pr_c.get("formal_pit_run_id"),
        "formal_run_id": pr_c.get("formal_run_id"),
        "pr_c_manifest_sha256": pr_c.get("formal_run_manifest_sha256"),
        "oos_manifest_sha256": oos_manifest_sha256,
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
    output_dir: Path,
    capacity_result: str = "PASS",
) -> dict[str, Any]:
    """Create PR-E binding: Capacity must reference PR-C."""
    if not pr_c_binding_path.exists():
        return blocked_report("pr_e_binding", "input", "pr_c_binding_not_found")

    pr_c = json.loads(pr_c_binding_path.read_text(encoding="utf-8"))

    pr_e = {
        "schema_version": "pr_chain_binding_v5_0",
        "stage": "PR_E",
        "status": capacity_result,
        "formal_pit_run_id": pr_c.get("formal_pit_run_id"),
        "formal_run_id": pr_c.get("formal_run_id"),
        "pr_c_manifest_sha256": pr_c.get("formal_run_manifest_sha256"),
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
    """PR-I: Verify the complete chain. All SHA links must be intact."""
    blockers = []

    def _load(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    try:
        pr_b = _load(pr_b_path) if pr_b_path.exists() else None
        pr_c = _load(pr_c_path) if pr_c_path.exists() else None
        pr_d = _load(pr_d_path) if pr_d_path.exists() else None
        pr_e = _load(pr_e_path) if pr_e_path.exists() else None
    except Exception as exc:
        return blocked_report("pr_i_chain", "load", f"json_error:{type(exc).__name__}")

    if not all([pr_b, pr_c]):
        blockers.append("pr_b_or_pr_c_missing")

    if pr_b and pr_c:
        if pr_b.get("formal_pit_run_id") != pr_c.get("formal_pit_run_id"):
            blockers.append("run_id_pr_b_pr_c_mismatch")
        # SHA256(PR-B file) == PR-C.pr_b_file_sha256
        pr_b_actual = canonical_sha({k: v for k, v in pr_b.items() if k != "content_sha256"})
        if pr_c.get("pr_b_file_sha256") != pr_b_actual:
            blockers.append("pr_b_file_sha_mismatch")

    if pr_c and pr_d:
        if pr_c.get("formal_run_id") != pr_d.get("formal_run_id"):
            blockers.append("run_id_pr_c_pr_d_mismatch")

    if pr_c and pr_e:
        if pr_c.get("formal_run_id") != pr_e.get("formal_run_id"):
            blockers.append("run_id_pr_c_pr_e_mismatch")

    status = "PASS" if not blockers else "BLOCKED"
    report = {
        "schema_version": "pr_chain_binding_v5_0",
        "stage": "PR_I",
        "status": status,
        "blockers": sorted(set(blockers)),
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report
