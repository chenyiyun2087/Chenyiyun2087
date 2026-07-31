#!/usr/bin/env python3
"""Formal Admission Pipeline — single entry point for Package + Readiness + PR-B.

v5.1.3: This is the ONLY path that connects a verified PIT Run to a formal
Package, Readiness assessment, and PR-B binding.  Manual steps are forbidden.

Pipeline:
  Stage A1: Verify PIT Run Seal
  Stage A2: Build Formal Package
  Stage A3: Verify Package Seal
  Stage A4: Formal Readiness
  Stage A5: Write readiness_report.json
  Stage A6: PR-B Binding
  Stage A7: Update Registry (candidate status)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.artifact_seal import verify_seal
from runtime.fail_closed import blocked_report
from runtime.pr_chain_binding import bind_pr_b

FORMAL_PACKAGES_ROOT = PROJECT_ROOT / "exports" / "formal_packages"
FORMAL_PIT_RUNS_ROOT = PROJECT_ROOT / "exports" / "formal_pit_runs"


def run_formal_admission(
    *,
    pit_run_id: str,
    release_id: str,
    strategy_set: str = "champion_v1_2b",
    initial_account_cny: float = 500_000.0,
) -> dict[str, Any]:
    """Execute the complete admission pipeline. Returns admission manifest."""
    pit_run_dir = FORMAL_PIT_RUNS_ROOT / pit_run_id
    if not pit_run_dir.is_dir():
        return blocked_report("admission", "input", "pit_run_dir_not_found",
                              extra={"pit_run_dir": str(pit_run_dir)})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A1: Verify PIT Run Seal
    # ═══════════════════════════════════════════════════════════════════════
    pit_seal = verify_seal(pit_run_dir)
    if pit_seal["status"] != "VERIFIED":
        return blocked_report("admission", "seal",
                              f"pit_run_seal_not_verified:{pit_seal['status']}",
                              extra={"seal_result": pit_seal})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A2: Build Formal Package
    # ═══════════════════════════════════════════════════════════════════════
    from scripts.research.build_formal_package import build_formal_package
    try:
        package_result = build_formal_package(
            formal_pit_run_id=pit_run_id,
            pit_run_dir=pit_run_dir,
            release_id=release_id,
            strategy_set=strategy_set,
            initial_account_cny=initial_account_cny,
        )
    except Exception as exc:
        return blocked_report("admission", "package",
                              f"package_exception:{type(exc).__name__}",
                              exception=exc)

    if package_result.get("status") != "PASS":
        return blocked_report("admission", "package",
                              "package_not_pass",
                              extra={"blockers": package_result.get("blockers", [])})

    package_id = package_result["package_id"]
    package_dir = FORMAL_PACKAGES_ROOT / package_id

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A3: Verify Package Seal
    # ═══════════════════════════════════════════════════════════════════════
    pkg_seal = verify_seal(package_dir)
    if pkg_seal["status"] != "VERIFIED":
        return blocked_report("admission", "package_seal",
                              f"package_seal_not_verified:{pkg_seal['status']}",
                              extra={"seal_result": pkg_seal})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A4: Formal Readiness
    # ═══════════════════════════════════════════════════════════════════════
    from scripts.research.formal_readiness_v4 import validate as readiness_validate
    try:
        readiness_result = readiness_validate(
            formal_pit_run_id=pit_run_id,
            package_dir=package_dir,
            release_id=release_id,
            strategy_set=strategy_set,
            pit_run_dir=pit_run_dir,
        )
    except Exception as exc:
        return blocked_report("admission", "readiness",
                              f"readiness_exception:{type(exc).__name__}",
                              exception=exc)

    if readiness_result.get("status") != "PASS":
        return blocked_report("admission", "readiness",
                              "readiness_not_pass",
                              extra={"blockers": readiness_result.get("blockers", [])})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A5: Write readiness_report.json
    # ═══════════════════════════════════════════════════════════════════════
    readiness_report_path = package_dir / "readiness_report.json"
    readiness_report_path.write_text(
        json.dumps(readiness_result, ensure_ascii=False, indent=2, sort_keys=True))

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A6: PR-B Binding
    # ═══════════════════════════════════════════════════════════════════════
    import hashlib
    package_sha = hashlib.sha256(
        (package_dir / "package_manifest.json").read_bytes()
    ).hexdigest()

    pr_b_dir = package_dir / "pr_b"
    try:
        pr_b_result = bind_pr_b(
            formal_pit_run_id=pit_run_id,
            package_sha256=package_sha,
            readiness_report_path=readiness_report_path,
            output_dir=pr_b_dir,
            release_id=release_id,
            strategy_set=strategy_set,
        )
    except Exception as exc:
        return blocked_report("admission", "pr_b",
                              f"pr_b_exception:{type(exc).__name__}",
                              exception=exc)

    if pr_b_result.get("status") != "PASS":
        return blocked_report("admission", "pr_b",
                              "pr_b_binding_not_pass",
                              extra={"blockers": pr_b_result.get("blockers", [])})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage A7: Update Registry (candidate status)
    # ═══════════════════════════════════════════════════════════════════════
    from runtime.formal_evidence_contract import update_active_formal_registry
    registry_payload = {
        "schema_version": "formal_evidence_registry_v1",
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": None,
        "pr_a_path": None,
        "pr_b_path": str(pr_b_dir.relative_to(PROJECT_ROOT) / "pr_b_binding.json"),
        "pr_c_path": None,
        "pr_d_path": None,
        "pr_e_path": None,
        "pr_i_path": None,
        "seal_manifest_sha256": pit_seal.get("artifact_tree_sha256", ""),
        "capital_authority": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": package_result.get("git_commit_sha", ""),
    }
    try:
        update_active_formal_registry(registry_payload)
    except Exception as reg_exc:
        # Write external activation report — do not modify sealed artifacts
        reg_dir = PROJECT_ROOT / "exports" / "formal_evidence_registry"
        reg_dir.mkdir(parents=True, exist_ok=True)
        activation = {
            "schema_version": "activation_report_v5_1_3",
            "stage": "admission_pr_b",
            "formal_pit_run_id": pit_run_id,
            "package_id": package_id,
            "status": "ACTIVATION_FAILED",
            "error": f"{type(reg_exc).__name__}: {reg_exc}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (reg_dir / f"admission_activation_failed_{pit_run_id[:16]}.json").write_text(
            json.dumps(activation, ensure_ascii=False, indent=2, sort_keys=True))

    return {
        "schema_version": "formal_admission_v5_1_3",
        "status": "PASS",
        "formal_pit_run_id": pit_run_id,
        "package_id": package_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "stages": {
            "pit_seal": pit_seal["status"],
            "package": package_result["status"],
            "package_seal": pkg_seal["status"],
            "readiness": readiness_result["status"],
            "pr_b": pr_b_result["status"],
        },
        "pr_b_path": str(pr_b_dir.relative_to(PROJECT_ROOT) / "pr_b_binding.json"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "capital_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-run-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--initial-capital", type=float, default=500_000.0)
    args = parser.parse_args()
    result = run_formal_admission(
        pit_run_id=args.pit_run_id,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        initial_account_cny=args.initial_capital,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
