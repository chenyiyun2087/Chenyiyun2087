from __future__ import annotations

import json

import pytest

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
