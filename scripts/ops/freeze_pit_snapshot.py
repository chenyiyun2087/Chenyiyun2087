#!/usr/bin/env python3
"""Freeze a complete, immutable Point-in-Time data package.

This command is read-only against MySQL. It writes component Parquet objects to
the content-addressed Evidence Store and a small manifest to exports. Missing
tables, metadata, coverage, or replica storage fail the run closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import create_engine, text

from runtime.contracts import SnapshotComponent, SnapshotManifest
from runtime.evidence_store import EvidenceStore
from scoreRank.core.db_config import build_sqlalchemy_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "pit_snapshot.yaml"
LINEAGE_COLUMNS = {
    "event_date", "publish_date", "available_at", "ingested_at", "source",
    "data_version", "snapshot_sha", "schema_version",
}


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()


def _next_snapshot_id(manifest_root: Path, snapshot_date: str) -> str:
    prefix = f"pit-cn-equity-{snapshot_date.replace('-', '')}-v"
    versions = []
    for path in manifest_root.glob(f"{prefix}*.json"):
        try:
            versions.append(int(path.stem.rsplit("-v", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"{prefix}{max(versions, default=0) + 1}"


def _validate_component(frame: pd.DataFrame, definition: dict, cutoff: datetime) -> tuple[str, str]:
    required = set(definition.get("required_columns") or []) | LINEAGE_COLUMNS | {
        definition["business_date_column"], definition["visible_at_column"], "source", "data_version"
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"pit_component_missing_columns:{definition['name']}:{','.join(missing)}")
    if frame.empty:
        raise ValueError(f"pit_component_empty:{definition['name']}")
    primary_key = list(definition.get("primary_key") or [])
    if not primary_key or frame.duplicated(primary_key).any():
        raise ValueError(f"pit_component_primary_key_invalid:{definition['name']}")
    visible = pd.to_datetime(frame[definition["visible_at_column"]], errors="coerce", utc=True)
    cutoff_ts = pd.Timestamp(cutoff)
    cutoff_ts = cutoff_ts.tz_localize("UTC") if cutoff_ts.tzinfo is None else cutoff_ts.tz_convert("UTC")
    if visible.isna().any() or (visible > cutoff_ts).any():
        raise ValueError(f"pit_component_visibility_violation:{definition['name']}")
    if frame["source"].astype(str).str.strip().eq("").any() or frame["data_version"].astype(str).str.strip().eq("").any():
        raise ValueError(f"pit_component_lineage_missing:{definition['name']}")
    for field in ("snapshot_sha", "schema_version"):
        if frame[field].astype(str).str.strip().eq("").any():
            raise ValueError(f"pit_component_lineage_missing:{definition['name']}:{field}")
    event = pd.to_datetime(frame["event_date"], errors="coerce", utc=True)
    published = pd.to_datetime(frame["publish_date"], errors="coerce", utc=True)
    available = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    ingested = pd.to_datetime(frame["ingested_at"], errors="coerce", utc=True)
    if event.isna().any() or published.isna().any() or available.isna().any() or ingested.isna().any():
        raise ValueError(f"pit_component_lineage_timestamp_invalid:{definition['name']}")
    if (published > available).any() or (available > ingested).any():
        raise ValueError(f"pit_component_lineage_order_violation:{definition['name']}")
    if "historical_backfill" in frame and "historical_use_allowed" in frame:
        unsafe = frame["historical_backfill"].astype(bool) & ~frame["historical_use_allowed"].astype(bool)
        if unsafe.any():
            raise ValueError(f"pit_component_backfill_not_historically_usable:{definition['name']}")
    dates = pd.to_datetime(frame[definition["business_date_column"]], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"pit_component_business_date_invalid:{definition['name']}")
    start, end = dates.min().date().isoformat(), dates.max().date().isoformat()
    minimum_start = definition.get("minimum_start")
    if minimum_start and start > str(minimum_start):
        raise ValueError(f"pit_component_start_coverage:{definition['name']}:{start}>{minimum_start}")
    return start, end


def freeze_snapshot(*, engine, config_path: Path, snapshot_date: str, manifest_root: Path,
                    require_replica: bool = True) -> SnapshotManifest:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    cutoff = datetime.fromisoformat(config["market_data_cutoff"].replace("{snapshot_date}", snapshot_date))
    cutoff = cutoff.replace(tzinfo=timezone.utc) if cutoff.tzinfo is None else cutoff
    store = EvidenceStore(require_replica=require_replica)
    snapshot_id = _next_snapshot_id(manifest_root, snapshot_date)
    git_sha = _git_sha()
    components: list[SnapshotComponent] = []
    with tempfile.TemporaryDirectory(prefix="pit-snapshot-") as temporary_name:
        temporary = Path(temporary_name)
        for definition in config.get("components", []):
            query = str(definition["query"])
            with engine.connect() as connection:
                frame = pd.read_sql(text(query), connection, params={"snapshot_date": snapshot_date})
            start, end = _validate_component(frame, definition, cutoff)
            path = temporary / f"{definition['name']}.parquet"
            frame.to_parquet(path, index=False)
            evidence = store.put_file(path, media_type="application/x-parquet", release_id=snapshot_id,
                                      run_id=snapshot_id, coverage_start=start, coverage_end=end)
            components.append(SnapshotComponent(
                name=definition["name"], sha256=evidence.sha256,
                relative_path=str(evidence.path.relative_to(store.root)), row_count=len(frame),
                coverage_start=start, coverage_end=end, source=definition["source"],
                data_version=str(frame["data_version"].iloc[0]),
                visible_at_field=definition["visible_at_column"], primary_key=tuple(definition["primary_key"]),
                historical_backfill=bool(frame.get("historical_backfill", pd.Series([False])).astype(bool).any()),
                validation_status="VERIFIED",
            ))
    manifest = SnapshotManifest(
        schema_version=str(config.get("manifest_schema") or "pit_snapshot_v2"), snapshot_date=snapshot_date, snapshot_id=snapshot_id,
        market_data_cutoff=cutoff, components=tuple(components), created_at=datetime.now(timezone.utc),
        generator_git_sha=git_sha, validation_status="VERIFIED",
    )
    manifest_root.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_root / f"{snapshot_id}.json"
    if manifest_path.exists():
        raise FileExistsError(f"snapshot_manifest_exists:{manifest_path}")
    encoded = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    manifest_path.write_text(encoded, encoding="utf-8")
    store.put_file(manifest_path, media_type="application/json", release_id=snapshot_id, run_id=snapshot_id)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest-root", type=Path, default=PROJECT_ROOT / "exports" / "pit_manifests")
    parser.add_argument("--allow-single-copy", action="store_true", help="Development only; output remains non-production.")
    args = parser.parse_args()
    engine = create_engine(build_sqlalchemy_url())
    manifest = freeze_snapshot(engine=engine, config_path=args.config, snapshot_date=args.date,
                               manifest_root=args.manifest_root, require_replica=not args.allow_single_copy)
    print(json.dumps({"snapshot_id": manifest.snapshot_id, "snapshot_sha": manifest.fingerprint(),
                      "status": manifest.validation_status}, ensure_ascii=False))


if __name__ == "__main__":
    main()
