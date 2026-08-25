"""Signal Package atomic-write + revision tests (v5.5.1 — no DB).

Covers:
  - atomicity: a sealed package appears whole or not at all; a failure
    mid-write leaves NO partial package and NO staging residue
  - revisions: next_revision_dir never overwrites; corrections carry
    parent_package_sha256 + revision + reason in their manifest
  - immutability: a SEALED package is never overwritten
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    PACKAGES_ROOT,
    PackageSealedError,
    SignalPackageBlocked,
    next_revision_dir,
    seal_signal_package,
    verify_package_sha,
)
import scripts.ops.build_daily_alpha_signal_package as package_builder  # noqa: E402

CLEAN_GIT = {"git_commit_sha": "test-sha", "worktree_clean": True}


def _payloads() -> dict:
    universe = pd.DataFrame({"symbol": ["600000"], "tradeable": [True]})
    scores = pd.DataFrame({"symbol": ["600000"], "score": [1.0]})
    factors = pd.DataFrame({"symbol": ["600000"], "score": [1.0]})
    portfolios = {"C1": pd.DataFrame(
        {"symbol": ["600000"], "target_weight": [1.0], "rank": [1]})}
    return {
        "signal_date": "2026-08-05",
        "execution_date": "2026-08-06",
        "universe": universe,
        "factor_values": factors,
        "scores": scores,
        "target_portfolios": portfolios,
        "data_quality": {"rows": 1},
        "input_manifest": {"source_snapshot_shas": {"market": "x"},
                           "pit_contract_sha": "y"},
    }


def _dir_sha(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p.glob("*")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def test_seal_writes_whole_package_atomically(tmp_path):
    pkg_dir = tmp_path / "2026-08-05"
    manifest = seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)
    assert pkg_dir.exists()
    assert verify_package_sha(pkg_dir)["ok"]
    assert manifest["package_status"] == "SEALED"
    assert manifest["revision"] == 1
    # No staging residue anywhere near the package.
    assert not list(pkg_dir.parent.glob(".staging/*"))
    assert not list(PACKAGES_ROOT.glob(".staging/*"))


def test_failed_write_leaves_no_partial_package(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "2026-08-05"
    payloads = _payloads()

    def _boom(*a, **k):
        raise RuntimeError("disk failure mid-write")

    monkeypatch.setattr("pandas.DataFrame.to_parquet", _boom)
    with pytest.raises(RuntimeError, match="disk failure"):
        seal_signal_package(pkg_dir, **payloads, git_info=CLEAN_GIT)
    # Nothing at the target, nothing left in staging.
    assert not pkg_dir.exists()
    assert not list(pkg_dir.parent.glob(".staging/*"))


def test_seal_avoids_cross_volume_staging_symlink(tmp_path, monkeypatch):
    """A release's shared ``.staging`` link must not feed an atomic rename."""
    pkg_parent = tmp_path / "packages"
    pkg_parent.mkdir()
    foreign_staging = tmp_path / "persistent-volume-staging"
    foreign_staging.mkdir()
    (pkg_parent / ".staging").symlink_to(foreign_staging, target_is_directory=True)
    pkg_dir = pkg_parent / "2026-08-05"

    renamed = []
    real_rename = os.rename

    def capture_rename(source, target):
        renamed.append((Path(source), Path(target)))
        return real_rename(source, target)

    monkeypatch.setattr(package_builder.os, "rename", capture_rename)
    seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)

    assert renamed
    assert renamed[0][0].resolve().parent != foreign_staging.resolve()
    assert pkg_dir.joinpath("signal_package_manifest.json").exists()
    assert not list(foreign_staging.iterdir())


def test_sealed_package_never_overwritten(tmp_path):
    pkg_dir = tmp_path / "2026-08-05"
    seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)
    before = _dir_sha(pkg_dir)
    with pytest.raises(PackageSealedError):
        seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)
    assert _dir_sha(pkg_dir) == before, "SEALED package content changed"


def test_next_revision_dir_never_collides(tmp_path):
    pkg = tmp_path / "2026-08-05"
    assert next_revision_dir(pkg) == pkg / "revision_2"
    # Simulate revision_2 existing -> next must be revision_3.
    (pkg / "revision_2").mkdir(parents=True)
    assert next_revision_dir(pkg) == pkg / "revision_3"
    # revision_2 AND revision_4 present -> max+1, no reuse.
    (pkg / "revision_4").mkdir()
    assert next_revision_dir(pkg) == pkg / "revision_5"


def test_correction_writes_next_revision_with_parent_sha(tmp_path):
    pkg_dir = tmp_path / "2026-08-05"
    first = seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)
    root_sha = json.loads((pkg_dir / "package_sha256.json").read_text()) \
        ["package_sha256"]
    rev = seal_signal_package(
        pkg_dir, **_payloads(), git_info=CLEAN_GIT,
        allow_revision=True, revision_reason="C3 residualization fix")
    rev_manifest = json.loads(
        (pkg_dir / "revision_2" / "signal_package_manifest.json").read_text())
    assert rev_manifest["revision"] == 2
    assert rev_manifest["parent_package_sha256"] == root_sha
    assert rev_manifest["revision_reason"] == "C3 residualization fix"
    assert rev["revision"] == 2
    # Root package untouched; revision has its own full identity.
    assert verify_package_sha(pkg_dir / "revision_2")["ok"]
    assert first["revision"] == 1


def test_second_correction_lands_in_revision_3(tmp_path):
    pkg_dir = tmp_path / "2026-08-05"
    seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT)
    seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT,
                        allow_revision=True, revision_reason="fix #1")
    seal_signal_package(pkg_dir, **_payloads(), git_info=CLEAN_GIT,
                        allow_revision=True, revision_reason="fix #2")
    assert (pkg_dir / "revision_2").exists()
    assert (pkg_dir / "revision_3").exists()
    assert not (pkg_dir / "revision_4").exists()
    m3 = json.loads(
        (pkg_dir / "revision_3" / "signal_package_manifest.json").read_text())
    assert m3["revision"] == 3
    assert m3["revision_reason"] == "fix #2"


def test_dirty_worktree_blocks_seal(tmp_path):
    with pytest.raises(SignalPackageBlocked, match="dirty"):
        seal_signal_package(tmp_path / "2026-08-05", **_payloads(),
                            git_info={"git_commit_sha": "x",
                                      "worktree_clean": False})
