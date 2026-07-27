from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research.run_immutable_formal_backtest import (
    FORMAL_STRATEGIES,
    run,
)


def _touch_package(root: Path) -> None:
    root.mkdir()
    for name in (
        "scores.csv",
        "prices.csv",
        "strict_corporate_actions.csv",
        "strict_security_lifecycle.csv",
        "strict_snapshot_manifest.json",
        "trade_calendar.csv",
    ):
        (root / name).write_text("fixture\n", encoding="utf-8")
    (root / "source_manifest.json").write_text("{}", encoding="utf-8")


def test_blocked_preflight_never_starts_formal_run(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "evidence_sha256": "a" * 64,
                "package": str(package),
            }
        ),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight,
        package=package,
        output_root=tmp_path / "runs",
        end_date="2026-07-24",
        dry_run=False,
    )
    assert result["status"] == "BLOCKED"
    assert result["formal_run_started"] is False


def test_ready_preflight_creates_one_immutable_dry_run(tmp_path):
    package = tmp_path / "package"
    _touch_package(package)
    evidence_sha = hashlib.sha256(b"package").hexdigest()
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "status": "READY_FOR_FORMAL_RUN",
                "evidence_sha256": evidence_sha,
                "package": str(package),
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "runs"
    result = run(
        preflight=preflight,
        package=package,
        output_root=output,
        end_date="2026-07-24",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"
    assert tuple(result["strategy_ids"]) == FORMAL_STRATEGIES
    assert result["cost_rate_one_way"] == 0.00075
    assert result["slippage_bps_one_way"] == 10
    assert "--trade-calendar-snapshot" in result["command"]
    with pytest.raises(FileExistsError, match="immutable_formal_run_exists"):
        run(
            preflight=preflight,
            package=package,
            output_root=output,
            end_date="2026-07-24",
            dry_run=True,
        )
