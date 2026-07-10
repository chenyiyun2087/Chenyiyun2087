"""Unique entrypoint for frozen Full Strategy V3 OOS validation evidence."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research.matched_portfolio_runner import _RANDOM_SEEDS
from scripts.research.validation_evidence import (
    EvidenceStatus,
    build_fold_definitions,
    canonical_sha,
    finalize_manifest,
    overall_coverage_status,
    sha256_file,
    validate_evidence_package,
)


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=PROJECT_ROOT, text=True
    ).strip()


def _json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _database_snapshot(engine) -> tuple[dict[str, Any], list, list, list]:
    calendar = pd.read_sql(text("""
        SELECT cal_date
        FROM chenyiyun.dim_trade_cal
        WHERE exchange='SSE' AND is_open=1
          AND cal_date BETWEEN '2022-01-01' AND '2026-06-30'
        ORDER BY cal_date
    """), engine)
    score_dates = pd.read_sql(text("""
        SELECT DISTINCT trade_date
        FROM chenyiyun.score_rank_daily
        WHERE trade_date BETWEEN '2022-01-01' AND '2026-06-30'
        ORDER BY trade_date
    """), engine)
    price_dates = pd.read_sql(text("""
        SELECT DISTINCT trade_date
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date BETWEEN 20220101 AND 20260630
        ORDER BY trade_date
    """), engine)
    counts = pd.read_sql(text("""
        SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date,
               COUNT(*) AS rows_count, COUNT(DISTINCT trade_date) AS trading_days
        FROM chenyiyun.score_rank_daily
        WHERE trade_date <= '2026-06-30'
    """), engine).iloc[0].to_dict()
    snapshot = {
        "score_rank_daily": counts,
        "score_date_count": len(score_dates),
        "price_date_count": len(price_dates),
        "calendar_date_count": len(calendar),
        "data_cutoff": "2026-06-30",
    }
    return (
        snapshot,
        calendar["cal_date"].tolist(),
        score_dates["trade_date"].tolist(),
        price_dates["trade_date"].astype(str).tolist(),
    )


def _source_snapshot(engine, sql: str, source: str) -> dict[str, Any]:
    row = pd.read_sql(text(sql), engine).iloc[0].to_dict()
    payload = {"source": source, "summary": row, "source_complete": False}
    payload["sha256"] = canonical_sha(payload)
    return payload


def _write_empty_parquets(output_dir: Path) -> None:
    schemas = {
        "daily_candidates.parquet": ["experiment_id", "window", "signal_date", "symbol", "rank", "reject_reason"],
        "daily_weights.parquet": ["experiment_id", "window", "signal_date", "symbol", "raw_weight", "final_weight", "cash_weight"],
        "daily_exposure.parquet": ["experiment_id", "window", "signal_date", "target_exposure", "actual_exposure"],
        "daily_nav.parquet": ["experiment_id", "window", "trade_date", "nav", "cash", "market_value"],
        "trade_ledger.parquet": ["experiment_id", "window", "trade_date", "symbol", "side", "shares", "price", "total_cost"],
        "rejection_ledger.parquet": ["experiment_id", "window", "trade_date", "symbol", "side", "reason"],
    }
    for name, columns in schemas.items():
        pd.DataFrame(columns=columns).to_parquet(output_dir / name, index=False)


def run(output_dir: Path, test_log: Path) -> dict[str, Any]:
    dirty = _git("status", "--porcelain")
    if dirty:
        raise RuntimeError("validation requires a clean worktree before output creation")
    if output_dir.exists():
        raise FileExistsError(f"validation output already exists: {output_dir}")
    if not test_log.is_file():
        raise FileNotFoundError(f"test log missing: {test_log}")

    git_sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)
    data_snapshot, calendar_dates, score_dates, price_dates = _database_snapshot(engine)
    folds = build_fold_definitions(calendar_dates, score_dates, price_dates)
    coverage_status = overall_coverage_status(folds)

    config_files = [
        PROJECT_ROOT / "config" / "strategy_release_registry.yaml",
        PROJECT_ROOT / "config" / "production_strategy.yaml",
        PROJECT_ROOT / "config" / "market_exposure_governor_v1.yaml",
    ]
    config_snapshot = {
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in config_files},
        "fixed_windows": [fold.get("window") for fold in folds],
        "train_months": 24,
        "embargo_trading_days": 10,
        "validation_months": 6,
        "step_months": 6,
    }
    config_sha = canonical_sha(config_snapshot)
    data_snapshot["fold_coverage"] = folds
    data_sha = canonical_sha(data_snapshot)
    calendar_snapshot = {
        "exchange": "SSE",
        "start": str(min(pd.Timestamp(x).date() for x in calendar_dates)),
        "end": str(max(pd.Timestamp(x).date() for x in calendar_dates)),
        "trading_dates": [str(pd.Timestamp(value).date()) for value in calendar_dates],
    }
    calendar_sha = canonical_sha(calendar_snapshot)
    corporate = _source_snapshot(engine, """
        SELECT COUNT(*) AS row_count, MIN(ex_date) AS min_ex_date,
               MAX(ex_date) AS max_ex_date, MAX(updated_at) AS max_updated_at
        FROM tushare_stock.ods_dividend
    """, "tushare_stock.ods_dividend")
    lifecycle = _source_snapshot(engine, """
        SELECT COUNT(*) AS row_count, MIN(list_date) AS min_list_date,
               MAX(list_date) AS max_list_date, MAX(delist_date) AS max_delist_date
        FROM tushare_stock.dim_stock
    """, "tushare_stock.dim_stock")

    output_dir.mkdir(parents=True, exist_ok=False)
    _json(output_dir / "git_state.json", {
        "git_commit_sha": git_sha,
        "branch": branch,
        "worktree_clean_before_run": True,
    })
    _json(output_dir / "config_snapshot.json", config_snapshot)
    _json(output_dir / "data_snapshot.json", data_snapshot)
    _json(output_dir / "calendar_snapshot.json", calendar_snapshot)
    _json(output_dir / "corporate_action_snapshot.json", corporate)
    _json(output_dir / "security_lifecycle_snapshot.json", lifecycle)
    _json(output_dir / "fold_definitions.json", folds)
    _json(output_dir / "factor_state_by_fold.json", {
        fold.get("window", "unknown"): {
            "status": "NOT_FITTED",
            "reason": "coverage_preflight_failed" if coverage_status != EvidenceStatus.REPRODUCIBLE else "PR18_matrix_not_run",
        }
        for fold in folds
    })
    _write_empty_parquets(output_dir)
    pd.DataFrame({"seed_index": range(20), "sha256_seed": _RANDOM_SEEDS}).to_csv(
        output_dir / "random_seed_results.csv", index=False
    )
    pd.DataFrame([
        {
            "window": fold.get("window"),
            "status": fold.get("status"),
            **fold.get("coverage", {}),
        }
        for fold in folds
    ]).to_csv(output_dir / "walk_forward_metrics.csv", index=False)
    pd.DataFrame(columns=["trade_date", "nav"]).to_csv(
        output_dir / "stitched_oos_nav.csv", index=False
    )
    shutil.copyfile(test_log, output_dir / "test_log.txt")

    lock_path = PROJECT_ROOT / "requirements.lock.txt"
    manifest = {
        "run_id": output_dir.name,
        "generated_at": datetime.now().astimezone().isoformat(),
        "git_commit_sha": git_sha,
        "worktree_clean": True,
        "config_sha": config_sha,
        "data_sha": data_sha,
        "calendar_sha": calendar_sha,
        "corporate_action_sha": corporate["sha256"],
        "lifecycle_sha": lifecycle["sha256"],
        "python_version": platform.python_version(),
        "dependency_lock_sha": sha256_file(lock_path),
        "evidence_status": coverage_status.value,
        "promotion_status": "PROMOTION_BLOCKED",
        "files": {},
    }
    manifest = finalize_manifest(output_dir, manifest)
    verification = validate_evidence_package(output_dir)
    _json(output_dir / "evidence_verification.json", verification)
    if not verification["passed"]:
        raise RuntimeError(f"evidence package verification failed: {verification['errors']}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--test-log", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir or (
        PROJECT_ROOT / "exports" / "full_strategy_v3_validation"
        / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_pr17"
    )
    manifest = run(output, args.test_log)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["evidence_status"] != EvidenceStatus.REPRODUCIBLE.value:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
