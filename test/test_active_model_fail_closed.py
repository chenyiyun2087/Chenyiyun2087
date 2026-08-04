"""Active B-signal model fail-closed tests (v5.4.1 evidence repair).

Production mode MUST never scan the model directory when active_model.json
is missing or invalid — it must raise ModelActivationBlocked.  Directory
scanning is a research-only, explicitly-opted-in fallback.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/Volumes/extension/projects/Chenyiyun2087")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.bs_model_infer import (  # noqa: E402
    ModelActivationBlocked,
    latest_model_path,
    load_latest_bs_model,
)


def _make_root(tmp: str) -> Path:
    root = Path(tmp)
    model_dir = root / "20260701_222545_255922"
    model_dir.mkdir()
    (model_dir / "random_forest_hit_20_10pct.joblib").write_bytes(b"placeholder")
    return root


def test_missing_active_manifest_raises_blocked(tmp_path):
    root = _make_root(str(tmp_path))
    # A newer model exists in the directory — production must NOT find it.
    with pytest.raises(ModelActivationBlocked):
        latest_model_path(root, "hit_20_10pct")


def test_invalid_active_manifest_raises_blocked(tmp_path):
    root = _make_root(str(tmp_path))
    (root / "active_model.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelActivationBlocked):
        latest_model_path(root, "hit_20_10pct")


def test_manifest_pointer_to_missing_model_raises_blocked(tmp_path):
    root = _make_root(str(tmp_path))
    (root / "active_model.json").write_text(json.dumps({
        "target": "hit_20_10pct",
        "model_path": str(root / "20260701_222545_255922" / "nonexistent.joblib"),
    }), encoding="utf-8")
    with pytest.raises(ModelActivationBlocked):
        latest_model_path(root, "hit_20_10pct")


def test_valid_manifest_returns_pointer(tmp_path):
    root = _make_root(str(tmp_path))
    active = root / "20260701_222545_255922" / "random_forest_hit_20_10pct.joblib"
    (root / "active_model.json").write_text(json.dumps({
        "target": "hit_20_10pct",
        "model_path": str(active),
    }), encoding="utf-8")
    assert latest_model_path(root, "hit_20_10pct") == active


def test_research_mode_may_scan_directory(tmp_path):
    root = _make_root(str(tmp_path))
    found = latest_model_path(root, "hit_20_10pct", research_mode=True)
    assert found is not None
    assert found.name == "random_forest_hit_20_10pct.joblib"


def test_load_raises_blocked_in_production(tmp_path):
    root = _make_root(str(tmp_path))
    with pytest.raises(ModelActivationBlocked):
        load_latest_bs_model(model_root=root, target="hit_20_10pct")
