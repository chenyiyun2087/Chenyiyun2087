"""Forward Shadow immutability tests (v5.5 contract).

A SEALED Signal Package is never overwritten; manifest SHAs must match
the files; a dirty worktree BLOCKS formal packaging; and the daily
builder NEVER touches historical formal evidence (the old
compute_daily_vls_scores.py appended to formal_scores.parquet — that
path is forbidden in Shadow Engine v2).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.ops.build_daily_alpha_signal_package as pkg_mod  # noqa: E402
from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    REQUIRED_PACKAGE_FILES,
    PackageSealedError,
    SignalPackageBlocked,
    seal_signal_package,
    verify_package_sha,
)

CLEAN_GIT = {"git_commit_sha": "test-sha", "worktree_clean": True}


def _inputs(tmp_path: Path):
    universe = pd.DataFrame({
        "trade_date": ["2026-08-05"] * 4,
        "symbol": ["600001", "600002", "600003", "600004"],
        "is_listed": [1, 1, 1, 1],
        "is_st": [0, 0, 0, 0],
        "is_suspended": [0, 0, 0, 0],
        "limit_status": ["NORMAL"] * 4,
        "security_status_transition": ["NORMAL"] * 4,
        "tradeable": [True, True, True, True],
    })
    factors = pd.DataFrame({
        "trade_date": ["2026-08-05"] * 4,
        "symbol": ["600001", "600002", "600003", "600004"],
        "score": [0.3, 0.2, 0.1, -0.1],
    })
    portfolios = {
        "C1": pd.DataFrame({
            "symbol": ["600001", "600002"],
            "score": [0.3, 0.2], "rank": [1, 2],
            "weight_before_overlay": [0.5, 0.5],
            "target_weight": [0.5, 0.5], "risk_overlay": ["none", "none"],
        }),
    }
    dq = {"signal_date": "2026-08-05", "bar_dates": 30, "bar_rows": 100,
          "mcap_rows": 10, "basic_rows": 10, "industry_rows": 10,
          "label_rows": 10}
    im = {"signal_date": "2026-08-05", "source_snapshot_shas": {},
          "pit_contract_sha": None}
    return universe, factors, portfolios, dq, im


def test_sealed_package_cannot_be_overwritten(tmp_path):
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    pkg = tmp_path / "2026-08-05"
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT)
    # A second seal of the same date must raise — never overwrite.
    with pytest.raises(PackageSealedError):
        seal_signal_package(pkg, signal_date="2026-08-05",
                            execution_date="2026-08-06", universe=universe,
                            factor_values=factors, scores=factors,
                            target_portfolios=portfolios, data_quality=dq,
                            input_manifest=im, git_info=CLEAN_GIT)


def test_revision_2_created_beside_original(tmp_path):
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    pkg = tmp_path / "2026-08-05"
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT)
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT,
                        allow_revision=True)
    assert (pkg / "signal_package_manifest.json").exists()       # original
    assert (pkg / "revision_2" / "signal_package_manifest.json").exists()
    # Original untouched: manifest still says SEALED with the same sha.
    orig = json.loads((pkg / "signal_package_manifest.json").read_text())
    rev = json.loads((pkg / "revision_2" / "signal_package_manifest.json").read_text())
    assert orig["package_status"] == "SEALED"
    assert orig["scores_sha"] == rev["scores_sha"]  # same payload, separate dir


def test_manifest_sha_matches_files(tmp_path):
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    pkg = tmp_path / "2026-08-05"
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT)
    check = verify_package_sha(pkg)
    assert check["ok"], check["errors"]
    # Tampering with a payload must be detected.
    (pkg / "scores.parquet").write_bytes(b"tampered")
    check2 = verify_package_sha(pkg)
    assert not check2["ok"]


def test_required_files_all_created(tmp_path):
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    pkg = tmp_path / "2026-08-05"
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT)
    for fname in REQUIRED_PACKAGE_FILES:
        assert (pkg / fname).exists(), f"missing {fname}"


def test_dirty_worktree_blocks_package(tmp_path):
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    pkg = tmp_path / "2026-08-05"
    dirty = {"git_commit_sha": "test-sha", "worktree_clean": False}
    with pytest.raises(SignalPackageBlocked, match="worktree is dirty"):
        seal_signal_package(pkg, signal_date="2026-08-05",
                            execution_date="2026-08-06", universe=universe,
                            factor_values=factors, scores=factors,
                            target_portfolios=portfolios, data_quality=dq,
                            input_manifest=im, git_info=dirty)
    assert not (pkg / "signal_package_manifest.json").exists()


def _init_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@test"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "test"], check=True)
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)


def test_git_info_ignores_untracked_evidence_exports(tmp_path, monkeypatch):
    """The production worktree gate excludes exports/** and reports/**
    (generated evidence, never committed under the parquet-bloat policy).
    Without this, a shadow day would be permanently BLOCKED by the very
    evidence the pipeline itself produces."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr(pkg_mod, "PROJECT_ROOT", tmp_path)
    exports = tmp_path / "exports" / "forward_shadow_evidence" / "packages"
    exports.mkdir(parents=True)
    (exports / "universe.parquet").write_bytes(b"evidence")
    reports = tmp_path / "reports" / "daily"
    reports.mkdir(parents=True)
    (reports / "nav.csv").write_text("date,nav\n", encoding="utf-8")
    info = pkg_mod._git_info()
    assert info["worktree_clean"] is True


def test_git_info_blocks_untracked_source(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr(pkg_mod, "PROJECT_ROOT", tmp_path)
    (tmp_path / "new_source.py").write_text("y = 2\n", encoding="utf-8")
    info = pkg_mod._git_info()
    assert info["worktree_clean"] is False


def test_package_builder_never_touches_historical_evidence(tmp_path):
    """The v5.5 builder must not modify historical formal scores —
    the pre-v5.5 pipeline appended to formal_scores.parquet."""
    universe, factors, portfolios, dq, im = _inputs(tmp_path)
    historical = tmp_path / "historical" / "f1_no_value" / "scores"
    historical.mkdir(parents=True)
    sentinel = historical / "formal_scores.parquet"
    sentinel.write_bytes(b"immutable-historical")
    pkg = tmp_path / "2026-08-05"
    seal_signal_package(pkg, signal_date="2026-08-05",
                        execution_date="2026-08-06", universe=universe,
                        factor_values=factors, scores=factors,
                        target_portfolios=portfolios, data_quality=dq,
                        input_manifest=im, git_info=CLEAN_GIT)
    assert sentinel.read_bytes() == b"immutable-historical", (
        "historical formal scores were modified by the package builder")
    # And nothing was written under the historical zone.
    new_files = [str(p.relative_to(tmp_path)) for p in historical.rglob("*")]
    assert new_files == ["historical/f1_no_value/scores/formal_scores.parquet"]
