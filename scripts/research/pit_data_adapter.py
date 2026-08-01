#!/usr/bin/env python3
"""Normalize file or read-only MySQL PIT sources into a frozen manifest.

The adapter is the only supported entrance to the long-horizon PIT builder.
It records content and schema hashes plus semantic versions.  It never falls
back to the short training panel and never changes a source database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import (
    get_available_at_column,
    get_contract_sha256,
    get_required_columns,
    get_source_families,
    validate_explicit_timezone,
    validate_frame_schema,
)

SOURCE_NAMES = get_source_families()
REQUIRED_COLUMNS = {name: get_required_columns(name) for name in SOURCE_NAMES}


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _schema_hash(frame: pd.DataFrame) -> str:
    return canonical_sha(
        [f"{column}:{frame[column].dtype}" for column in sorted(frame.columns)]
    )


def _safe_select(query: str) -> bool:
    normalized = " ".join(query.strip().split()).lower()
    if not normalized.startswith("select ") or ";" in normalized:
        return False
    forbidden = re.compile(
        r"\b(insert|update|delete|drop|alter|truncate|replace|create|grant|revoke)\b"
    )
    return forbidden.search(normalized) is None


def _query_sha(query: str, params: Any = None) -> str:
    return canonical_sha({"query": " ".join(query.strip().split()), "params": params})


def _query_text_sha(query: str) -> str:
    """Hash normalized SQL text independently from bound parameters."""
    return canonical_sha(" ".join(str(query or "").strip().split()))


def _parameter_sha(params: Any = None) -> str:
    """Hash bound parameters independently from SQL text."""
    return canonical_sha(params if params is not None else {})


def _snapshot_identity_from_config(config: dict[str, Any]) -> str:
    value = str(config.get("snapshot_id") or config.get("snapshot_token") or "")
    return value.strip()


def _validate_source_timezones(
    frames: dict[str, pd.DataFrame], blockers: list[str], *, strict: bool = True
) -> None:
    for name, frame in frames.items():
        column = get_available_at_column(name)
        if column not in frame.columns and not strict and "available_at" in frame.columns:
            column = "available_at"
        if column not in frame.columns:
            if strict:
                blockers.append(f"source_column_missing:{name}:{column}")
            continue
        offenders = validate_explicit_timezone(frame[column])
        if offenders:
            blockers.append(f"source_available_at_timezone_missing:{name}")
        parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if parsed.isna().any():
            blockers.append(f"source_available_at_unparseable:{name}")


def _write_blocked(
    output_dir: Path, blockers: list[str], config_path: Path | None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_7_pit_data_adapter_v1",
        "status": "BLOCKED",
        "adapter_ready": False,
        "config_path": str(config_path) if config_path else None,
        "blockers": sorted(set(blockers)),
        "historical_evidence_level": "E0",
        "synthetic_evidence_level": "S0",
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale qualified artifacts from prior runs
    for stale_name in ["pit_source_manifest.json"]:
        stale_path = output_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()
    snapshots_dir = output_dir / "snapshots"
    if snapshots_dir.exists():
        import shutil
        shutil.rmtree(snapshots_dir)
    (output_dir / "pit_adapter_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def build_pit_adapter_manifest(
    config_path: Path | None, output_dir: Path
) -> dict[str, Any]:
    if config_path is None or not config_path.exists():
        return _write_blocked(
            output_dir, ["adapter_config_missing"], config_path
        )
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _write_blocked(
            output_dir, [f"adapter_config_json_error:{type(exc).__name__}:{exc}"], config_path
        )
    adapter_type = str(config.get("adapter_type") or "").upper()
    origin = str(config.get("evidence_origin") or "")
    source_config = config.get("sources") or {}
    strict_contract = origin == "HISTORICAL_REAL" or bool(
        config.get("require_canonical_sources", False)
    )
    active_names = list(SOURCE_NAMES) if strict_contract else [
        name for name in SOURCE_NAMES if name in source_config
    ]
    if not active_names and not strict_contract:
        active_names = list(SOURCE_NAMES)
    blockers: list[str] = []
    if adapter_type not in {"FILE", "MYSQL"}:
        blockers.append("adapter_type_invalid")
    if origin not in {"SYNTHETIC", "HISTORICAL_REAL"}:
        blockers.append("evidence_origin_invalid")
    if origin == "HISTORICAL_REAL":
        attestation = config.get("evidence_attestation") or {}
        if not isinstance(attestation, dict):
            blockers.append("evidence_attestation_missing_or_invalid")
        else:
            for req in ("data_source_version", "revision_chain_proof", "availability_time_proof"):
                if not str(attestation.get(req) or ""):
                    blockers.append(f"evidence_attestation_missing:{req}")
        completeness = config.get("source_completeness")
        for family in ("corporate_actions", "security_lifecycle"):
            if isinstance(completeness, dict):
                explicit = completeness.get(family)
            else:
                explicit = config.get(
                    "corporate_action_complete"
                    if family == "corporate_actions"
                    else "security_lifecycle_complete"
                )
            if explicit is not True:
                blockers.append(f"source_completeness_missing:{family}")
    for field in (
        "release",
        "provider",
        "retrieved_at",
        "schema_semantic_version",
        "field_definition_hash",
    ):
        if not str(config.get(field) or ""):
            blockers.append(f"adapter_metadata_missing:{field}")
    fdh = str(config.get("field_definition_hash") or "")
    if origin == "HISTORICAL_REAL":
        if fdh.startswith("matCHANGEME") or len(fdh) != 64:
            blockers.append("field_definition_hash_is_placeholder")
    elif fdh.startswith("matCHANGEME"):
        blockers.append("field_definition_hash_is_placeholder")
    contract_sha = get_contract_sha256()
    if strict_contract and fdh and fdh != contract_sha:
        blockers.append("field_definition_hash_mismatch_with_canonical_contract")
    configured_snapshot_id = _snapshot_identity_from_config(config)
    if strict_contract and adapter_type == "FILE" and not configured_snapshot_id:
        blockers.append("file_snapshot_id_missing")
    if strict_contract and adapter_type == "MYSQL" and not (
        str(config.get("snapshot_token") or "")
        or str(config.get("snapshot_token_query") or "")
        or bool(config.get("require_gtid", False))
    ):
        blockers.append("mysql_snapshot_token_or_gtid_requirement_missing")
    retrieved_at = pd.to_datetime(
        config.get("retrieved_at"), errors="coerce", utc=True
    )
    if pd.isna(retrieved_at):
        blockers.append("retrieved_at_invalid_or_timezone_missing")
    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    source_query_sha: dict[str, str] = {}
    snapshot_meta: dict[str, Any] = {
        "snapshot_id": configured_snapshot_id or None,
        "transaction_isolation": None,
        "transaction_started_at": None,
        "transaction_finished_at": None,
        "snapshot_token": None,
        "snapshot_token_query_sha256": None,
        "gtid_or_binlog_position": None,
    }
    if adapter_type == "FILE":
        import shutil
        # The frozen copy is part of the release output.  Keeping it under
        # ``output_dir/snapshots`` makes the manifest, semantic audit, builder,
        # and downstream registry all bind the same bytes.
        freeze_dir = output_dir / "snapshots"
        freeze_dir.mkdir(parents=True, exist_ok=True)
        for name in active_names:
            payload = source_config.get(name) or {}
            path_value = payload.get("path")
            if not path_value:
                blockers.append(f"source_path_missing:{name}")
                continue
            path = Path(str(path_value)).expanduser()
            if not path.is_absolute():
                path = (config_path.parent / path).resolve()
            if not path.exists():
                blockers.append(f"source_file_missing:{name}")
                continue
            # Freeze: copy to temp dir before reading (TOCTOU protection)
            suffix = path.suffix if path.suffix else ".parquet"
            frozen = freeze_dir / f"{name}{suffix}"
            shutil.copy2(path, frozen)
            paths[name] = frozen
            frames[name] = _read_table(frozen)
            source_query_sha[name] = _query_sha(
                str((source_config.get(name) or {}).get("query") or ""),
                (source_config.get(name) or {}).get("params"),
            )
    elif adapter_type == "MYSQL":
        db_url = os.getenv("CHENYIYUN_DB_URL")
        if not db_url:
            blockers.append("CHENYIYUN_DB_URL_not_configured")
        else:
            from sqlalchemy import create_engine, text

            engine = create_engine(db_url)
            snapshot_dir = output_dir / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime, timezone

            with engine.connect() as conn:
                conn.execute(text("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                conn.execute(text("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"))
                snapshot_meta["transaction_started_at"] = datetime.now(timezone.utc).isoformat()
                isolation_row = conn.execute(
                    text("SELECT @@SESSION.transaction_isolation")
                ).fetchone()
                snapshot_meta["transaction_isolation"] = str(isolation_row[0]) if isolation_row else ""
                token_query = str(config.get("snapshot_token_query") or "").strip()
                if token_query:
                    snapshot_meta["snapshot_token_query_sha256"] = _query_sha(
                        token_query, config.get("snapshot_token_params")
                    )
                    if not _safe_select(token_query):
                        blockers.append("mysql_snapshot_token_query_not_read_only_select")
                    else:
                        token_params = config.get("snapshot_token_params") or {}
                        token_row = (
                            conn.execute(text(token_query), token_params)
                            if token_params
                            else conn.execute(text(token_query))
                        ).fetchone()
                        token_value = token_row[0] if token_row else None
                        if token_value in (None, ""):
                            blockers.append("mysql_snapshot_token_empty")
                        snapshot_meta["snapshot_token"] = str(token_value or "")
                elif config.get("snapshot_token") or os.getenv("CHENYIYUN_DB_SNAPSHOT_TOKEN"):
                    snapshot_meta["snapshot_token"] = str(
                        config.get("snapshot_token")
                        or os.getenv("CHENYIYUN_DB_SNAPSHOT_TOKEN")
                    )
                elif not config.get("require_gtid", False):
                    blockers.append("mysql_snapshot_token_query_missing")
                else:
                    gtid_row = conn.execute(text("SELECT @@GLOBAL.gtid_executed")).fetchone()
                    gtid_value = gtid_row[0] if gtid_row else None
                    if not gtid_value:
                        blockers.append("mysql_gtid_empty")
                    snapshot_meta["snapshot_token"] = str(gtid_value or "")
                    snapshot_meta["gtid_or_binlog_position"] = str(gtid_value or "")
                if snapshot_meta.get("snapshot_token") and config.get("require_gtid"):
                    snapshot_meta["gtid_or_binlog_position"] = snapshot_meta[
                        "snapshot_token"
                    ]
                for name in active_names:
                    payload = source_config.get(name) or {}
                    query = str(payload.get("query") or "")
                    if not _safe_select(query):
                        blockers.append(f"mysql_query_not_read_only_select:{name}")
                        continue
                    frame = pd.read_sql(
                        text(query), conn, params=payload.get("params") or {}
                    )
                    path = snapshot_dir / f"{name}.parquet"
                    frame.to_parquet(path, index=False)
                    paths[name] = path
                    frames[name] = frame
                    source_query_sha[name] = _query_sha(
                        query, payload.get("params")
                    )
                snapshot_meta["transaction_finished_at"] = datetime.now(timezone.utc).isoformat()
                conn.execute(text("COMMIT"))
            engine.dispose()
            if not snapshot_meta.get("snapshot_token"):
                blockers.append("mysql_snapshot_identity_missing")
            expected_token = str(
                config.get("snapshot_token")
                or os.getenv("CHENYIYUN_DB_SNAPSHOT_TOKEN")
                or ""
            )
            if expected_token and str(snapshot_meta.get("snapshot_token")) != expected_token:
                blockers.append("mysql_snapshot_identity_mismatch")
    for name, frame in frames.items():
        missing_columns = sorted(REQUIRED_COLUMNS[name] - set(frame.columns))
        if strict_contract:
            blockers.extend(
                f"source_column_missing:{name}:{column}"
                for column in missing_columns
            )
        expected_schema = str(
            (source_config.get(name) or {}).get("expected_schema_hash") or ""
        )
        actual_schema = _schema_hash(frame)
        if strict_contract and expected_schema and expected_schema != actual_schema:
            blockers.append(f"source_schema_hash_mismatch:{name}")
        if strict_contract:
            blockers.extend(validate_frame_schema(frame, name))
        elif "trade_date" not in frame.columns or "symbol" not in frame.columns:
            blockers.append(f"source_key_missing:{name}")
    _validate_source_timezones(frames, blockers, strict=strict_contract)
    if len(frames) != len(active_names):
        blockers.append("source_family_incomplete")
    if blockers:
        return _write_blocked(output_dir, blockers, config_path)
    sources: dict[str, Any] = {}
    coverage_values: list[pd.Timestamp] = []
    for name in active_names:
        frame = frames[name]
        business_column = "cal_date" if name == "trade_calendar" else "trade_date"
        parsed_dates = pd.to_datetime(
            frame.get(business_column, pd.Series(dtype="object")),
            errors="coerce",
        ).dropna()
        if parsed_dates.empty:
            blockers.append(f"source_business_date_missing:{name}")
            coverage_start = None
            coverage_end = None
        else:
            coverage_start = parsed_dates.min().date().isoformat()
            coverage_end = parsed_dates.max().date().isoformat()
            coverage_values.extend([parsed_dates.min(), parsed_dates.max()])
        sources[name] = {
            "path": str(paths[name]),
            "sha256": _file_sha(paths[name]),
            "content_sha256": _file_sha(paths[name]),
            "schema_hash": _schema_hash(frame),
            "rows": int(len(frame)),
            "version": str((source_config.get(name) or {}).get("version") or ""),
            "provider": str(config["provider"]),
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "query_sha256": source_query_sha.get(name, ""),
            "query_text_sha256": _query_text_sha(
                str((source_config.get(name) or {}).get("query") or "")
            ),
            "parameter_sha256": _parameter_sha(
                (source_config.get(name) or {}).get("params")
            ),
        }
        if not sources[name]["version"]:
            blockers.append(f"source_version_missing:{name}")
    if blockers:
        return _write_blocked(output_dir, blockers, config_path)
    manifest: dict[str, Any] = {
        "schema_version": "alpha_v4_7_pit_source_manifest_v1",
        "status": "QUALIFIED",
        "adapter_type": adapter_type,
        "release": str(config["release"]),
        "evidence_origin": origin,
        "provider": str(config["provider"]),
        "retrieved_at": pd.Timestamp(retrieved_at).isoformat(),
        "schema_semantic_version": str(config["schema_semantic_version"]),
        "field_definition_hash": str(config["field_definition_hash"]),
        "calendar_source": str(
            config.get("calendar_source")
            or ((source_config.get("trade_calendar") or {}).get("source") or "")
            or ((source_config.get("trade_calendar") or {}).get("provider") or "")
        ),
        "source_completeness": {
            "corporate_actions": bool(
                (config.get("source_completeness") or {}).get("corporate_actions")
                if isinstance(config.get("source_completeness"), dict)
                else config.get(
                    "corporate_action_complete",
                    config.get("corporate_actions_complete", False),
                )
            ),
            "security_lifecycle": bool(
                (config.get("source_completeness") or {}).get("security_lifecycle")
                if isinstance(config.get("source_completeness"), dict)
                else config.get("security_lifecycle_complete", False)
            ),
        },
        "corporate_action_complete": bool(
            (config.get("source_completeness") or {}).get("corporate_actions")
            if isinstance(config.get("source_completeness"), dict)
            else config.get(
                "corporate_action_complete",
                config.get("corporate_actions_complete", False),
            )
        ),
        "security_lifecycle_complete": bool(
            (config.get("source_completeness") or {}).get("security_lifecycle")
            if isinstance(config.get("source_completeness"), dict)
            else config.get("security_lifecycle_complete", False)
        ),
        "sources": sources,
        "snapshot_identity": {
            **snapshot_meta,
            "configured_snapshot_id": configured_snapshot_id or None,
        },
        "coverage_start": min(coverage_values).date().isoformat() if coverage_values else None,
        "coverage_end": max(coverage_values).date().isoformat() if coverage_values else None,
        "historical_evidence_level": (
            "E1" if origin == "HISTORICAL_REAL" else "E0"
        ),
        "synthetic_evidence_level": "S1" if origin == "SYNTHETIC" else "S0",
        "capital_authority": False,
        "adapter_config_sha256": (
            _file_sha(config_path) if config_path and config_path.exists() else None
        ),
    }
    manifest["content_sha256"] = canonical_sha(
        {
            key: value
            for key, value in manifest.items()
            if key != "content_sha256"
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "pit_source_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    config_sha = _file_sha(config_path) if config_path and config_path.exists() else None
    report = {
        "schema_version": "alpha_v4_7_pit_data_adapter_v1",
        "status": "PASS",
        "adapter_ready": True,
        "config_path": str(config_path) if config_path else None,
        "config_sha256": config_sha,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _file_sha(manifest_path),
        "evidence_origin": origin,
        "snapshot_identity": manifest.get("snapshot_identity"),
        "coverage_start": manifest.get("coverage_start"),
        "coverage_end": manifest.get("coverage_end"),
        "historical_evidence_level": manifest["historical_evidence_level"],
        "synthetic_evidence_level": manifest["synthetic_evidence_level"],
        "capital_authority": False,
        "blockers": [],
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    (output_dir / "pit_adapter_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_pit_adapter_manifest(args.config, args.output_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
