#!/usr/bin/env python3
"""Formal Readiness v4 — comprehensive pre-flight validation before formal run.

Validates:
  Identity: formal_pit_run_id, release_id, strategy_set, git SHA,
            acceptance_profile SHA, artifact_tree SHA, fixture_mode
  Data: evidence levels, adapter/blocks/scores/package stages PASS
  Time: available_at ≤ signal_time, T+1 execution, timezone-aware
  Coverage: trade calendar ≥98%, universe→market 100%, lifecycle 100%,
            financial/industry ≥threshold, score coverage ≥threshold
  Integrity: seal verification, artifact tree, required objects,
             no symlinks, no .building paths
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.artifact_seal import verify_seal
from runtime.fail_closed import blocked_report


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(
    *,
    formal_pit_run_id: str,
    package_dir: Path,
    release_id: str,
    strategy_set: str,
    pit_run_dir: Path | None = None,
    fixture_mode: bool = False,
    git_commit_sha: str = "",
    acceptance_profile_sha: str = "",
) -> dict[str, Any]:
    """Run all readiness checks. Returns READY_FOR_FORMAL_RUN or BLOCKED.

    v5.1.6: Delegates business logic to formal_readiness_preflight.evaluate_package()
    (the single authoritative Readiness engine).  v4 adds Seal verification and
    identity checks that the preflight doesn't cover.
    """
    blockers: list[str] = []

    # ═══════════════════════════════════════════════════════════════════
    # Identity + Seal (v4's unique value-add)
    # ═══════════════════════════════════════════════════════════════════
    if fixture_mode:
        blockers.append("fixture_mode_must_be_false_for_formal_run")

    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.exists():
        blockers.append("package_manifest_not_found")
    else:
        pkg = json.loads(manifest_path.read_text(encoding="utf-8"))
        if pkg.get("formal_pit_run_id") != formal_pit_run_id:
            blockers.append("run_id_mismatch")
        if pkg.get("status") != "PASS":
            blockers.append("package_not_pass")
        if pkg.get("release_id") != release_id:
            blockers.append("release_id_mismatch")
        if pkg.get("strategy_set") != strategy_set:
            blockers.append("strategy_set_mismatch")
        pkg_raw = {k: v for k, v in pkg.items() if k not in ("content_sha256", "built_at")}
        if pkg.get("content_sha256") != canonical_sha(pkg_raw):
            blockers.append("package_manifest_content_sha_invalid")

    # Package Seal
    pkg_seal = verify_seal(package_dir)
    if pkg_seal["status"] != "VERIFIED":
        blockers.append(f"package_seal_not_verified:{pkg_seal['status']}")

    # PIT Run Seal (mandatory in v5.1.6)
    if pit_run_dir is None:
        blockers.append("pit_run_dir_required_for_seal_verification")
    else:
        pit_seal = verify_seal(pit_run_dir)
        if pit_seal["status"] != "VERIFIED":
            blockers.append(f"pit_run_seal_not_verified:{pit_seal['status']}")

    # No symlinks, no .building
    for p in package_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(package_dir)}")
    if ".building" in str(package_dir):
        blockers.append(".building_in_path")

    # ═══════════════════════════════════════════════════════════════════
    # Delegate business logic to the single authoritative preflight engine
    # ═══════════════════════════════════════════════════════════════════
    preflight_blockers: list[str] = []
    preflight_status = "NOT_RUN"
    try:
        from scripts.research.formal_readiness_preflight import evaluate_package
        import yaml
        readiness_config_path = PROJECT_ROOT / "config" / "formal_readiness.yaml"
        readiness_config = yaml.safe_load(readiness_config_path.read_text(encoding="utf-8")) or {}
        preflight_result = evaluate_package(package_dir, readiness_config)
        preflight_status = preflight_result.get("status", "UNKNOWN")
        if preflight_status != "READY_FOR_FORMAL_RUN":
            preflight_blockers = (
                preflight_result.get("blocking_checks")
                or preflight_result.get("blockers")
                or [preflight_status]
            )
    except Exception as exc:
        preflight_blockers.append(f"preflight_evaluation_error:{type(exc).__name__}")

    all_blockers = sorted(set(blockers + preflight_blockers))
    # READY_FOR_FORMAL_RUN is the single success token consumed by the
    # immutable Runner.  Keep legacy_status for downstream diagnostic readers,
    # but never emit a second success spelling from the formal admission path.
    status = "READY_FOR_FORMAL_RUN" if not all_blockers else "BLOCKED"

    report = {
        "schema_version": "formal_readiness_v5_1_6",
        "status": status,
        "legacy_status": "PASS" if status == "READY_FOR_FORMAL_RUN" else status,
        "ready_for_formal_run": status == "READY_FOR_FORMAL_RUN",
        "formal_pit_run_id": formal_pit_run_id,
        "package": str(package_dir),
        "package_id": pkg.get("package_id", "") if manifest_path.exists() else "",
        "release_id": release_id,
        "strategy_set": strategy_set,
        "git_commit_sha": git_commit_sha,
        "acceptance_profile_sha": acceptance_profile_sha,
        "admission_id": "",
        "pr_b_file_sha256": "",
        "pr_b_sha256": "",
        "blockers": all_blockers,
        "preflight_status": preflight_status,
        "preflight_evidence_sha256": preflight_result.get("evidence_sha256")
        if "preflight_result" in locals()
        else "",
        "evidence_sha256": preflight_result.get("evidence_sha256")
        if "preflight_result" in locals()
        else "",
        "fixture_mode": fixture_mode,
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="formal_pit_run_id")
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--acceptance-sha", default="")
    args = parser.parse_args()
    result = validate(
        formal_pit_run_id=args.run_id,
        package_dir=args.package_dir,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        git_commit_sha=args.git_sha,
        acceptance_profile_sha=args.acceptance_sha,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
