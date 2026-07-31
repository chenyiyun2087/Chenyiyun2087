#!/usr/bin/env python3
"""Formal PIT Pipeline — single, immutable entry point for PIT evidence production.

This is the ONLY path that can produce formal PIT evidence in the
Chenyiyun2087 Formal Evidence Backbone.  Individual Adapter, Builder,
or Auditor components may only produce diagnostic evidence when invoked
directly.  Formal qualification requires this orchestrator.

v5.1.2: PIT Run is separated from Formal Package.  This pipeline produces
a sealed, immutable PIT Run containing Adapter snapshots, Factor Panel, and
Formal Scores.  The Formal Package is built separately from a verified PIT Run.

Pipeline:
  Stage 0: Identity Lock
  Stage 1: Adapter
  Stage 2: Semantic Audit
  Stage 3: Factor Builder
  Stage 4: Score Builder
  Stage 5: Write pit_run_manifest.json
  Stage 6: Seal
  Stage 7: Atomic Publish

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
from runtime.formal_evidence_binding import check_clean_worktree
from runtime.formal_evidence_contract import (
    EvidenceStatus,
    VersionManifest,
    compute_formal_pit_run_id,
    update_active_formal_registry,
)
from scripts.research.pit_data_adapter import build_pit_adapter_manifest
from scripts.research.pit_factor_panel_builder import build_pit_factor_panel

FORMAL_PIT_RUNS_ROOT = PROJECT_ROOT / "exports" / "formal_pit_runs"

# ── Identity files whose SHAs replace pending_pr_5 ──
DEPENDENCY_LOCK_PATH = PROJECT_ROOT / "requirements.lock.txt"
FIELD_SEMANTICS_PATH = PROJECT_ROOT / "config" / "factor_registry.yaml"


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


def _db_snapshot_identity() -> str:
    """Capture a stable database identity string.

    Uses server_uuid + transaction_isolation as a stable identifier.
    Falls back to timestamp if DB is unavailable (diagnostic only).
    """
    import pymysql
    try:
        db_url = os.getenv("CHENYIYUN_DB_URL")
        if db_url:
            # Extract host from URL for a short identity
            conn = pymysql.connect(
                host=os.getenv("CHENYIYUN_DB_HOST", "localhost"),
                port=int(os.getenv("CHENYIYUN_DB_PORT", "3306")),
                user=os.getenv("CHENYIYUN_DB_USER", "root"),
                password=os.getenv("CHENYIYUN_DB_PASSWORD", ""),
                database=os.getenv("CHENYIYUN_DB_NAME", "chenyiyun"),
                connect_timeout=5,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT @@server_uuid, @@transaction_isolation")
                row = cur.fetchone()
                conn.close()
                if row:
                    return f"{row[0]}_iso_{row[1]}"
    except Exception:
        pass
    # Fallback: timestamp (non-reproducible, diagnostic only)
    return f"mysql_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


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
    # Git worktree must be clean
    try:
        git_sha_val, is_clean = check_clean_worktree()
        if not is_clean:
            blockers.append("git_worktree_not_clean")
    except Exception:
        blockers.append("git_worktree_check_failed")
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
    # Dependency lock must exist
    if not DEPENDENCY_LOCK_PATH.exists():
        blockers.append("dependency_lock_missing")
    # Field semantics must exist
    if not FIELD_SEMANTICS_PATH.exists():
        blockers.append("field_semantics_missing")
    return blockers


def _write_stage_report(
    stage_dir: Path,
    stage_name: str,
    result: dict[str, Any],
) -> None:
    """Write a per-stage report for auditability."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "stage": stage_name,
        "status": result.get("status", "BLOCKED"),
        "blockers": result.get("blockers", []),
        "component": result.get("component", stage_name),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    (stage_dir / f"{stage_name}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def _block_and_seal(
    building_dir: Path,
    run_id: str,
    git_sha: str,
    stage_name: str,
    error_code: str,
    *,
    exception: Exception | None = None,
    extra: dict[str, Any] | None = None,
    registry_path_override: Path | None = None,
) -> dict[str, Any]:
    """Write blocked run manifest, seal, atomically publish, and return BLOCKED.

    registry_path_override enables test isolation for seal registration.
    """
    _write_pit_run_manifest(
        building_dir, run_id, "unknown", "BLOCKED", [error_code],
        strategy_set="unknown",
    )
    seal_directory(building_dir, run_id=run_id, git_commit_sha=git_sha,
                   registry_path_override=registry_path_override)
    run_dir = FORMAL_PIT_RUNS_ROOT / run_id
    if not run_dir.exists():
        building_dir.rename(run_dir)
    return blocked_report(
        "formal_pit_orchestrator", stage_name, error_code,
        exception=exception,
        extra=extra,
    )


def run_formal_pit_pipeline(
    *,
    release_id: str,
    strategy_set: str,
    adapter_config_path: Path,
    acceptance_profile: str = "formal_v5_0",
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

    # ── Resolve real identity SHAs (no more pending_pr_5) ──
    dependency_lock_sha = _file_sha(DEPENDENCY_LOCK_PATH)
    query_bundle_sha = config_sha  # The adapter config IS the query bundle
    field_semantics_sha = _file_sha(FIELD_SEMANTICS_PATH)
    db_snapshot_id = _db_snapshot_identity()

    run_id = compute_formal_pit_run_id(
        release_id=release_id,
        strategy_set=strategy_set,
        git_commit_sha=git_sha,
        dependency_lock_sha=dependency_lock_sha,
        acceptance_profile_sha=profile_sha,
        adapter_config_sha=config_sha,
        query_bundle_sha=query_bundle_sha,
        field_semantics_sha=field_semantics_sha,
        database_snapshot_identity=db_snapshot_id,
    )

    run_dir = FORMAL_PIT_RUNS_ROOT / run_id
    building_dir = FORMAL_PIT_RUNS_ROOT / f".building_{run_id[:16]}"

    # ── Pre-run: check run ID not reused ──
    if run_dir.exists() or building_dir.exists():
        shutil.rmtree(building_dir, ignore_errors=True)
        return blocked_report(
            "formal_pit_orchestrator", "pre_run",
            "run_id_already_exists",
            extra={"run_id": run_id},
        )

    building_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 0: Identity Lock
    # ═══════════════════════════════════════════════════════════════════════
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
    # Record resolved identity SHAs
    (identity_dir / "identity_lock.json").write_text(json.dumps({
        "dependency_lock_sha256": dependency_lock_sha,
        "query_bundle_sha256": query_bundle_sha,
        "field_semantics_sha256": field_semantics_sha,
        "database_snapshot_identity": db_snapshot_id,
        "git_commit_sha": git_sha,
        "release_id": release_id,
        "strategy_set": strategy_set,
    }, ensure_ascii=False, indent=2))

    # ── Config ──
    config_dir = building_dir / "config"
    config_dir.mkdir()
    shutil.copy2(adapter_config_path, config_dir / "adapter_config.json")

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 1: Adapter
    # ═══════════════════════════════════════════════════════════════════════
    adapter_dir = building_dir / "adapter"
    adapter_dir.mkdir()
    try:
        adapter_result = build_pit_adapter_manifest(
            config_dir / "adapter_config.json", adapter_dir,
        )
    except Exception as exc:
        return _block_and_seal(building_dir, run_id, git_sha, "adapter",
                               f"adapter_exception:{type(exc).__name__}", exception=exc)

    _write_stage_report(adapter_dir, "adapter", adapter_result)

    if adapter_result.get("status") != "PASS":
        return _block_and_seal(building_dir, run_id, git_sha, "adapter",
                               "adapter_not_pass",
                               extra={"blockers": adapter_result.get("blockers", [])})

    manifest_path = Path(adapter_result["manifest_path"])
    adapter_report_path = adapter_dir / "pit_adapter_report.json"
    snapshots_dir = adapter_dir / "snapshots"

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 2: Semantic Audit
    # ═══════════════════════════════════════════════════════════════════════
    audit_dir = building_dir / "audit"
    audit_dir.mkdir()
    try:
        from scripts.research.pit_semantic_audit import run_semantic_audit
        audit_result = run_semantic_audit(snapshots_dir, manifest_path)
    except ImportError:
        # Semantic audit module not available — BLOCKED for formal historical data
        audit_result = {
            "status": "BLOCKED",
            "component": "semantic_audit",
            "blockers": ["semantic_audit_module_not_available"],
        }
    except Exception as exc:
        return _block_and_seal(building_dir, run_id, git_sha, "semantic_audit",
                               f"audit_exception:{type(exc).__name__}", exception=exc)

    _write_stage_report(audit_dir, "semantic_audit", audit_result)

    if audit_result.get("status") == "BLOCKED":
        return _block_and_seal(building_dir, run_id, git_sha, "semantic_audit",
                               "semantic_audit_blocked",
                               extra={"blockers": audit_result.get("blockers", [])})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 3: Factor Builder
    # ═══════════════════════════════════════════════════════════════════════
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
        return _block_and_seal(building_dir, run_id, git_sha, "builder",
                               f"builder_exception:{type(exc).__name__}", exception=exc)

    _write_stage_report(builder_dir, "factor_builder", builder_result)

    if builder_result.get("status") != "PASS":
        return _block_and_seal(building_dir, run_id, git_sha, "factor_builder",
                               "factor_builder_not_pass",
                               extra={"blockers": builder_result.get("blockers", [])})

    factor_panel_path = builder_dir / "factor_panel_daily.parquet"

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 4: Score Builder
    # ═══════════════════════════════════════════════════════════════════════
    scores_dir = building_dir / "scores"
    scores_dir.mkdir()
    try:
        from scripts.research.build_formal_scores import build_formal_scores
        score_result = build_formal_scores(
            factor_panel_path=factor_panel_path,
            output_dir=scores_dir,
        )
    except Exception as exc:
        return _block_and_seal(building_dir, run_id, git_sha, "score_builder",
                               f"score_exception:{type(exc).__name__}", exception=exc)

    _write_stage_report(scores_dir, "score_builder", score_result)

    if score_result.get("status") != "PASS":
        return _block_and_seal(building_dir, run_id, git_sha, "score_builder",
                               "score_builder_not_pass",
                               extra={"blockers": score_result.get("blockers", [])})

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 5: Write pit_run_manifest
    # ═══════════════════════════════════════════════════════════════════════
    scores_sha = score_result.get("score_sha256", "")
    pit_run_manifest = {
        "schema_version": "pit_run_manifest_v5_1_2",
        "formal_pit_run_id": run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "status": "PASS",
        "blockers": [],
        "git_commit_sha": git_sha,
        "adapter_config_sha256": _file_sha(config_dir / "adapter_config.json"),
        "factor_panel_sha256": _file_sha(factor_panel_path),
        "scores_sha256": scores_sha,
        "scores_path": str((scores_dir / "formal_scores.parquet").relative_to(building_dir)),
        "factor_panel_path": str(factor_panel_path.relative_to(building_dir)),
        "evidence_status": EvidenceStatus().as_dict(),
        "capital_authority": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pit_run_manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in pit_run_manifest.items() if k != "content_sha256"}
    )
    (building_dir / "pit_run_manifest.json").write_text(
        json.dumps(pit_run_manifest, ensure_ascii=False, indent=2, sort_keys=True))

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 6: Seal
    # ═══════════════════════════════════════════════════════════════════════
    seal_directory(building_dir, run_id=run_id, git_commit_sha=git_sha)

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 7: Atomic Publish
    # ═══════════════════════════════════════════════════════════════════════
    building_dir.rename(run_dir)

    # ── Register in active formal evidence registry ──
    # PR-B/C/D/E paths are set by downstream Package/Runner/OOS/Capacity,
    # not by this PIT Run pipeline.
    registry_payload = {
        "schema_version": "formal_evidence_registry_v1",
        "formal_pit_run_id": run_id,
        "formal_run_id": None,
        "pr_a_path": None,
        "pr_b_path": None,
        "pr_c_path": None,
        "pr_d_path": None,
        "pr_e_path": None,
        "pr_i_path": None,
        "seal_manifest_sha256": _file_sha(run_dir / "seal_manifest.json"),
        "capital_authority": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": git_sha,
    }
    try:
        update_active_formal_registry(registry_payload)
    except Exception as reg_exc:
        # v5.1.2: Registry activation failure writes external activation report.
        # The sealed PIT Run is never modified.
        activation_report = {
            "schema_version": "activation_report_v5_1_2",
            "formal_pit_run_id": run_id,
            "status": "ACTIVATION_FAILED",
            "error": f"{type(reg_exc).__name__}: {reg_exc}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        reg_dir = PROJECT_ROOT / "exports" / "formal_evidence_registry"
        reg_dir.mkdir(parents=True, exist_ok=True)
        (reg_dir / f"activation_failed_{run_id[:16]}.json").write_text(
            json.dumps(activation_report, ensure_ascii=False, indent=2, sort_keys=True))

    return json.loads((run_dir / "pit_run_manifest.json").read_text())


def _write_pit_run_manifest(
    run_dir: Path, run_id: str, release_id: str,
    status: str, blockers: list[str],
    strategy_set: str = "unknown",
) -> dict[str, Any]:
    """Write pit_run_manifest.json — the PIT Run's own manifest."""
    manifest = {
        "schema_version": "pit_run_manifest_v5_1_2",
        "formal_pit_run_id": run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "status": status,
        "blockers": blockers,
        "evidence_status": EvidenceStatus().as_dict(),
        "capital_authority": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (run_dir / "pit_run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--adapter-config", type=Path, required=True)
    parser.add_argument("--acceptance-profile", default="formal_v5_0")
    args = parser.parse_args()
    result = run_formal_pit_pipeline(
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        adapter_config_path=args.adapter_config,
        acceptance_profile=args.acceptance_profile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
