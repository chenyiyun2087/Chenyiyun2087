#!/usr/bin/env python3
"""Formal PIT Pipeline — single, immutable entry point for evidence production.

This is the ONLY path that can produce formal PIT evidence in the
Chenyiyun2087 Formal Evidence Backbone v5.0.  Individual Adapter, Builder,
or Auditor components may only produce diagnostic evidence when invoked
directly.  Formal qualification requires this orchestrator.

Run identity is content-addressed from all inputs.  The output directory
is atomically published from .building/ → formal_pit_run_id/.  Reusing
a run ID is forbidden.  Any failure produces a sealed BLOCKED directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha, load_validation_profile
from runtime.artifact_seal import seal_directory
from runtime.fail_closed import blocked_report, fail_closed
from runtime.formal_evidence_contract import (
    EvidenceStatus,
    VersionManifest,
    compute_formal_pit_run_id,
)
from scripts.research.pit_data_adapter import build_pit_adapter_manifest
from scripts.research.pit_factor_panel_builder import build_pit_factor_panel

FORMAL_RUNS_ROOT = PROJECT_ROOT / "exports" / "formal_evidence_runs"


def _file_sha(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def _check_prerequisites(
    adapter_config_path: Path,
    acceptance_profile: str,
) -> list[str]:
    """Run pre-flight checks. Returns list of blockers (empty = ready)."""
    blockers = []
    # Git HEAD must be resolvable
    try:
        _git_sha()
    except Exception:
        blockers.append("git_head_unresolvable")
    # Adapter config must exist
    if not adapter_config_path.exists():
        blockers.append("adapter_config_missing")
    # Acceptance profile must exist
    try:
        load_validation_profile(acceptance_profile)
    except Exception:
        blockers.append(f"acceptance_profile_missing:{acceptance_profile}")
    # DB URL must be set
    if not os.getenv("CHENYIYUN_DB_URL"):
        blockers.append("CHENYIYUN_DB_URL_not_configured")
    return blockers


def run_formal_pit_pipeline(
    *,
    release_id: str,
    strategy_set: str,
    adapter_config_path: Path,
    acceptance_profile: str = "formal_v5_0",
    end_date: str | None = None,
) -> dict[str, Any]:
    """Execute the complete formal PIT pipeline. Returns run manifest."""
    blockers = _check_prerequisites(adapter_config_path, acceptance_profile)
    if blockers:
        return blocked_report(
            "formal_pit_orchestrator", "prerequisites",
            "preflight_blocked",
            extra={"blockers": blockers},
        )

    git_sha = _git_sha()
    profile = load_validation_profile(acceptance_profile)
    profile_sha = canonical_sha(profile)
    config_sha = _file_sha(adapter_config_path)
    version_manifest_path = PROJECT_ROOT / "config" / "version_manifest.yaml"
    version_manifest = VersionManifest.from_dict(
        yaml.safe_load(version_manifest_path.read_text(encoding="utf-8"))
    )
    version_sha = version_manifest.content_sha()

    # Database snapshot identity (best-effort for now; full watermark in PR-5)
    db_snapshot_id = f"mysql_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    run_id = compute_formal_pit_run_id(
        release_id=release_id,
        strategy_set=strategy_set,
        git_commit_sha=git_sha,
        dependency_lock_sha="pending_pr_5",
        acceptance_profile_sha=profile_sha,
        adapter_config_sha=config_sha,
        query_bundle_sha="pending_pr_5",
        field_semantics_sha="pending_pr_5",
        database_snapshot_identity=db_snapshot_id,
    )

    run_dir = FORMAL_RUNS_ROOT / run_id
    building_dir = FORMAL_RUNS_ROOT / f".building_{run_id[:16]}"

    # ── Pre-run: check run ID not reused ──
    if run_dir.exists() or building_dir.exists():
        shutil.rmtree(building_dir, ignore_errors=True)
        return blocked_report(
            "formal_pit_orchestrator", "pre_run",
            "run_id_already_exists",
            extra={"run_id": run_id},
        )

    building_dir.mkdir(parents=True, exist_ok=True)

    # ── Identity ──
    identity_dir = building_dir / "identity"
    identity_dir.mkdir()
    (identity_dir / "git_manifest.json").write_text(json.dumps(
        {"git_commit_sha": git_sha, "captured_at": datetime.now(timezone.utc).isoformat()},
        ensure_ascii=False, indent=2))
    (identity_dir / "acceptance_profile.json").write_text(json.dumps(
        {"profile_name": acceptance_profile, "profile_sha256": profile_sha},
        ensure_ascii=False, indent=2))
    (identity_dir / "version_manifest.json").write_text(json.dumps(
        version_manifest.as_dict(), ensure_ascii=False, indent=2))

    # ── Config ──
    config_dir = building_dir / "config"
    config_dir.mkdir()
    shutil.copy2(adapter_config_path, config_dir / "adapter_config.json")

    # ── Stage 1: Adapter ──
    adapter_dir = building_dir / "adapter"
    adapter_dir.mkdir()
    try:
        adapter_result = build_pit_adapter_manifest(
            adapter_config_path, adapter_dir
        )
    except Exception as exc:
        shutil.rmtree(building_dir, ignore_errors=True)
        return fail_closed("formal_pit_orchestrator", "adapter", exc)

    if adapter_result.get("status") != "PASS":
        seal_directory(building_dir, run_id=run_id, git_commit_sha=git_sha)
        building_dir.rename(run_dir)
        return _write_run_manifest(run_dir, run_id, release_id, "BLOCKED",
                                   adapter_result.get("blockers", []))

    manifest_path = Path(adapter_result["manifest_path"])
    adapter_report_path = adapter_dir / "pit_adapter_report.json"
    snapshots_dir = adapter_dir / "snapshots"

    # ── Stage 2: Builder ──
    builder_dir = building_dir / "builder"
    builder_dir.mkdir()
    try:
        builder_result = build_pit_factor_panel(
            market_path=snapshots_dir / "market.parquet",
            universe_path=snapshots_dir / "universe.parquet",
            financial_path=snapshots_dir / "financial.parquet",
            industry_path=snapshots_dir / "industry.parquet",
            adjustment_path=snapshots_dir / "adjustment.parquet",
            source_manifest_path=manifest_path,
            adapter_report_path=adapter_report_path,
            output_dir=builder_dir,
            profile_name=acceptance_profile,
            fixture_mode=False,
        )
    except Exception as exc:
        seal_directory(building_dir, run_id=run_id, git_commit_sha=git_sha)
        building_dir.rename(run_dir)
        return fail_closed("formal_pit_orchestrator", "builder", exc)

    # ── Seal and publish ──
    status = builder_result.get("status", "BLOCKED")
    blockers_list = builder_result.get("blockers", [])
    seal_directory(building_dir, run_id=run_id, git_commit_sha=git_sha)
    building_dir.rename(run_dir)

    return _write_run_manifest(run_dir, run_id, release_id, status, blockers_list)


def _write_run_manifest(
    run_dir: Path, run_id: str, release_id: str,
    status: str, blockers: list[str],
) -> dict[str, Any]:
    manifest = {
        "schema_version": "formal_evidence_backbone_v5_0",
        "run_id": run_id,
        "release_id": release_id,
        "status": status,
        "blockers": blockers,
        "evidence_status": EvidenceStatus().as_dict(),
        "capital_authority": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--adapter-config", type=Path, required=True)
    parser.add_argument("--acceptance-profile", default="formal_v5_0")
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()
    result = run_formal_pit_pipeline(
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        adapter_config_path=args.adapter_config,
        acceptance_profile=args.acceptance_profile,
        end_date=args.end_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
