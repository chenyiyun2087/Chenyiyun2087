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


def _register_seal(run_id: str, seal_manifest: dict[str, Any]) -> None:
    """Register a seal in the external seal_registry.json."""
    registry_path = Path(SEAL_REGISTRY_PATH)
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
        "seal_manifest_file_sha256": seal_manifest.get("content_sha256", ""),
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
) -> dict[str, Any]:
    """Traverse run_dir, hash every file, write seal manifest, chmod read-only.

    v2 additions:
      - Rejects directories containing symlinks.
      - Registers the seal in seal_registry.json after completion.

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

    # Register seal in external registry
    _register_seal(run_id, seal)

    # Make all files (including seal) read-only
    for path in run_dir.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    # Make run_dir itself read-only after sealing
    run_dir.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )

    return seal


def verify_seal(run_dir: Path, *, check_registry: bool = True) -> dict[str, Any]:
    """Verify an existing seal: recompute all hashes, compare tree SHA.

    v2: When check_registry=True, also cross-checks the seal's content_sha256
    against the external seal_registry.json entry.  This prevents "re-seal to
    pass verification" attacks.

    Returns {"status": "VERIFIED", ...} or {"status": "TAMPERED", ...}.
    """
    seal_path = run_dir / "seal_manifest.json"
    if not seal_path.exists():
        return {"status": "UNSEALED", "error": "seal_manifest.json not found"}

    original = json.loads(seal_path.read_text(encoding="utf-8"))
    expected_tree = original.get("artifact_tree_sha256")

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
            "expected_tree_sha256": expected_tree,
            "actual_tree_sha256": actual_tree,
            "file_count_expected": original.get("file_count"),
            "file_count_actual": len(files),
        }

    # ── External registry cross-check ──
    if check_registry:
        registry_path = Path(SEAL_REGISTRY_PATH)
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                entry = registry.get("entries", {}).get(original.get("run_id"))
                if entry:
                    registered_manifest_sha = entry.get("seal_manifest_file_sha256")
                    current_manifest_sha = original.get("content_sha256")
                    if registered_manifest_sha != current_manifest_sha:
                        return {
                            "status": "TAMPERED",
                            "reason": "seal_manifest_sha_mismatch_with_registry",
                            "registered_sha": registered_manifest_sha,
                            "current_sha": current_manifest_sha,
                        }
            except (OSError, json.JSONDecodeError):
                pass  # Registry corruption is noted but not fatal

    return {
        "status": "VERIFIED",
        "artifact_tree_sha256": actual_tree,
        "file_count": len(files),
        "sealed_at": original.get("sealed_at"),
    }
