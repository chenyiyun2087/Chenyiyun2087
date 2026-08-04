from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research.run_immutable_formal_backtest import (
    FORMAL_STRATEGIES,
    run,
)


import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def _build_valid_package(root: Path) -> str:
    """Build a package that will pass formal readiness re-validation.

    Returns the evidence_sha256 from the preflight computation.
    """
    import yaml

    # Data must end at the config's latest_complete_trade_date, otherwise
    # the data_end_date check (v5.2+) blocks the "valid" fixture.
    config = yaml.safe_load(
        (PROJECT_ROOT / "config" / "formal_readiness.yaml").read_text(encoding="utf-8")
    )
    root.mkdir(parents=True, exist_ok=True)
    date = str(config["latest_complete_trade_date"])
    available = f"{date}T15:00:00+08:00"
    symbols = ["000001", "000002"]

    pd.DataFrame(
        [{"cal_date": date, "exchange": "SSE", "is_open": 1,
          "source": "tushare_stock.dim_trade_cal", "available_at": available}]
    ).to_csv(root / "trade_calendar.csv", index=False)

    pd.DataFrame(
        [{"trade_date": date, "symbol": sym, "is_tradable": 1, "available_at": available}
         for sym in symbols]
    ).to_csv(root / "tradable_universe.csv", index=False)

    pd.DataFrame(
        [{"trade_date": date, "symbol": sym, "strategy": strat, "score": 1.0,
          "available_at": available}
         for strat in (
             "production_governed_vol_position",
             "production_governed_vol_position_v1_2b_dynamic_score",
             "production_governed_vol_position_v1_2b_gate_tuned",
             "production_governed_vol_position_v1_2b_execution_safe_uplift",
             "production_governed_vol_position_v1_2b_strict_precommit_uplift",
         )
         for sym in symbols]
    ).to_csv(root / "scores.csv", index=False)

    pd.DataFrame(
        [{"trade_date": date, "symbol": sym, "open": 10.0, "high": 11.0,
          "low": 9.0, "close": 10.5, "amount": 1_000_000, "available_at": available}
         for sym in symbols]
    ).to_csv(root / "prices.csv", index=False)

    pd.DataFrame(
        [{"trade_date": date, "symbol": sym, "adj_factor": 1.0, "available_at": available}
         for sym in symbols]
    ).to_csv(root / "adjustment_factors.csv", index=False)

    pd.DataFrame(
        [{"event_id": f"{sym}:none", "effective_date": date, "symbol": sym,
          "action_type": "NONE", "source_event_id": f"{sym}:none",
          "as_of_timestamp": available, "source_complete": True,
          "event_hash": "a" * 64, "available_at": available}
         for sym in symbols]
    ).to_csv(root / "strict_corporate_actions.csv", index=False)

    pd.DataFrame(
        [{"symbol": sym, "trade_date": date, "is_listed": 1, "is_suspended": 0,
          "available_at": available}
         for sym in symbols]
    ).to_csv(root / "strict_security_lifecycle.csv", index=False)

    (root / "initial_account.json").write_text(
        json.dumps({"currency": "CNY", "initial_cash_cny": 500_000, "positions": {}}),
        encoding="utf-8",
    )

    strict_manifest = {
        "snapshot_schema_version": "strict_corporate_lifecycle_snapshot_v2",
        "dataset_version": "fixture",
        "generated_at": available,
        "source_sha256": "b" * 64,
        "snapshot_sha256": hashlib.sha256(
            (root / "strict_corporate_actions.csv").read_bytes()
        ).hexdigest(),
        "lifecycle_source_sha256": "c" * 64,
        "lifecycle_snapshot_sha256": hashlib.sha256(
            (root / "strict_security_lifecycle.csv").read_bytes()
        ).hexdigest(),
    }
    (root / "strict_snapshot_manifest.json").write_text(
        json.dumps(strict_manifest), encoding="utf-8"
    )

    object_names = [
        "trade_calendar.csv", "tradable_universe.csv", "scores.csv",
        "prices.csv", "adjustment_factors.csv", "strict_corporate_actions.csv",
        "strict_security_lifecycle.csv", "initial_account.json",
        "strict_snapshot_manifest.json",
    ]
    source_manifest = {
        "calendar_source": "tushare_stock.dim_trade_cal",
        "coverage_start": "2013-01-01",
        "coverage_end": date,
        "corporate_action_complete": True,
        "security_lifecycle_complete": True,
        "objects": {
            name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in object_names
        },
    }
    (root / "source_manifest.json").write_text(
        json.dumps(source_manifest), encoding="utf-8"
    )

    import yaml
    from scripts.research.formal_readiness_preflight import evaluate_package
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "formal_readiness.yaml").read_text()
    )
    result = evaluate_package(root, config)
    return str(result.get("evidence_sha256", ""))


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
        fixture_mode=True,
    )
    assert result["status"] == "BLOCKED"
    assert result["formal_run_started"] is False


def test_ready_preflight_creates_one_immutable_dry_run(tmp_path):
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
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
    # Use fixture_mode to bypass worktree check in test environment
    result = run(
        preflight=preflight,
        package=package,
        output_root=output,
        end_date="2026-07-24",
        dry_run=True,
        fixture_mode=True,
    )
    assert result["status"] == "DRY_RUN"
    # v5.3: strategy_ids are detected from the frozen scores and reported
    # sorted; identity (not order) is the contract.
    assert set(result["strategy_ids"]) == set(FORMAL_STRATEGIES)
    assert result["cost_rate_one_way"] == 0.00075
    assert result["slippage_bps_one_way"] == 10
    assert "--trade-calendar-snapshot" in result["command"]
    assert result["fixture_mode"] is True
    assert result["formally_verified"] is False
    with pytest.raises(FileExistsError, match="immutable_formal_run_exists"):
        run(
            preflight=preflight,
            package=package,
            output_root=output,
            end_date="2026-07-24",
            dry_run=True,
            fixture_mode=True,
        )


# ------------------------------------------------------------------
# PR-H0 new tests
# ------------------------------------------------------------------


def test_formal_runner_revalidates_package_before_run(tmp_path):
    """Runner re-runs evaluate_package and accepts an unmodified package."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    assert evidence_sha, "package must pass preflight"
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    assert result["status"] == "DRY_RUN"
    assert "input_objects" in result


def test_formal_runner_rejects_package_mutated_after_preflight(tmp_path):
    """Package mutation after preflight is detected by re-validation."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    # Mutate: remove a tradable symbol from universe
    universe = pd.read_csv(package / "tradable_universe.csv", dtype={"symbol": str})
    universe = universe[universe["symbol"] != "000002"]
    universe.to_csv(package / "tradable_universe.csv", index=False)
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    # Re-validation should fail
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "FORMAL_INPUT_REVALIDATION_FAILED"


def test_formal_runner_rejects_preflight_sha_mismatch(tmp_path):
    """Preflight file tampered after creation is detected by evidence mismatch."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    payload = {
        "status": "READY_FOR_FORMAL_RUN",
        "evidence_sha256": evidence_sha,
        "package": str(package),
    }
    preflight.write_text(json.dumps(payload), encoding="utf-8")
    # Normal run succeeds
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    assert result["status"] == "DRY_RUN"

    # Now tamper: replace the preflight evidence_sha with a fake one
    tampered = {**payload, "evidence_sha256": "f" * 64}
    preflight.write_text(json.dumps(tampered), encoding="utf-8")
    result2 = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs2", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    # Re-validation detects evidence_sha mismatch
    assert result2["status"] == "BLOCKED"
    assert "EVIDENCE_SHA256_MISMATCH" in result2["reason"]


def test_formal_runner_rejects_dirty_worktree(tmp_path):
    """Without fixture_mode, dirty worktree blocks the run."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    # Create a dirty file (outside exports/reports) to trigger the block
    dirty_file = PROJECT_ROOT / "_test_dirty_worktree_temp.txt"
    dirty_file.write_text("dirty", encoding="utf-8")
    try:
        # Run WITHOUT fixture_mode — should detect dirty worktree
        result = run(
            preflight=preflight, package=package,
            output_root=tmp_path / "runs", end_date="2013-01-04",
            dry_run=True, fixture_mode=False,
        )
        assert result["status"] == "BLOCKED"
        assert "dirty_worktree" in result["reason"]
    finally:
        dirty_file.unlink(missing_ok=True)


def test_formal_runner_freezes_all_input_objects(tmp_path):
    """After a successful dry-run, frozen_inputs/ contains all required files."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    run_dir = tmp_path / "runs" / result["formal_run_id"]
    frozen = run_dir / "frozen_inputs"
    assert frozen.is_dir()
    for name in ("trade_calendar.csv", "tradable_universe.csv", "scores.csv",
                 "prices.csv", "adjustment_factors.csv"):
        assert (frozen / name).is_file(), f"missing frozen: {name}"


def test_formal_runner_reads_only_frozen_inputs(tmp_path):
    """Backtest command points to frozen_inputs/, not the original package."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    run_dir = tmp_path / "runs" / result["formal_run_id"]
    frozen = run_dir / "frozen_inputs"
    cmd = " ".join(result["command"])
    assert str(frozen) in cmd
    assert "--tradable-universe-snapshot" in cmd
    assert "--adjustment-factor-snapshot" in cmd
    assert "--formal-mode" in cmd


def test_formal_runner_requires_tradable_universe_snapshot(tmp_path):
    """formal_mode flag is passed to the backtest command."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    assert result["status"] == "DRY_RUN"
    # formal_mode is in the command
    assert "--formal-mode" in " ".join(result["command"])


def test_formal_runner_requires_adjustment_factor_snapshot(tmp_path):
    """adjustment_factor_snapshot is passed to the backtest command."""
    package = tmp_path / "package"
    evidence_sha = _build_valid_package(package)
    preflight = tmp_path / "preflight.json"
    preflight.write_text(
        json.dumps({
            "status": "READY_FOR_FORMAL_RUN",
            "evidence_sha256": evidence_sha,
            "package": str(package),
        }),
        encoding="utf-8",
    )
    result = run(
        preflight=preflight, package=package,
        output_root=tmp_path / "runs", end_date="2013-01-04",
        dry_run=True, fixture_mode=True,
    )
    assert result["status"] == "DRY_RUN"
    assert "--adjustment-factor-snapshot" in " ".join(result["command"])
