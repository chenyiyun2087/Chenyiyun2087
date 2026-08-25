from __future__ import annotations

import json

import pytest

from scripts.ops import deploy_production_release
from scripts.ops import release_runtime


def test_load_runtime_release_validates_identity_and_paths(tmp_path):
    project_root = tmp_path / "release"
    project_root.mkdir()
    runtime_python = tmp_path / "python"
    runtime_python.write_text("", encoding="utf-8")
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "release_id": "chenyiyun-prod-test",
        "commit_sha": "a" * 40,
        "project_root": str(project_root),
        "runtime_python": str(runtime_python),
        "source_repo": str(source_repo),
    }), encoding="utf-8")

    release = release_runtime.load_runtime_release(manifest)

    assert release.release_id == "chenyiyun-prod-test"
    assert release.commit_sha == "a" * 40
    assert release.project_root == project_root.resolve()


def test_load_runtime_release_rejects_bad_commit(tmp_path):
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "release_id": "release",
        "commit_sha": "not-a-sha",
        "project_root": str(tmp_path),
        "runtime_python": str(tmp_path / "python"),
        "source_repo": str(tmp_path),
    }), encoding="utf-8")

    with pytest.raises(RuntimeError, match="commit_sha"):
        release_runtime.load_runtime_release(manifest)


def test_load_runtime_release_preserves_venv_python_path(tmp_path):
    project_root = tmp_path / "release"
    project_root.mkdir()
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    base_python = tmp_path / "base-python"
    base_python.write_text("", encoding="utf-8")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "release_id": "chenyiyun-prod-venv",
        "commit_sha": "b" * 40,
        "project_root": str(project_root),
        "runtime_python": str(venv_python),
        "source_repo": str(source_repo),
    }), encoding="utf-8")

    release = release_runtime.load_runtime_release(manifest)

    assert release.runtime_python == venv_python


def test_apply_runtime_release_exports_persistent_source_root(tmp_path):
    project_root = tmp_path / "release"
    project_root.mkdir()
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    runtime_python = tmp_path / "python"
    runtime_python.write_text("", encoding="utf-8")
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "release_id": "chenyiyun-prod-source-root",
        "commit_sha": "c" * 40,
        "project_root": str(project_root),
        "runtime_python": str(runtime_python),
        "source_repo": str(source_repo),
    }), encoding="utf-8")

    env = {"CHENYIYUN_RELEASE_MANIFEST": str(manifest)}
    release_runtime.apply_runtime_release_environment(env)

    assert env["CHENYIYUN_SOURCE_REPO"] == str(source_repo.resolve())


def test_release_sharing_skips_forward_package_staging_dirs(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "release"
    package_root = source / "exports" / "forward_shadow_evidence" / "packages"
    (package_root / "2026-08-04").mkdir(parents=True)
    (package_root / "2026-08-04" / "classification.json").write_text("{}")
    (package_root / ".staging" / "partial").mkdir(parents=True)
    (source / "exports" / "forward_shadow_evidence" / "README.md").write_text("evidence")

    tracked = {
        "exports/forward_shadow_evidence/README.md",
        "exports/forward_shadow_evidence/packages/2026-08-04/classification.json",
    }
    deploy_production_release._share_untracked_subdirectories(
        source, target, "exports", tracked
    )

    assert not (target / "exports" / "forward_shadow_evidence" /
                "packages" / ".staging").exists()
