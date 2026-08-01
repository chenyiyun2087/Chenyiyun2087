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
from runtime.artifact_seal import seal_directory, verify_seal
from runtime.fail_closed import blocked_report, fail_closed
from runtime.formal_evidence_binding import check_clean_worktree
from runtime.formal_evidence_contract import (
    EvidenceStatus,
    VersionManifest,
    compute_formal_pit_run_id,
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


def _db_snapshot_identity(adapter_config_path: Path) -> str:
    """Return a provider-issued immutable snapshot identity.

    A timestamp or server UUID is not a database snapshot.  Formal runs must
    bind to a configured snapshot token/GTID and the adapter later verifies
    that the same token was observed inside its repeatable-read transaction.
    """
    try:
        config = json.loads(adapter_config_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    identity = str(
        config.get("snapshot_token")
        or config.get("snapshot_id")
        or os.getenv("CHENYIYUN_DB_SNAPSHOT_TOKEN")
        or ""
    ).strip()
    return identity


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
    # DB URL and stable snapshot identity are mandatory for formal MYSQL runs;
    # FILE runs still require an explicit immutable snapshot_id.
    try:
        adapter_cfg = json.loads(adapter_config_path.read_text(encoding="utf-8"))
    except Exception:
        adapter_cfg = {}
    adapter_type = str(adapter_cfg.get("adapter_type") or "").upper()
    if adapter_type == "MYSQL" and not os.getenv("CHENYIYUN_DB_URL"):
        blockers.append("CHENYIYUN_DB_URL_not_configured")
    if not _db_snapshot_identity(adapter_config_path):
        blockers.append("database_snapshot_identity_not_configured")
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


def _write_alpha_evidence_placeholders(
    building_dir: Path,
    *,
    profile: dict[str, Any],
    release_id: str,
    strategy_set: str,
    input_snapshot_sha256: str,
    snapshot_identity: dict[str, Any] | None = None,
) -> None:
    """Emit explicit fail-closed downstream evidence until OOS/ledger inputs exist."""
    reports_dir = building_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "config_sha256": canonical_sha(profile),
        "evidence_version": profile.get("evidence_version", "alpha_v3_2_evidence_v1"),
        "code_head": _git_sha(),
        "input_snapshot_sha256": input_snapshot_sha256,
        "snapshot_identity": snapshot_identity or {},
        "release": release_id,
        "strategy": strategy_set,
        "sample_start": profile.get("core_period", {}).get("min_start_date"),
        "sample_end": None,
        "timezone": "Asia/Shanghai",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_model": "T15:30_signal_T+1_09:30_open",
        "capital_authority": False,
        "research_status": "BLOCKED_DATA",
        "trading_status": "TRADING_BLOCKED",
        "capital_status": "NO_SCALE",
        "allowed_new_capital_cny": 0,
    }
    names = {
        "alpha_attribution_report.json": "attribution_and_oos_evidence_missing",
        "factor_ic_report.json": "factor_ic_evidence_missing",
        "walk_forward_report.json": "walk_forward_evidence_missing",
        "execution_cost_report.json": "execution_cost_evidence_missing",
        "alpha_proof_guard_report.json": "proof_guard_downstream_evidence_missing",
        "alpha_proof_report.json": "proof_summary_downstream_evidence_missing",
    }
    for filename, blocker in names.items():
        payload = {
            "schema_version": filename.removesuffix(".json") + "_v1",
            **common,
            "status": "BLOCKED",
            "blockers": [blocker],
        }
        payload["content_sha256"] = canonical_sha(
            {key: value for key, value in payload.items() if key != "content_sha256"}
        )
        (reports_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    gate = {
        "schema_version": "promotion_gate_report_v1",
        **common,
        "status": "BLOCKED",
        "gates": {
            "research": "BLOCKED_DATA",
            "trading": "TRADING_BLOCKED",
            "capital": "NO_SCALE",
        },
        "blockers": ["downstream_oos_and_shadow_evidence_missing"],
    }
    gate["content_sha256"] = canonical_sha(
        {key: value for key, value in gate.items() if key != "content_sha256"}
    )
    (reports_dir / "promotion_gate_report.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    scorecard = {
        "schema_version": "strategy_scorecard_v1",
        **common,
        "status": "BLOCKED_DATA",
        "promotion_gate_report_sha256": _file_sha(reports_dir / "promotion_gate_report.json"),
        "allowed_new_capital_cny": 0,
    }
    scorecard["content_sha256"] = canonical_sha(
        {key: value for key, value in scorecard.items() if key != "content_sha256"}
    )
    (reports_dir / "strategy_scorecard.json").write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


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
    acceptance_profile: str = "alpha_v3_2",
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
    db_snapshot_id = _db_snapshot_identity(adapter_config_path)

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

    if audit_result.get("status") != "PASS":
        return _block_and_seal(building_dir, run_id, git_sha, "semantic_audit",
                               f"semantic_audit_not_pass:{audit_result.get('status')}",
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
            trade_calendar_path=snapshots_dir / "trade_calendar.parquet",
            security_lifecycle_path=snapshots_dir / "security_lifecycle.parquet",
            corporate_actions_path=snapshots_dir / "corporate_actions.parquet",
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
        from runtime.formal_contract import FORMAL_STRATEGIES
        score_result = build_formal_scores(
            factor_panel_path=factor_panel_path,
            output_dir=scores_dir,
            strategy_ids=list(FORMAL_STRATEGIES),
        )
    except Exception as exc:
        return _block_and_seal(building_dir, run_id, git_sha, "score_builder",
                               f"score_exception:{type(exc).__name__}", exception=exc)

    _write_stage_report(scores_dir, "score_builder", score_result)

    if score_result.get("status") != "PASS":
        return _block_and_seal(building_dir, run_id, git_sha, "score_builder",
                               "score_builder_not_pass",
                               extra={"blockers": score_result.get("blockers", [])})

    adapter_manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_snapshot_sha256 = canonical_sha({
        name: (info or {}).get("content_sha256") or (info or {}).get("sha256")
        for name, info in sorted((adapter_manifest_payload.get("sources") or {}).items())
    })
    _write_alpha_evidence_placeholders(
        building_dir,
        profile=profile,
        release_id=release_id,
        strategy_set=strategy_set,
        input_snapshot_sha256=input_snapshot_sha256,
        snapshot_identity=adapter_manifest_payload.get("snapshot_identity"),
    )

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
        "source_manifest_sha256": _file_sha(manifest_path),
        "adapter_report_sha256": _file_sha(adapter_report_path),
        "semantic_audit_sha256": _file_sha(audit_dir / "semantic_audit_report.json"),
        "snapshot_identity": adapter_manifest_payload.get("snapshot_identity"),
        "input_snapshot_sha256": canonical_sha({
            name: (info or {}).get("content_sha256") or (info or {}).get("sha256")
            for name, info in sorted((adapter_manifest_payload.get("sources") or {}).items())
        }) if adapter_manifest_payload.get("sources") else "",
        "scores_path": str((scores_dir / "formal_scores.parquet").relative_to(building_dir)),
        "factor_panel_path": str(factor_panel_path.relative_to(building_dir)),
        "evidence_status": EvidenceStatus().as_dict(),
        "capital_authority": False,
        "research_status": "RESEARCH_PASS",
        "trading_status": "TRADING_BLOCKED",
        "capital_status": "NO_SCALE",
        "allowed_new_capital_cny": 0,
        "execution_model": "T15:30_signal_T+1_09:30_open",
        "timezone": "Asia/Shanghai",
        "sample_start": builder_result.get("sample_start"),
        "sample_end": builder_result.get("sample_end"),
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
    seal_check = verify_seal(building_dir)
    if seal_check.get("status") != "VERIFIED":
        return blocked_report(
            "formal_pit_orchestrator", "seal",
            "pit_run_seal_verification_failed",
            extra={"run_id": run_id, "seal_result": seal_check},
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Stage 7: Atomic Publish
    # ═══════════════════════════════════════════════════════════════════════
    building_dir.rename(run_dir)

    # ── Register in pit_candidate_registry (NOT active_formal_run) ──
    # v5.1.5: PIT Run is a candidate, not an active chain.
    # Only a complete PR-I PASS updates active_formal_run.json.
    candidate_payload = {
        "schema_version": "pit_candidate_registry_v5_1_5",
        "formal_pit_run_id": run_id,
        "status": "PIT_VERIFIED",
        "seal_manifest_sha256": _file_sha(run_dir / "seal_manifest.json"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": git_sha,
    }
    try:
        reg_dir = PROJECT_ROOT / "exports" / "formal_evidence_registry"
        reg_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = reg_dir / "pit_candidate_registry.json"
        tmp = candidate_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(candidate_payload, ensure_ascii=False, indent=2, sort_keys=True))
        tmp.replace(candidate_path)
    except Exception as reg_exc:
        activation_report = {
            "schema_version": "activation_report_v5_1_5",
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
    parser.add_argument("--acceptance-profile", default="alpha_v3_2")
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
