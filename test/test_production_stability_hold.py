from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from runtime.production_stability_hold import (
    ProductionUpgradePaused,
    assert_production_upgrade_allowed,
    load_production_stability_hold,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_hold(path, **overrides):
    payload = {
        "schema_version": "production_stability_hold_v1",
        "status": "PAUSED",
        "activated_at": "2026-09-02T00:00:00+08:00",
        "reason": "stability audit",
        "scope": ["production_release_publish"],
        "allow_stability_hotfix": True,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_active_hold_blocks_normal_production_upgrade(tmp_path):
    hold = tmp_path / "hold.json"
    _write_hold(hold)

    with pytest.raises(ProductionUpgradePaused, match="production_upgrade_paused"):
        assert_production_upgrade_allowed(hold)


def test_active_hold_allows_explicit_stability_hotfix(tmp_path):
    hold = tmp_path / "hold.json"
    _write_hold(hold)

    loaded = assert_production_upgrade_allowed(hold, stability_hotfix=True)

    assert loaded is not None
    assert loaded.allow_stability_hotfix is True


def test_missing_or_resumed_hold_does_not_block(tmp_path):
    hold = tmp_path / "hold.json"
    assert load_production_stability_hold(hold) is None

    _write_hold(hold, status="RESUMED")
    assert assert_production_upgrade_allowed(hold) is not None


def test_malformed_hold_fails_closed(tmp_path):
    hold = tmp_path / "hold.json"
    hold.write_text("{bad", encoding="utf-8")

    with pytest.raises(ProductionUpgradePaused, match="hold_invalid"):
        load_production_stability_hold(hold)


def test_deploy_entrypoint_blocks_normal_dry_run_during_hold(monkeypatch):
    from scripts.ops import deploy_production_release

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_production_release.py",
            "--source-root",
            str(PROJECT_ROOT),
            "--dry-run",
        ],
    )
    with pytest.raises(SystemExit, match="production_upgrade_paused"):
        deploy_production_release.main()


def test_promotion_approval_entrypoint_respects_hold():
    from scripts.ops.strategy_release_registry import record_promotion_approval

    with pytest.raises(ProductionUpgradePaused, match="production_upgrade_paused"):
        record_promotion_approval(
            None,
            1,
            "PROMOTE_CANARY",
            strategy_id="strategy",
            runtime_release_id="release",
            gate_evidence_sha="a" * 64,
            actor="operator",
        )
