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
from scripts.research.pit_factor_panel_builder import (
    REQUIRED_COLUMNS,
    SOURCE_NAMES,
)


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
    config = json.loads(config_path.read_text(encoding="utf-8"))
    adapter_type = str(config.get("adapter_type") or "").upper()
    origin = str(config.get("evidence_origin") or "")
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
    retrieved_at = pd.to_datetime(
        config.get("retrieved_at"), errors="coerce", utc=True
    )
    if pd.isna(retrieved_at):
        blockers.append("retrieved_at_invalid_or_timezone_missing")
    source_config = config.get("sources") or {}
    frames: dict[str, pd.DataFrame] = {}
    paths: dict[str, Path] = {}
    if adapter_type == "FILE":
        for name in SOURCE_NAMES:
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
            paths[name] = path
            frames[name] = _read_table(path)
    elif adapter_type == "MYSQL":
        db_url = os.getenv("CHENYIYUN_DB_URL")
        if not db_url:
            blockers.append("CHENYIYUN_DB_URL_not_configured")
        else:
            from sqlalchemy import create_engine, text

            engine = create_engine(db_url)
            snapshot_dir = output_dir / "snapshots"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            for name in SOURCE_NAMES:
                payload = source_config.get(name) or {}
                query = str(payload.get("query") or "")
                if not _safe_select(query):
                    blockers.append(f"mysql_query_not_read_only_select:{name}")
                    continue
                frame = pd.read_sql(text(query), engine)
                path = snapshot_dir / f"{name}.parquet"
                frame.to_parquet(path, index=False)
                paths[name] = path
                frames[name] = frame
            engine.dispose()
    for name, frame in frames.items():
        missing_columns = sorted(REQUIRED_COLUMNS[name] - set(frame.columns))
        blockers.extend(
            f"source_column_missing:{name}:{column}"
            for column in missing_columns
        )
        expected_schema = str(
            (source_config.get(name) or {}).get("expected_schema_hash") or ""
        )
        if origin == "HISTORICAL_REAL" and not expected_schema:
            blockers.append(f"source_schema_hash_missing:{name}")
        actual_schema = _schema_hash(frame)
        if expected_schema and expected_schema != actual_schema:
            blockers.append(f"source_schema_hash_mismatch:{name}")
    if len(frames) != len(SOURCE_NAMES):
        blockers.append("source_family_incomplete")
    if blockers:
        return _write_blocked(output_dir, blockers, config_path)
    sources: dict[str, Any] = {}
    for name in SOURCE_NAMES:
        frame = frames[name]
        sources[name] = {
            "path": str(paths[name]),
            "sha256": _file_sha(paths[name]),
            "schema_hash": _schema_hash(frame),
            "rows": int(len(frame)),
            "version": str((source_config.get(name) or {}).get("version") or ""),
            "provider": str(config["provider"]),
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
        "sources": sources,
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
