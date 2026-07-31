#!/usr/bin/env python3
"""Artifact Seal v2 — deterministic file tree hashing with external trust anchor.

After a formal run completes, the seal traverses all output files, records
their SHA-256 and byte counts, computes a deterministic artifact_tree_sha256,
and makes all files read-only.  The seal is then registered in an external
seal_registry.json so that downstream verification cannot succeed by simply
re-sealing tampered files.

v2 changes:
  - Symlink detection: seal_directory rejects run dirs containing symlinks.
  - External registration: each seal is registered in seal_registry.json.
  - verify_seal cross-checks against the registry entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.acceptance_config import canonical_sha

SEAL_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "exports" / "formal_evidence_registry" / "seal_registry.json"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _register_seal(
    run_id: str,
    seal_manifest: dict[str, Any],
    seal_path: Path,
    registry_path: Path | None = None,
) -> None:
    """Register a seal in the external seal_registry.json.

    v2.1: seal_manifest_file_sha256 is now the SHA-256 of the seal manifest
    FILE bytes (not the internal content_sha256).  This prevents an attacker
    from modifying seal contents while preserving the old content_sha256.
    """
    registry_path = Path(registry_path or SEAL_REGISTRY_PATH)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry = {}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            registry = {"schema_version": "seal_registry_v1", "entries": {}}

    if not isinstance(registry, dict):
        registry = {"schema_version": "seal_registry_v1", "entries": {}}
    if "entries" not in registry:
        registry["entries"] = {}

    entry = {
        "run_id": run_id,
        "seal_manifest_file_sha256": _file_sha(seal_path),
        "artifact_tree_sha256": seal_manifest.get("artifact_tree_sha256", ""),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "registered_by": seal_manifest.get("git_commit_sha", ""),
        "status": "ACTIVE",
    }
    registry["entries"][run_id] = entry
    registry["updated_at"] = datetime.now(timezone.utc).isoformat()

    tmp = registry_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True))
    tmp.replace(registry_path)


def seal_directory(
    run_dir: Path,
    *,
    run_id: str,
    git_commit_sha: str,
    fixture_mode: bool = False,
    registry_path_override: Path | None = None,
) -> dict[str, Any]:
    """Traverse run_dir, hash every file, write seal manifest, chmod read-only.

    v2 additions:
      - Rejects directories containing symlinks.
      - Registers the seal in seal_registry.json after completion.
      - registry_path_override allows test isolation.

    Returns the seal manifest.  Raises OSError if the directory is empty,
    unreadable, or contains symlinks.
    """
    if not run_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {run_dir}")

    # ── Symlink detection ──
    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise OSError(f"Symlinks forbidden in sealed directory: {path}")

    files: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(run_dir))
        sha = _file_sha(path)
        size = path.stat().st_size
        files[rel] = {"sha256": sha, "bytes": size}
        total_bytes += size

    if not files:
        raise ValueError(f"No files found in {run_dir}")

    # Compute deterministic tree hash from sorted file list
    tree_payload = {
        rel: {k: meta[k] for k in sorted(meta)}
        for rel, meta in sorted(files.items())
    }
    tree_sha = hashlib.sha256(
        json.dumps(tree_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    seal = {
        "schema_version": "artifact_tree_seal_v2",
        "run_id": run_id,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "artifact_tree_sha256": tree_sha,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": git_commit_sha,
        "fixture_mode": fixture_mode,
    }
    seal["content_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in seal.items() if k != "content_sha256"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()

    # Write seal manifest
    seal_path = run_dir / "seal_manifest.json"
    seal_path.write_text(json.dumps(seal, ensure_ascii=False, indent=2, sort_keys=True))

    # Register seal in external registry (v2.1: pass seal_path for file SHA)
    _register_seal(run_id, seal, seal_path, registry_path=registry_path_override)

    # v2.1: Make all files AND subdirectories read-only (including seal)
    for path in run_dir.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    # Make run_dir itself read-only after sealing
    run_dir.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )

    return seal


def verify_seal(
    run_dir: Path,
    *,
    check_registry: bool = True,
    registry_path_override: Path | None = None,
) -> dict[str, Any]:
    """Verify an existing seal: recompute all hashes, compare tree SHA.

    v2.1 changes:
      - Recomputes seal self-hash (content_sha256) to verify integrity.
      - Compares seal_manifest_file_sha256 against sha256(seal file bytes),
        not the internal content_sha256.
      - Registry missing → TAMPERED (not silently pass).
      - Registry run entry missing → TAMPERED.
      - Registry entry not ACTIVE → TAMPERED.
      - Registry damaged/unreadable → TAMPERED (not silently pass).
      - registry_path_override allows test isolation.

    Returns {"status": "VERIFIED", ...} or {"status": "TAMPERED", ...}.
    """
    seal_path = run_dir / "seal_manifest.json"
    if not seal_path.exists():
        return {"status": "UNSEALED", "error": "seal_manifest.json not found"}

    original = json.loads(seal_path.read_text(encoding="utf-8"))
    expected_tree = original.get("artifact_tree_sha256")

    # ── v2.1: Verify seal self-hash ──
    declared_content_sha = original.get("content_sha256")
    if declared_content_sha:
        recomputed_content_sha = hashlib.sha256(
            json.dumps(
                {k: v for k, v in original.items() if k != "content_sha256"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if recomputed_content_sha != declared_content_sha:
            return {
                "status": "TAMPERED",
                "reason": "seal_content_sha_mismatch",
                "declared": declared_content_sha,
                "recomputed": recomputed_content_sha,
            }

    # ── Recompute artifact tree ──
    files = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name == "seal_manifest.json":
            continue
        rel = str(path.relative_to(run_dir))
        files[rel] = {"sha256": _file_sha(path), "bytes": path.stat().st_size}

    tree_payload = {
        rel: {k: meta[k] for k in sorted(meta)}
        for rel, meta in sorted(files.items())
    }
    actual_tree = hashlib.sha256(
        json.dumps(tree_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if actual_tree != expected_tree:
        return {
            "status": "TAMPERED",
            "reason": "artifact_tree_mismatch",
            "expected_tree_sha256": expected_tree,
            "actual_tree_sha256": actual_tree,
            "file_count_expected": original.get("file_count"),
            "file_count_actual": len(files),
        }

    # ── v2.1: External registry cross-check (mandatory, fail-closed) ──
    if check_registry:
        registry_path = registry_path_override or Path(SEAL_REGISTRY_PATH)
        if not registry_path.exists():
            return {
                "status": "TAMPERED",
                "reason": "seal_registry_not_found",
                "registry_path": str(registry_path),
            }

        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "TAMPERED",
                "reason": f"seal_registry_damaged:{type(exc).__name__}",
            }

        if not isinstance(registry, dict):
            return {
                "status": "TAMPERED",
                "reason": "seal_registry_invalid_format",
            }

        run_id = original.get("run_id")
        entry = registry.get("entries", {}).get(run_id)
        if not entry:
            return {
                "status": "TAMPERED",
                "reason": "run_not_found_in_seal_registry",
                "run_id": run_id,
            }

        if entry.get("status") != "ACTIVE":
            return {
                "status": "TAMPERED",
                "reason": f"registry_entry_not_active:{entry.get('status')}",
            }

        # v2.1: Compare seal_manifest_file_sha256 = sha256(seal file bytes)
        registered_file_sha = entry.get("seal_manifest_file_sha256")
        actual_file_sha = _file_sha(seal_path)
        if registered_file_sha != actual_file_sha:
            return {
                "status": "TAMPERED",
                "reason": "seal_manifest_file_sha_mismatch_with_registry",
                "registered_file_sha": registered_file_sha,
                "actual_file_sha": actual_file_sha,
            }

    return {
        "status": "VERIFIED",
        "artifact_tree_sha256": actual_tree,
        "file_count": len(files),
        "sealed_at": original.get("sealed_at"),
    }
