#!/usr/bin/env python3
"""Create a clean production worktree and atomically publish its identity."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.ops.release_runtime import DEFAULT_RELEASE_MANIFEST


DEFAULT_RELEASE_ROOT = Path("/Volumes/extension/runtime/Chenyiyun2087/releases")
SHARED_RUNTIME_ROOTS = (
    "exports",
    "sina/bs_detection/SinaAppBS",
    "data",
    "logs",
)
GIT_STATUS_PATHS = (".", ":(exclude)exports/**", ":(exclude)reports/**")


def _git(source_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=source_root, text=True, stderr=subprocess.STDOUT
    ).strip()


def _tracked_paths(source_root: Path) -> set[str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=source_root, stderr=subprocess.STDOUT
    )
    return {item.decode("utf-8") for item in raw.split(b"\0") if item}


def _has_tracked_descendant(relative: str, tracked: set[str]) -> bool:
    prefix = relative.rstrip("/") + "/"
    return any(path == relative or path.startswith(prefix) for path in tracked)


def _share_untracked_subdirectories(
    source_root: Path,
    release_root: Path,
    relative: str,
    tracked: set[str],
) -> None:
    """Share generated directories without replacing tracked release files."""
    source = source_root / relative
    target = release_root / relative
    if not source.exists():
        return
    if target.is_symlink():
        if target.resolve() != source.resolve():
            raise RuntimeError(f"release_runtime_path_conflict:{target}")
        return
    if not target.exists():
        if not _has_tracked_descendant(relative, tracked):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
            return
        target.mkdir(parents=True)
    if not target.is_dir():
        raise RuntimeError(f"release_runtime_path_not_directory:{target}")
    for child in sorted(source.iterdir()):
        if child.is_dir() and not child.is_symlink():
            child_relative = f"{relative}/{child.name}"
            _share_untracked_subdirectories(
                source_root, release_root, child_relative, tracked
            )


def _ensure_worktree(
    source_root: Path,
    target: Path,
    commit_sha: str,
) -> None:
    if target.exists():
        try:
            target_root = Path(_git(target, "rev-parse", "--show-toplevel")).resolve()
            target_sha = _git(target, "rev-parse", "HEAD").lower()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"release_target_not_git_worktree:{target}") from exc
        if target_root != target.resolve():
            raise RuntimeError(f"release_target_root_mismatch:{target_root}; expected:{target}")
        if target_sha != commit_sha:
            raise RuntimeError(
                f"release_target_commit_mismatch:{target_sha}; expected:{commit_sha}"
            )
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(target), commit_sha],
        cwd=source_root,
        check=True,
    )


def _write_release_manifest(
    path: Path,
    *,
    release_id: str,
    commit_sha: str,
    project_root: Path,
    runtime_python: Path,
    source_repo: Path,
) -> None:
    payload = {
        "schema_version": "production_runtime_release_v1",
        "release_id": release_id,
        "commit_sha": commit_sha,
        "project_root": str(project_root),
        "runtime_python": str(runtime_python),
        "source_repo": str(source_repo),
        "worktree_clean": True,
        "shared_runtime_roots": list(SHARED_RUNTIME_ROOTS),
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a clean production runtime release.")
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--release-id")
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_RELEASE_MANIFEST)
    parser.add_argument("--runtime-python", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_root = args.source_root.expanduser().resolve()
    commit_sha = _git(source_root, "rev-parse", "HEAD").lower()
    status = _git(
        source_root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *GIT_STATUS_PATHS,
    )
    if status:
        raise SystemExit(
            "FATAL: source checkout has tracked/runtime changes; commit the release first."
        )
    release_id = args.release_id or f"chenyiyun-prod-{commit_sha[:12]}"
    runtime_python = (args.runtime_python or source_root / ".venv" / "bin" / "python").expanduser().resolve()
    if not runtime_python.is_file():
        raise SystemExit(f"FATAL: runtime Python is missing: {runtime_python}")
    target = (args.release_root.expanduser().resolve() / f"{release_id}-{commit_sha[:12]}")
    manifest = args.manifest.expanduser().resolve()

    plan = {
        "release_id": release_id,
        "commit_sha": commit_sha,
        "project_root": str(target),
        "runtime_python": str(runtime_python),
        "manifest": str(manifest),
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **plan}, ensure_ascii=False, indent=2))
        return

    _ensure_worktree(source_root, target, commit_sha)
    tracked = _tracked_paths(source_root)
    for relative in SHARED_RUNTIME_ROOTS:
        _share_untracked_subdirectories(source_root, target, relative, tracked)
    _write_release_manifest(
        manifest,
        release_id=release_id,
        commit_sha=commit_sha,
        project_root=target,
        runtime_python=runtime_python,
        source_repo=source_root,
    )
    print(json.dumps({"published": True, **plan}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
