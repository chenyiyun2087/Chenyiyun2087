"""Frozen evidence contract for Full Strategy V3 validation runs."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


FIXED_WINDOWS = (
    ("2025H1", "2025-01-01", "2025-06-30"),
    ("2025H2", "2025-07-01", "2025-12-31"),
    ("2026H1", "2026-01-01", "2026-06-30"),
)

REQUIRED_EVIDENCE_FILES = frozenset({
    "git_state.json",
    "config_snapshot.json",
    "data_snapshot.json",
    "calendar_snapshot.json",
    "corporate_action_snapshot.json",
    "security_lifecycle_snapshot.json",
    "fold_definitions.json",
    "factor_state_by_fold.json",
    "daily_candidates.parquet",
    "daily_weights.parquet",
    "daily_exposure.parquet",
    "daily_nav.parquet",
    "trade_ledger.parquet",
    "rejection_ledger.parquet",
    "random_seed_results.csv",
    "walk_forward_metrics.csv",
    "stitched_oos_nav.csv",
    "test_log.txt",
})

REQUIRED_MANIFEST_FIELDS = frozenset({
    "git_commit_sha",
    "worktree_clean",
    "config_sha",
    "data_sha",
    "calendar_sha",
    "corporate_action_sha",
    "lifecycle_sha",
    "python_version",
    "dependency_lock_sha",
    "evidence_status",
    "promotion_status",
    "files",
})


class EvidenceStatus(str, Enum):
    REPRODUCIBLE = "REPRODUCIBLE"
    NON_REPRODUCIBLE = "NON_REPRODUCIBLE"
    INSUFFICIENT_OOS_COVERAGE = "INSUFFICIENT_OOS_COVERAGE"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha(payload: Any) -> str:
    return sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    )


def build_fold_definitions(
    calendar_dates: Iterable[Any],
    score_dates: Iterable[Any],
    price_dates: Iterable[Any],
    embargo_days: int = 10,
    train_months: int = 24,
) -> list[dict[str, Any]]:
    calendar = sorted({pd.Timestamp(value).date() for value in calendar_dates})
    score_set = {pd.Timestamp(value).date() for value in score_dates}
    price_set = {pd.Timestamp(value).date() for value in price_dates}
    folds: list[dict[str, Any]] = []
    for label, window_start, window_end in FIXED_WINDOWS:
        start = pd.Timestamp(window_start).date()
        end = pd.Timestamp(window_end).date()
        validation_days = [day for day in calendar if start <= day <= end]
        if not validation_days:
            folds.append({
                "window": label,
                "status": EvidenceStatus.INSUFFICIENT_OOS_COVERAGE.value,
                "reason": "calendar_window_empty",
            })
            continue
        prior_days = [day for day in calendar if day < validation_days[0]]
        if len(prior_days) <= embargo_days:
            folds.append({
                "window": label,
                "status": EvidenceStatus.INSUFFICIENT_OOS_COVERAGE.value,
                "reason": "embargo_history_incomplete",
            })
            continue
        embargo = prior_days[-embargo_days:]
        train_end = prior_days[-embargo_days - 1]
        train_start = (pd.Timestamp(train_end) - pd.DateOffset(months=train_months)).date()
        train_days = [day for day in calendar if train_start <= day <= train_end]
        score_train = sum(day in score_set for day in train_days)
        score_validation = sum(day in score_set for day in validation_days)
        price_train = sum(day in price_set for day in train_days)
        price_validation = sum(day in price_set for day in validation_days)
        coverage = {
            "score_train": score_train / max(len(train_days), 1),
            "score_validation": score_validation / max(len(validation_days), 1),
            "price_train": price_train / max(len(train_days), 1),
            "price_validation": price_validation / max(len(validation_days), 1),
        }
        complete = bool(train_days) and all(value >= 0.999999 for value in coverage.values())
        folds.append({
            "window": label,
            "train_start": str(train_start),
            "train_end": str(train_end),
            "embargo_start": str(embargo[0]),
            "embargo_end": str(embargo[-1]),
            "validation_start": str(validation_days[0]),
            "validation_end": str(validation_days[-1]),
            "train_trading_days": len(train_days),
            "validation_trading_days": len(validation_days),
            "coverage": coverage,
            "status": (
                EvidenceStatus.REPRODUCIBLE.value
                if complete
                else EvidenceStatus.INSUFFICIENT_OOS_COVERAGE.value
            ),
            "reason": "" if complete else "required_score_or_price_dates_missing",
        })
    return folds


def overall_coverage_status(folds: list[dict[str, Any]]) -> EvidenceStatus:
    labels = {fold.get("window") for fold in folds}
    complete = (
        labels == {item[0] for item in FIXED_WINDOWS}
        and all(fold.get("status") == EvidenceStatus.REPRODUCIBLE.value for fold in folds)
    )
    return EvidenceStatus.REPRODUCIBLE if complete else EvidenceStatus.INSUFFICIENT_OOS_COVERAGE


def finalize_manifest(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_EVIDENCE_FILES - {path.name for path in output_dir.iterdir() if path.is_file()})
    if missing:
        manifest["evidence_status"] = EvidenceStatus.NON_REPRODUCIBLE.value
        manifest["promotion_status"] = "PROMOTION_BLOCKED"
        manifest["missing_files"] = missing
    manifest["files"] = {
        name: sha256_file(output_dir / name)
        for name in sorted(REQUIRED_EVIDENCE_FILES)
        if (output_dir / name).is_file()
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return manifest


def validate_evidence_package(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"passed": False, "errors": ["manifest_missing"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for field in sorted(REQUIRED_MANIFEST_FIELDS):
        if field not in manifest or manifest[field] in (None, ""):
            errors.append(f"manifest_field_missing:{field}")
    for name in sorted(REQUIRED_EVIDENCE_FILES):
        path = output_dir / name
        if not path.is_file():
            errors.append(f"evidence_file_missing:{name}")
            continue
        expected = manifest.get("files", {}).get(name)
        if expected != sha256_file(path):
            errors.append(f"evidence_sha_mismatch:{name}")
    if manifest.get("worktree_clean") is not True:
        errors.append("worktree_not_clean_at_run_start")
    return {"passed": not errors, "errors": errors, "manifest": manifest}
