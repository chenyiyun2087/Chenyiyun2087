from pathlib import Path
import shutil
import subprocess

import pytest

from runtime.formal_evidence_binding import check_clean_worktree
from scripts.ops import build_daily_alpha_signal_package as package
from scripts.ops.deploy_production_release import CANONICAL_RUNTIME_ROOTS


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


@pytest.fixture
def shared_release(tmp_path, monkeypatch):
    root = tmp_path / 'release'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'test')
    git(root, 'config', 'user.email', 'test@example.com')
    (root / 'tracked.py').write_text('original source\n')
    (root / 'config').mkdir()
    (root / 'config/runtime.yaml').write_text('enabled: true\n')
    for relative in CANONICAL_RUNTIME_ROOTS:
        path = root / relative
        path.mkdir(parents=True)
        (path / 'historical.dat').write_bytes(b'tracked checkout seed')
    git(root, 'add', '.')
    git(root, 'commit', '-q', '-m', 'release')
    sha = git(root, 'rev-parse', 'HEAD')
    for relative in CANONICAL_RUNTIME_ROOTS:
        path = root / relative
        destination = tmp_path / 'persistent' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), destination)
        path.symlink_to(destination, target_is_directory=True)
        (path / 'new-day.dat').write_bytes(b'new runtime evidence')
    monkeypatch.setattr(package, 'PROJECT_ROOT', root)
    monkeypatch.setenv('CHENYIYUN_RELEASE_SHA', sha)
    assert git(root, 'status', '--porcelain')
    return root, sha


def test_shared_generated_roots_pass_both_code_gates(shared_release):
    root, sha = shared_release
    assert package._git_info()['worktree_clean'] is True
    assert check_clean_worktree(root) == (sha, True)


@pytest.mark.parametrize('path', ['tracked.py', 'config/runtime.yaml', 'untracked.py'])
def test_source_or_config_changes_still_block_both_gates(shared_release, path):
    root, sha = shared_release
    (root / path).write_text('changed\n')
    assert package._git_info()['worktree_clean'] is False
    assert check_clean_worktree(root) == (sha, False)


def test_release_sha_mismatch_still_blocks_shared_runtime(shared_release, monkeypatch):
    monkeypatch.setenv('CHENYIYUN_RELEASE_SHA', '0' * 40)
    info = package._git_info()
    assert info['release_sha_match'] is False
    assert info['worktree_clean'] is False
