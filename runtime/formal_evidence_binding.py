"""Shared formal evidence binding and validation primitives.

Used by the formal readiness preflight, immutable formal runner, and
backtest engine to enforce a single, auditable evidence chain without
duplicating SHA / worktree / freeze logic across scripts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

try:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
except NameError:
    PROJECT_ROOT = Path.cwd()


# ---------------------------------------------------------------------------
# Re-usable dataclass (mirrors formal_readiness_preflight.Check so callers
# can construct consistent check records without a circular import).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    check: str
    passed: bool
    actual: str
    required: str


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    """SHA-256 of a single file (streaming, 1 MiB chunks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a JSON-serialisable dict."""
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Worktree cleanliness
# ---------------------------------------------------------------------------


def check_clean_worktree(project_root: Path | None = None) -> tuple[str, bool]:
    """Return (git_commit_sha, is_clean).

    A *clean* worktree has no modified tracked files and no untracked
    files (excluding ``exports/`` and ``reports/``).  The function is
    fail-closed: any OSError / CalledProcessError makes ``is_clean=False``.
    """
    root = project_root or PROJECT_ROOT
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        status = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                ".",
                ":(exclude)exports/**",
                ":(exclude)reports/**",
            ],
            cwd=root,
            text=True,
        )
        dirty = bool(status.strip())
        return sha, not dirty
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN", False


def head_unchanged(before_sha: str, project_root: Path | None = None) -> bool:
    """True when **HEAD** has not moved since *before_sha* was captured."""
    root = project_root or PROJECT_ROOT
    try:
        current = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        return current == before_sha
    except (OSError, subprocess.CalledProcessError):
        return False


# ---------------------------------------------------------------------------
# Package reality (anti-symlink)
# ---------------------------------------------------------------------------


def validate_package_reality(package: Path) -> bool:
    """True when the package path is a real directory, not a symlink chain."""
    try:
        resolved = package.resolve()
        return resolved == Path(os.path.abspath(str(package))) and resolved.is_dir()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Input freezing
# ---------------------------------------------------------------------------


def _csv_metadata(path: Path) -> dict[str, Any]:
    """Per-file metadata for a CSV file."""
    try:
        frame = pd.read_csv(path, dtype={"symbol": str, "ts_code": str})
    except Exception:
        return {"rows": 0, "coverage_start": None, "coverage_end": None}

    rows = len(frame)
    coverage_start: str | None = None
    coverage_end: str | None = None
    for col in ("trade_date", "cal_date", "effective_date"):
        if col in frame.columns:
            dates = pd.to_datetime(frame[col], errors="coerce").dropna()
            if not dates.empty:
                coverage_start = dates.min().strftime("%Y-%m-%d")
                coverage_end = dates.max().strftime("%Y-%m-%d")
            break
    return {"rows": rows, "coverage_start": coverage_start, "coverage_end": coverage_end}


def freeze_inputs(
    package: Path,
    frozen_dir: Path,
    required_objects: list[str],
) -> dict[str, dict[str, Any]]:
    """Copy every *required_object* from *package* into *frozen_dir*.

    Returns a per-filename metadata dict suitable for
    ``formal_run_manifest.json → input_objects``.
    """
    frozen_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, dict[str, Any]] = {}

    for filename in required_objects:
        src = package / filename
        if not src.is_file():
            raise FileNotFoundError(f"required object missing from package: {filename}")
        dst = frozen_dir / filename
        shutil.copy2(src, dst)
        # Read-only for owner / group / others
        dst.chmod(
            stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        )
        sha = _sha(dst)
        info: dict[str, Any] = {
            "sha256": sha,
            "bytes": dst.stat().st_size,
        }
        if filename.endswith(".csv"):
            info.update(_csv_metadata(dst))
        metadata[filename] = info

    return metadata


def compute_frozen_bundle_sha(metadata: dict[str, dict[str, Any]]) -> str:
    """Deterministic SHA-256 over the per-file metadata of the frozen bundle."""
    # Sort keys so the hash is reproducible
    canonical = {
        filename: {k: meta[k] for k in sorted(meta)}
        for filename, meta in sorted(metadata.items())
    }
    return canonical_sha(canonical)


def verify_frozen_inputs(
    frozen_dir: Path,
    expected_metadata: dict[str, dict[str, Any]],
) -> list[Check]:
    """Re-verify every file in *expected_metadata* against disk.

    Returns a list of :class:`Check` records — empty list means all clear.
    """
    checks: list[Check] = []
    for filename, expected in sorted(expected_metadata.items()):
        target = frozen_dir / filename
        if not target.is_file():
            checks.append(
                Check(
                    f"frozen_object_missing:{filename}",
                    False,
                    "MISSING",
                    expected.get("sha256", "present"),
                )
            )
            continue
        actual_sha = _sha(target)
        expected_sha = str(expected.get("sha256") or "")
        checks.append(
            Check(
                f"frozen_object_sha:{filename}",
                actual_sha == expected_sha,
                actual_sha,
                expected_sha,
            )
        )
        # Verify read-only
        mode = target.stat().st_mode
        writable = bool(mode & stat.S_IWUSR or mode & stat.S_IWGRP or mode & stat.S_IWOTH)
        checks.append(
            Check(
                f"frozen_object_readonly:{filename}",
                not writable,
                "writable" if writable else "read-only",
                "read-only",
            )
        )
    return checks


# ---------------------------------------------------------------------------
# Formal Run ID
# ---------------------------------------------------------------------------


def compute_formal_run_id(
    evidence_sha256: str,
    git_sha: str,
    acceptance_config_sha: str,
    readiness_config_sha: str,
    frozen_bundle_sha: str,
    start_date: str,
    end_date: str,
    strategy_ids: list[str],
    formal_pit_run_id: str = "",
    package_id: str = "",
    admission_id: str = "",
    pr_b_sha256: str = "",
) -> str:
    """Produce a deterministic, content-addressed formal run identifier.

    The id binds **all** inputs — evidence, code, config, frozen data,
    strategy set, and date range.  The result is a filesystem-safe string:
    ``formal-<40-chars-composite-hex>``.
    """
    composite = canonical_sha(
        {
            "evidence_sha256": evidence_sha256,
            "git_sha": git_sha,
            "acceptance_config_sha": acceptance_config_sha,
            "readiness_config_sha": readiness_config_sha,
            "frozen_bundle_sha": frozen_bundle_sha,
            "start_date": start_date,
            "end_date": end_date,
            "strategy_ids": sorted(strategy_ids),
            "formal_pit_run_id": formal_pit_run_id,
            "package_id": package_id,
            "admission_id": admission_id,
            "pr_b_sha256": pr_b_sha256,
        }
    )
    # Keep the run-id filesystem-friendly — full 64-char hex, not a prefix.
    return f"formal-{composite}"
