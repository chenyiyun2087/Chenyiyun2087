#!/usr/bin/env python3
"""Fail-closed discovery and qualification of real Alpha evidence.

The pipeline inventories existing files, checks whether they satisfy the
v4.1 evidence contracts, freezes only qualified inputs, and produces adapters
that can be passed to the existing proof chain.  Discovery never promotes an
asset merely because its filename resembles a required dataset.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from runtime.acceptance_config import canonical_sha


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _table_schema(path: Path) -> tuple[list[str], dict[str, str], int | None]:
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path)
        elif path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        else:
            return [], {}, None
    except Exception:  # discovery must report unreadable assets, not abort
        return [], {}, None
    return (
        [str(column) for column in frame.columns],
        {str(column): str(dtype) for column, dtype in frame.dtypes.items()},
        int(len(frame)),
    )


def _candidate(
    path: Path,
    *,
    kind: str,
    project_root: Path,
    explicit: bool,
) -> dict[str, Any]:
    columns, dtypes, row_count = _table_schema(path)
    payload = _json(path) if path.suffix.lower() == ".json" else {}
    schema_material: Any = (
        {"columns": columns, "dtypes": dtypes}
        if columns
        else {"json_top_level_keys": sorted(payload)}
    )
    return {
        "candidate_id": canonical_sha(
            {"kind": kind, "path": _relative(path, project_root)}
        )[:16],
        "kind": kind,
        "path": _relative(path, project_root),
        "explicit_input": explicit,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "file_sha256": _file_sha(path),
        "schema_sha256": canonical_sha(schema_material),
        "columns": columns,
        "row_count": row_count,
        "readable": bool(columns or payload),
        "_json_payload": payload,
        "_absolute_path": str(path.resolve()),
    }


def discover_evidence(
    project_root: Path,
    config: dict[str, Any],
    explicit_paths: dict[str, Path | None],
) -> dict[str, Any]:
    """Build a bounded deterministic inventory of candidate assets."""

    max_per_kind = int(config.get("max_candidates_per_kind", 25))
    discovered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    patterns = config.get("discovery_patterns") or {}
    kinds = ("benchmark", "factor", "pit", "shadow", "execution")
    for kind in kinds:
        paths: list[tuple[Path, bool]] = []
        explicit = explicit_paths.get(kind)
        if explicit is not None and explicit.exists():
            paths.append((explicit, True))
        for pattern in patterns.get(kind, []):
            paths.extend((path, False) for path in project_root.glob(str(pattern)))
        for path, is_explicit in sorted(
            paths, key=lambda item: (not item[1], str(item[0]))
        )[:max_per_kind]:
            key = (kind, str(path.resolve()))
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            discovered.append(
                _candidate(
                    path,
                    kind=kind,
                    project_root=project_root,
                    explicit=is_explicit,
                )
            )
    public_candidates = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in discovered
    ]
    counts = {
        kind: sum(row["kind"] == kind for row in public_candidates)
        for kind in kinds
    }
    return {
        "schema_version": "alpha_v4_1_data_catalog_v1",
        "status": "DISCOVERED" if public_candidates else "BLOCKED",
        "bounded": True,
        "max_candidates_per_kind": max_per_kind,
        "counts": counts,
        "candidates": public_candidates,
        "_internal_candidates": discovered,
    }


def _parse_tz_aware(values: pd.Series) -> bool:
    if values.empty or values.isna().any():
        return False
    parsed = pd.to_datetime(values, errors="coerce", utc=False)
    if parsed.isna().any():
        return False
    return all(getattr(value, "tzinfo", None) is not None for value in parsed)


def _qualify_benchmark(
    row: dict[str, Any],
    config: dict[str, Any],
    analysis_asof: datetime,
) -> tuple[str, list[str], dict[str, Any]]:
    path = Path(row["_absolute_path"])
    reasons: list[str] = []
    try:
        frame = pd.read_csv(path)
    except Exception:
        return "BLOCKED", ["unreadable_benchmark_table"], {}
    required_columns = {"benchmark", "trade_date", "nav", "available_at"}
    if not required_columns.issubset(frame.columns):
        reasons.append("benchmark_contract_columns_missing")
        return "BLOCKED", reasons, {}
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    required_codes = [str(value) for value in config["required_benchmark_codes"]]
    present = set(frame["benchmark"].astype(str))
    if not set(required_codes).issubset(present):
        reasons.append("required_benchmark_codes_missing")
    if frame.duplicated(["benchmark", "trade_date"]).any():
        reasons.append("duplicate_benchmark_dates")
    if frame["trade_date"].isna().any() or frame["nav"].isna().any():
        reasons.append("invalid_benchmark_values")
    if (frame["nav"] <= 0).any():
        reasons.append("non_positive_benchmark_nav")
    if not _parse_tz_aware(frame["available_at"]):
        reasons.append("benchmark_available_at_missing_or_timezone_naive")
    else:
        available = pd.to_datetime(frame["available_at"], utc=True)
        if (available > pd.Timestamp(analysis_asof).tz_convert("UTC")).any():
            reasons.append("benchmark_available_after_analysis_asof")
    per_code = frame.groupby("benchmark")["trade_date"].nunique().to_dict()
    min_days = int(config["benchmark_min_aligned_days"])
    if any(int(per_code.get(code, 0)) < min_days for code in required_codes):
        reasons.append("benchmark_aligned_history_too_short")
    date_sets = {
        code: set(
            frame.loc[frame["benchmark"].astype(str) == code, "trade_date"]
            .dropna()
            .tolist()
        )
        for code in required_codes
    }
    union_dates = set().union(*date_sets.values())
    aligned_dates = (
        set.intersection(*date_sets.values()) if date_sets else set()
    )
    coverage = {
        code: (len(date_sets[code]) / len(union_dates) if union_dates else 0.0)
        for code in required_codes
    }
    if any(
        value < float(config["benchmark_min_coverage"])
        for value in coverage.values()
    ):
        reasons.append("benchmark_daily_coverage_below_minimum")
    if len(aligned_dates) < min_days:
        reasons.append("benchmark_common_alignment_too_short")
    end_dates = {
        code: max(date_sets[code]) if date_sets[code] else None
        for code in required_codes
    }
    if len({value for value in end_dates.values()}) != 1:
        reasons.append("benchmark_end_date_mismatch")
    return (
        "QUALIFIED" if not reasons else "BLOCKED",
        reasons,
        {
            "days_by_benchmark": {str(k): int(v) for k, v in per_code.items()},
            "coverage_by_benchmark": coverage,
            "common_aligned_days": len(aligned_dates),
            "end_date_by_benchmark": {
                code: value.date().isoformat() if value is not None else None
                for code, value in end_dates.items()
            },
        },
    )


def _qualify_factor(
    row: dict[str, Any],
    config: dict[str, Any],
    required_factors: list[str],
) -> tuple[str, list[str], dict[str, Any]]:
    path = Path(row["_absolute_path"])
    reasons: list[str] = []
    try:
        frame = (
            pd.read_csv(path)
            if path.suffix.lower() == ".csv"
            else pd.read_parquet(path)
        )
    except Exception:
        return "BLOCKED", ["unreadable_factor_table"], {}
    availability = [f"{factor}_available_at" for factor in required_factors]
    missing_availability = [
        column for column in availability if column not in frame.columns
    ]
    missing_factors = [factor for factor in required_factors if factor not in frame]
    if missing_factors:
        reasons.append("required_factor_columns_missing")
    if missing_availability:
        reasons.append("factor_available_at_columns_missing")
    if any(str(column).startswith("bs_model_") for column in frame.columns):
        reasons.append("backfilled_model_fields_require_formal_pit_proof")
    if "signal_time" not in frame.columns:
        reasons.append("factor_signal_time_missing")
    elif not _parse_tz_aware(frame["signal_time"]):
        reasons.append("factor_signal_time_missing_or_timezone_naive")
    if not missing_availability:
        for column in availability:
            if not _parse_tz_aware(frame[column]):
                reasons.append(f"{column}_missing_or_timezone_naive")
                break
        if (
            "signal_time" in frame.columns
            and "factor_signal_time_missing" not in reasons
            and
            "factor_signal_time_missing_or_timezone_naive" not in reasons
            and not any(
                reason.endswith("_missing_or_timezone_naive")
                for reason in reasons
                if reason.startswith(tuple(availability))
            )
        ):
            signal = pd.to_datetime(frame["signal_time"], utc=True)
            if any(
                (
                    pd.to_datetime(frame[column], utc=True) > signal
                ).any()
                for column in availability
            ):
                reasons.append("factor_available_after_signal")
    date_column = next(
        (name for name in ("trade_date", "signal_date", "date") if name in frame),
        None,
    )
    unique_dates = (
        int(pd.to_datetime(frame[date_column], errors="coerce").nunique())
        if date_column
        else 0
    )
    if unique_dates < 252:
        reasons.append("factor_history_too_short")
    return (
        "QUALIFIED" if not reasons else "BLOCKED",
        reasons,
        {
            "unique_dates": unique_dates,
            "required_availability_columns": availability,
        },
    )


def _qualify_pit(
    row: dict[str, Any],
    release_id: str,
    strategy_id: str,
) -> tuple[str, list[str], dict[str, Any]]:
    payload = row["_json_payload"]
    manifest = payload.get("manifest") if isinstance(payload, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    reasons: list[str] = []
    if not manifest:
        reasons.append("pit_manifest_missing")
    if not bool(manifest.get("formal_pit_eligible")):
        reasons.append("formal_pit_eligible_false")
    if str(manifest.get("release_id") or manifest.get("release") or "") != release_id:
        reasons.append("pit_release_mismatch")
    if str(manifest.get("strategy_id") or manifest.get("strategy") or "") != strategy_id:
        reasons.append("pit_strategy_mismatch")
    components = manifest.get("components") or {}
    component_text = json.dumps(components, ensure_ascii=False).lower()
    if "suspension" not in component_text:
        reasons.append("pit_suspension_source_missing")
    return (
        "QUALIFIED" if not reasons else "BLOCKED",
        reasons,
        {
            "source_release": manifest.get("release_id")
            or manifest.get("release"),
            "source_strategy": manifest.get("strategy_id")
            or manifest.get("strategy"),
            "formal_pit_eligible": bool(manifest.get("formal_pit_eligible")),
        },
    )


def qualify_evidence(
    catalog: dict[str, Any],
    config: dict[str, Any],
    *,
    release_id: str,
    strategy_id: str,
    analysis_asof: datetime,
    required_factors: list[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source in catalog["_internal_candidates"]:
        kind = source["kind"]
        if kind == "benchmark":
            status, reasons, diagnostics = _qualify_benchmark(
                source, config, analysis_asof
            )
        elif kind == "factor":
            status, reasons, diagnostics = _qualify_factor(
                source, config, required_factors
            )
        elif kind == "pit":
            status, reasons, diagnostics = _qualify_pit(
                source, release_id, strategy_id
            )
        elif kind == "shadow":
            status, reasons, diagnostics = (
                "BLOCKED",
                ["shadow_requires_release_scoped_live_observation_contract"],
                {},
            )
        else:
            status, reasons, diagnostics = (
                "DISCOVERED_ONLY",
                ["execution_asset_not_sufficient_for_e3_or_e4"],
                {},
            )
        rows.append(
            {
                "candidate_id": source["candidate_id"],
                "kind": kind,
                "path": source["path"],
                "file_sha256": source["file_sha256"],
                "schema_sha256": source["schema_sha256"],
                "status": status,
                "blockers": reasons,
                "diagnostics": diagnostics,
                "_absolute_path": source["_absolute_path"],
                "size_bytes": source["size_bytes"],
            }
        )
    required_status = {
        kind: (
            "QUALIFIED"
            if any(row["kind"] == kind and row["status"] == "QUALIFIED" for row in rows)
            else "BLOCKED"
        )
        for kind in ("benchmark", "factor", "pit", "shadow")
    }
    public_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    return {
        "schema_version": "alpha_v4_1_evidence_qualification_v1",
        "status": (
            "PASS"
            if all(value == "QUALIFIED" for value in required_status.values())
            else "BLOCKED"
        ),
        "release_id": release_id,
        "strategy_id": strategy_id,
        "required_evidence_status": required_status,
        "rows": public_rows,
        "_internal_rows": rows,
    }


def freeze_qualified_evidence(
    qualification: dict[str, Any],
    snapshot_dir: Path,
    config: dict[str, Any],
    *,
    created_at: datetime,
) -> dict[str, Any]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(config.get("snapshot_copy_max_bytes", 0))
    assets: list[dict[str, Any]] = []
    for row in qualification["_internal_rows"]:
        if row["status"] != "QUALIFIED":
            continue
        source = Path(row["_absolute_path"])
        if row["size_bytes"] > max_bytes:
            assets.append(
                {
                    "candidate_id": row["candidate_id"],
                    "kind": row["kind"],
                    "status": "BLOCKED",
                    "blockers": ["qualified_asset_exceeds_snapshot_copy_limit"],
                    "source_sha256": row["file_sha256"],
                }
            )
            continue
        target = snapshot_dir / f"{row['candidate_id']}{source.suffix.lower()}"
        shutil.copy2(source, target)
        assets.append(
            {
                "candidate_id": row["candidate_id"],
                "kind": row["kind"],
                "status": "FROZEN",
                "source_path": row["path"],
                "snapshot_path": str(Path("evidence_snapshots") / target.name),
                "source_sha256": row["file_sha256"],
                "snapshot_sha256": _file_sha(target),
                "schema_sha256": row["schema_sha256"],
            }
        )
    deterministic = {
        "release_id": qualification["release_id"],
        "strategy_id": qualification["strategy_id"],
        "assets": assets,
    }
    return {
        "schema_version": "alpha_v4_1_snapshot_manifest_v1",
        "status": "FROZEN" if assets and all(
            row["status"] == "FROZEN" for row in assets
        ) else "BLOCKED",
        "created_at": created_at.isoformat(),
        **deterministic,
        "snapshot_sha256": canonical_sha(deterministic),
    }


def build_adapter_report(
    qualification: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    frozen_by_id = {
        row["candidate_id"]: row
        for row in snapshot["assets"]
        if row["status"] == "FROZEN"
    }
    adapters = []
    for kind in ("benchmark", "factor", "pit", "shadow"):
        qualified = next(
            (
                row
                for row in qualification["_internal_rows"]
                if row["kind"] == kind and row["status"] == "QUALIFIED"
            ),
            None,
        )
        frozen = (
            frozen_by_id.get(qualified["candidate_id"]) if qualified else None
        )
        adapters.append(
            {
                "kind": kind,
                "status": "READY" if frozen else "BLOCKED",
                "input_path": frozen.get("snapshot_path", "") if frozen else "",
                "input_sha256": frozen.get("snapshot_sha256", "") if frozen else "",
                "blockers": [] if frozen else [f"{kind}_qualified_snapshot_missing"],
                "automatic_gate_override_allowed": False,
            }
        )
    return {
        "schema_version": "alpha_v4_1_evidence_adapter_v1",
        "status": (
            "READY"
            if all(row["status"] == "READY" for row in adapters)
            else "BLOCKED"
        ),
        "adapters": adapters,
        "capital_authority": False,
        "broker_action_allowed": False,
    }


def build_refresh_queue(
    qualification: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    priorities = config.get("refresh_priorities") or {}
    actions = {
        "benchmark": "acquire_three_index_daily_nav_with_timezone_aware_available_at",
        "factor": "produce_release_scoped_factor_and_ic_panels_with_signal_time",
        "pit": "run_formal_pit_capture_for_current_release_and_strategy",
        "shadow": "accumulate_real_shadow_days_and_closed_round_trips",
    }
    items = []
    for kind in ("benchmark", "factor", "pit", "shadow"):
        if qualification["required_evidence_status"][kind] == "QUALIFIED":
            continue
        blockers = sorted(
            {
                blocker
                for row in qualification["rows"]
                if row["kind"] == kind
                for blocker in row["blockers"]
            }
        ) or [f"{kind}_candidate_missing"]
        items.append(
            {
                "priority": int(priorities.get(kind, 99)),
                "kind": kind,
                "status": "OPEN",
                "blockers": blockers,
                "next_action": actions[kind],
                "owner": "DATA_EVIDENCE_PIPELINE",
                "capital_effect": "NO_SCALE",
            }
        )
    items.sort(key=lambda row: (row["priority"], row["kind"]))
    return {
        "schema_version": "alpha_v4_1_evidence_refresh_queue_v1",
        "status": "OPEN" if items else "CLEAR",
        "open_count": len(items),
        "items": items,
    }


def build_evidence_acquisition_pipeline(
    project_root: Path,
    profile: dict[str, Any],
    *,
    release_id: str,
    strategy_id: str,
    analysis_asof: datetime,
    output_dir: Path,
    explicit_paths: dict[str, Path | None],
) -> dict[str, dict[str, Any]]:
    config = profile["evidence_acquisition"]
    catalog = discover_evidence(project_root, config, explicit_paths)
    qualification = qualify_evidence(
        catalog,
        config,
        release_id=release_id,
        strategy_id=strategy_id,
        analysis_asof=analysis_asof,
        required_factors=[
            str(value) for value in profile["attribution"]["required_factors"]
        ],
    )
    snapshot = freeze_qualified_evidence(
        qualification,
        output_dir / "evidence_snapshots",
        config,
        created_at=analysis_asof,
    )
    adapter = build_adapter_report(qualification, snapshot)
    refresh_queue = build_refresh_queue(qualification, config)
    catalog.pop("_internal_candidates", None)
    qualification.pop("_internal_rows", None)
    return {
        "data_catalog_report.json": catalog,
        "evidence_qualification_report.json": qualification,
        "evidence_snapshot_manifest.json": snapshot,
        "evidence_adapter_report.json": adapter,
        "evidence_refresh_queue_report.json": refresh_queue,
    }
