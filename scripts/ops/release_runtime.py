"""Resolve and export the immutable runtime release selected by launchd."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RELEASE_MANIFEST = Path(
    "/Users/chenyiyun/Library/Application Support/Chenyiyun2087/production_release.json"
)
RUNTIME_STATUS_EXCLUDES = (
    ":(exclude)exports/**",
    ":(exclude)reports/**",
    ":(exclude)data/pit/**",
    ":(exclude)data/pit",
    ":(exclude)logs/score_backfill/**",
    ":(exclude)logs/score_backfill",
    ":(exclude)logs/web/**",
    ":(exclude)logs/web",
    ":(exclude)sina/bs_detection/SinaAppBS/**",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class RuntimeRelease:
    release_id: str
    commit_sha: str
    project_root: Path
    runtime_python: Path
    manifest_path: Path
    source_repo: Path


def release_manifest_path(value: str | os.PathLike[str] | None = None) -> Path:
    raw = value or os.environ.get("CHENYIYUN_RELEASE_MANIFEST")
    return Path(raw).expanduser() if raw else DEFAULT_RELEASE_MANIFEST


def load_runtime_release(
    value: str | os.PathLike[str] | None = None,
) -> RuntimeRelease:
    manifest_path = release_manifest_path(value).resolve()
    if not manifest_path.is_file():
        raise RuntimeError(f"release_manifest_missing:{manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"release_manifest_invalid:{manifest_path}:{type(exc).__name__}") from exc

    release_id = str(payload.get("release_id") or "").strip()
    commit_sha = str(payload.get("commit_sha") or "").strip().lower()
    project_root_raw = str(payload.get("project_root") or "").strip()
    runtime_python_raw = str(payload.get("runtime_python") or "").strip()
    source_repo_raw = str(payload.get("source_repo") or "").strip()
    if not release_id or not _RELEASE_ID_RE.fullmatch(release_id):
        raise RuntimeError("release_manifest_invalid:release_id")
    if not _SHA_RE.fullmatch(commit_sha):
        raise RuntimeError("release_manifest_invalid:commit_sha")
    if not project_root_raw or not runtime_python_raw or not source_repo_raw:
        raise RuntimeError("release_manifest_invalid:missing_paths")

    project_root = Path(project_root_raw).expanduser().resolve()
    # Keep a venv launcher path intact. Resolving its symlink would silently
    # bypass the venv and drop packages installed only in that runtime.
    runtime_python = Path(runtime_python_raw).expanduser()
    source_repo = Path(source_repo_raw).expanduser().resolve()
    if not project_root.is_dir():
        raise RuntimeError(f"release_project_missing:{project_root}")
    if not runtime_python.is_file():
        raise RuntimeError(f"release_python_missing:{runtime_python}")
    if not source_repo.is_dir():
        raise RuntimeError(f"release_source_repo_missing:{source_repo}")

    return RuntimeRelease(
        release_id=release_id,
        commit_sha=commit_sha,
        project_root=project_root,
        runtime_python=runtime_python,
        manifest_path=manifest_path,
        source_repo=source_repo,
    )


def apply_runtime_release_environment(
    environ: dict[str, str] | None = None,
) -> RuntimeRelease:
    """Load the release manifest and put only non-secret identity in env."""
    target = environ if environ is not None else os.environ
    release = load_runtime_release(target.get("CHENYIYUN_RELEASE_MANIFEST"))
    target["CHENYIYUN_PROJECT_ROOT"] = str(release.project_root)
    target["CHENYIYUN_RUNTIME_RELEASE_ID"] = release.release_id
    target["CHENYIYUN_RELEASE_SHA"] = release.commit_sha
    target["CHENYIYUN_RUNTIME_PYTHON"] = str(release.runtime_python)
    target["CHENYIYUN_RELEASE_MANIFEST"] = str(release.manifest_path)
    target["CHENYIYUN_REQUIRE_RELEASE"] = "1"
    return release
