"""PR-H0 tests for runtime/formal_evidence_binding.py."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from runtime.formal_evidence_binding import (
    Check,
    canonical_sha,
    check_clean_worktree,
    compute_formal_run_id,
    freeze_inputs,
    head_unchanged,
    validate_package_reality,
    verify_frozen_inputs,
)


# ------------------------------------------------------------------
# check_clean_worktree
# ------------------------------------------------------------------


def test_clean_worktree_passes_on_clean_repo():
    """In a temp directory with a git repo (clean), returns clean=True."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, capture_output=True)
        (root / "dummy").write_text("clean\n")
        subprocess.run(["git", "add", "dummy"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
        sha, clean = check_clean_worktree(root)
        assert clean is True
        assert len(sha) == 40


def test_clean_worktree_detects_dirty_repo():
    """Uncommitted file → clean=False."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, capture_output=True)
        (root / "dirty").write_text("untracked\n")
        sha, clean = check_clean_worktree(root)
        assert clean is False


# ------------------------------------------------------------------
# head_unchanged
# ------------------------------------------------------------------


def test_head_unchanged_detects_same_sha():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=root, capture_output=True)
        (root / "f").write_text("x\n")
        subprocess.run(["git", "add", "f"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "x"], cwd=root, capture_output=True)
        current = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        assert head_unchanged(current, root) is True


# ------------------------------------------------------------------
# validate_package_reality
# ------------------------------------------------------------------


def test_validate_package_reality_detects_symlink(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "f").write_text("x")
    link = tmp_path / "link"
    os.symlink(str(real_dir), str(link))
    # Symlink resolves differently than abspath
    assert not validate_package_reality(link)


def test_validate_package_reality_accepts_real_dir(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    assert validate_package_reality(pkg) is True


# ------------------------------------------------------------------
# freeze_inputs + verify_frozen_inputs
# ------------------------------------------------------------------


def test_freeze_inputs_copies_all_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.csv").write_text("col1\n1\n2\n", encoding="utf-8")
    (src / "b.json").write_text('{"k":"v"}', encoding="utf-8")
    (src / "c.csv").write_text("x\n3\n", encoding="utf-8")
    dst = tmp_path / "frozen"
    meta = freeze_inputs(src, dst, ["a.csv", "b.json", "c.csv"])
    assert (dst / "a.csv").is_file()
    assert (dst / "b.json").is_file()
    assert (dst / "c.csv").is_file()
    assert len(meta) == 3
    for name in ("a.csv", "b.json", "c.csv"):
        assert "sha256" in meta[name]
        assert "bytes" in meta[name]


def test_freeze_inputs_sets_readonly(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.csv").write_text("a\n1\n")
    dst = tmp_path / "frozen"
    freeze_inputs(src, dst, ["f.csv"])
    mode = (dst / "f.csv").stat().st_mode
    # Should not be writable by anyone
    assert not (mode & stat.S_IWUSR)
    assert not (mode & stat.S_IWGRP)
    assert not (mode & stat.S_IWOTH)


def test_freeze_inputs_computes_correct_sha(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    content = b"hello\nworld\n"
    (src / "data.csv").write_bytes(content)
    dst = tmp_path / "frozen"
    meta = freeze_inputs(src, dst, ["data.csv"])
    expected = hashlib.sha256(content).hexdigest()
    assert meta["data.csv"]["sha256"] == expected


def test_verify_frozen_inputs_detects_missing_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.csv").write_text("a\n1\n")
    dst = tmp_path / "frozen"
    meta = freeze_inputs(src, dst, ["x.csv"])
    (dst / "x.csv").unlink()
    checks = verify_frozen_inputs(dst, meta)
    assert any(not c.passed and "missing" in c.check for c in checks)


def test_verify_frozen_inputs_detects_sha_mismatch(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "y.csv").write_text("a\n1\n")
    dst = tmp_path / "frozen"
    meta = freeze_inputs(src, dst, ["y.csv"])
    # Make writable before tampering (freeze sets read-only)
    (dst / "y.csv").chmod(0o644)
    (dst / "y.csv").write_text("a\n2\n")  # tamper
    (dst / "y.csv").chmod(0o444)
    checks = verify_frozen_inputs(dst, meta)
    assert any(not c.passed and "sha" in c.check for c in checks)


def test_freezing_missing_file_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "frozen"
    with pytest.raises(FileNotFoundError):
        freeze_inputs(src, dst, ["nonexistent.csv"])


# ------------------------------------------------------------------
# compute_formal_run_id
# ------------------------------------------------------------------


def test_compute_formal_run_id_binds_all_inputs():
    rid1 = compute_formal_run_id(
        evidence_sha256="a" * 64, git_sha="b" * 40,
        acceptance_config_sha="c" * 64,
        readiness_config_sha="d" * 64,
        frozen_bundle_sha="e" * 64,
        start_date="2013-01-01", end_date="2026-07-25",
        strategy_ids=["s1", "s2"],
    )
    assert rid1.startswith("formal-")
    assert len(rid1) > len("formal-") + 16  # not just a prefix

    # Different input → different run ID
    rid2 = compute_formal_run_id(
        evidence_sha256="z" * 64, git_sha="b" * 40,
        acceptance_config_sha="c" * 64,
        readiness_config_sha="d" * 64,
        frozen_bundle_sha="e" * 64,
        start_date="2013-01-01", end_date="2026-07-25",
        strategy_ids=["s1", "s2"],
    )
    assert rid1 != rid2


# ------------------------------------------------------------------
# canonical_sha
# ------------------------------------------------------------------


def test_canonical_sha_is_deterministic():
    a = canonical_sha({"b": 2, "a": 1})
    b = canonical_sha({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


# ------------------------------------------------------------------
# Check dataclass
# ------------------------------------------------------------------


def test_check_dataclass():
    c = Check("test_check", True, "ok", "should be ok")
    assert c.passed is True
    assert c.check == "test_check"
