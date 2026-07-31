#!/usr/bin/env python3
"""Formal Package Builder — constructs immutable package from a verified PIT Run.

v5.1.2: The Formal Package is a separate immutable artifact, built ONLY from
a published, Seal-VERIFIED PIT Run.  It eliminates the circular dependency
where Package required run_manifest.json and seal_manifest.json that hadn't
been created yet.

Input:  exports/formal_pit_runs/<pit_run_id>/  (Seal VERIFIED)
Output: exports/formal_packages/<package_id>/

The package contains all inputs needed by PR-C (Immutable Formal Runner):
  trade_calendar, market/prices, tradable_universe, adjustment_factors,
  corporate_actions, security_lifecycle, financial_PIT, industry_PIT,
  factor_panel, formal_scores, initial_account, source_manifest,
  score_manifest, pit_run_manifest, seal_manifest

Builder constraints:
  - Only accept inputs from a verified-Seal PIT Run (verify_seal mandatory)
  - All five Snapshot families are MANDATORY (not optional)
  - No symlinks, no .building paths, no database access
  - No _internal_call escape hatch — Seal verification is always required
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.artifact_seal import seal_directory, verify_seal
from runtime.fail_closed import blocked_report

FORMAL_PACKAGES_ROOT = PROJECT_ROOT / "exports" / "formal_packages"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file_safe(src: Path, dst: Path) -> dict[str, str]:
    """Copy a file, recording its SHA. Raises on symlinks."""
    if src.is_symlink() or dst.is_symlink():
        raise OSError(f"symlink forbidden: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"path": str(dst.relative_to(dst.parent)), "sha256": _file_sha(dst)}


def compute_package_id(formal_pit_run_id: str, strategy_set: str, release_id: str) -> str:
    """Content-addressed package ID."""
    return hashlib.sha256(
        json.dumps({
            "formal_pit_run_id": formal_pit_run_id,
            "strategy_set": strategy_set,
            "release_id": release_id,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_formal_package(
    *,
    formal_pit_run_id: str,
    pit_run_dir: Path,
    release_id: str,
    strategy_set: str,
    initial_account_cny: float = 500_000.0,
) -> dict[str, Any]:
    """Build a complete formal package from a verified PIT Run directory.

    The PIT Run MUST have a VERIFIED seal.  No _internal_call escape hatch.
    """
    blockers: list[str] = []

    # ── Verify PIT Run Seal ──
    if not pit_run_dir.is_dir():
        return blocked_report("formal_package", "input", "pit_run_dir_not_found")

    seal_result = verify_seal(pit_run_dir)
    if seal_result["status"] != "VERIFIED":
        blockers.append(f"pit_run_seal_not_verified:{seal_result['status']}")
        if seal_result.get("reason"):
            blockers.append(f"seal_reason:{seal_result['reason']}")

    # Verify pit_run_manifest
    pit_manifest_path = pit_run_dir / "pit_run_manifest.json"
    if not pit_manifest_path.exists():
        blockers.append("pit_run_manifest_not_found")
    else:
        pit_manifest = json.loads(pit_manifest_path.read_text(encoding="utf-8"))
        if pit_manifest.get("formal_pit_run_id") != formal_pit_run_id:
            blockers.append("pit_run_id_mismatch")
        if pit_manifest.get("status") not in ("PASS", "PASS_NOT_ACTIVATED"):
            blockers.append(f"pit_run_status_not_pass:{pit_manifest.get('status')}")

    if ".building" in str(pit_run_dir):
        blockers.append("pit_run_dir_is_building")

    # Check for symlinks
    for p in pit_run_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(pit_run_dir)}")

    if blockers:
        return blocked_report("formal_package", "preflight",
                              "preflight_failed", extra={"blockers": blockers})

    # ── Derive package_id ──
    package_id = compute_package_id(formal_pit_run_id, strategy_set, release_id)
    package_dir = FORMAL_PACKAGES_ROOT / package_id
    building_dir = FORMAL_PACKAGES_ROOT / f".building_pkg_{package_id[:16]}"

    if package_dir.exists() or building_dir.exists():
        shutil.rmtree(building_dir, ignore_errors=True)
        return blocked_report("formal_package", "preflight",
                              "package_id_already_exists",
                              extra={"package_id": package_id})

    building_dir.mkdir(parents=True, exist_ok=True)
    required_objects: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    # ── Required entries (all mandatory for formal package) ──
    ENTRIES = {
        # Core evidence
        "scores.parquet": pit_run_dir / "scores" / "formal_scores.parquet",
        "score_manifest.json": pit_run_dir / "scores" / "score_manifest.json",
        "factor_panel.parquet": pit_run_dir / "builder" / "factor_panel_daily.parquet",
        "source_manifest.json": pit_run_dir / "adapter" / "pit_source_manifest.json",
        "adapter_report.json": pit_run_dir / "adapter" / "pit_adapter_report.json",
        "builder_report.json": pit_run_dir / "builder" / "builder_report.json",
        # PIT Run identity
        "pit_run_manifest.json": pit_run_dir / "pit_run_manifest.json",
        "seal_manifest.json": pit_run_dir / "seal_manifest.json",
        # Snapshots — ALL mandatory (v5.1.2)
        "market.parquet": pit_run_dir / "adapter" / "snapshots" / "market.parquet",
        "universe.parquet": pit_run_dir / "adapter" / "snapshots" / "universe.parquet",
        "financial.parquet": pit_run_dir / "adapter" / "snapshots" / "financial.parquet",
        "industry.parquet": pit_run_dir / "adapter" / "snapshots" / "industry.parquet",
        "adjustment.parquet": pit_run_dir / "adapter" / "snapshots" / "adjustment.parquet",
    }

    for rel_name, src_path in sorted(ENTRIES.items()):
        if not src_path.exists():
            missing.append(rel_name)
            continue
        try:
            info = _copy_file_safe(src_path, building_dir / rel_name)
            required_objects[rel_name] = info
        except OSError as exc:
            blockers.append(f"copy_failed:{rel_name}:{exc}")

    if missing:
        blockers.append(f"missing_objects:{','.join(missing)}")

    # ── Initial account (identity bound to PIT Run sealed_at) ──
    seal_manifest = json.loads((pit_run_dir / "seal_manifest.json").read_text(encoding="utf-8"))
    account = {
        "currency": "CNY",
        "initial_capital": initial_account_cny,
        "generated_at": seal_manifest.get("sealed_at", datetime.now(timezone.utc).isoformat()),
        "pit_run_sealed_at": seal_manifest.get("sealed_at"),
    }
    acc_path = building_dir / "initial_account.json"
    acc_path.write_text(json.dumps(account, ensure_ascii=False, indent=2))
    required_objects["initial_account.json"] = {
        "path": "initial_account.json", "sha256": _file_sha(acc_path),
    }

    if blockers:
        return blocked_report("formal_package", "build",
                              "build_failed", extra={"blockers": blockers})

    # ── Package manifest ──
    artifact_tree_sha = canonical_sha(
        {name: {"sha256": info["sha256"]}
         for name, info in sorted(required_objects.items())}
    )

    manifest = {
        "schema_version": "formal_package_v5_1_2",
        "status": "PASS",
        "package_id": package_id,
        "formal_pit_run_id": formal_pit_run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "required_objects": required_objects,
        "artifact_tree_sha256": artifact_tree_sha,
        "file_count": len(required_objects),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "capital_authority": False,
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (building_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    # ── Seal the package ──
    seal_directory(building_dir, run_id=package_id, git_commit_sha="")

    # ── Atomic publish ──
    building_dir.rename(package_dir)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-run-id", required=True, help="formal_pit_run_id")
    parser.add_argument("--pit-run-dir", type=Path, required=True,
                        help="Path to sealed PIT Run directory")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--initial-capital", type=float, default=500_000.0)
    args = parser.parse_args()
    result = build_formal_package(
        formal_pit_run_id=args.pit_run_id,
        pit_run_dir=args.pit_run_dir,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        initial_account_cny=args.initial_capital,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
