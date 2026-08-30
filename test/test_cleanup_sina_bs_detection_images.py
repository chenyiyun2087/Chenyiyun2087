from __future__ import annotations

from datetime import date

from scripts.ops.cleanup_sina_bs_detection_images import (
    collect_previous_week_image_dirs,
    run_cleanup,
)


def test_collect_previous_week_resolves_release_symlink(tmp_path):
    source_root = tmp_path / "source" / "config_1"
    release_root = tmp_path / "release" / "config_1"
    source_root.mkdir(parents=True)
    release_root.mkdir(parents=True)
    real_dir = source_root / "20260817"
    real_dir.mkdir()
    (real_dir / "image.png").write_bytes(b"x")
    (release_root / "20260817").symlink_to(real_dir, target_is_directory=True)

    start, end, dirs = collect_previous_week_image_dirs(release_root, date(2026, 8, 28))

    assert (start, end) == (date(2026, 8, 17), date(2026, 8, 23))
    assert dirs == [real_dir.resolve()]


def test_cleanup_uses_persistent_source_root_from_environment(tmp_path, monkeypatch):
    source_root = tmp_path / "source" / "sina" / "bs_detection" / "SinaAppBS" / "config_1"
    source_root.mkdir(parents=True)
    target = source_root / "20260817"
    target.mkdir()
    (target / "image.png").write_bytes(b"x")
    monkeypatch.setenv("CHENYIYUN_SOURCE_REPO", str(tmp_path / "source"))

    args = type("Args", (), {
        "date": "20260828",
        "root": None,
        "execute": True,
        "friday_only": True,
    })()
    result = run_cleanup(args)

    assert result["deleted_count"] == 1
    assert not target.exists()
