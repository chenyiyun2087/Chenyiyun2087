#!/usr/bin/env python3
"""Non-destructive PIT V2 metadata migration and incremental snapshot merge.

The command never edits its inputs.  Missing availability timestamps are not
inferred: migration fails closed and emits a precise gap list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


LINEAGE = ("event_date", "publish_date", "available_at", "ingested_at", "source", "snapshot_sha", "schema_version")


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)


def _digest(frame: pd.DataFrame) -> str:
    canonical = frame.sort_index(axis=1).to_json(orient="records", date_format="iso", force_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def migrate(source: Path, destination: Path, primary_key: list[str], incremental: Path | None = None) -> dict:
    if destination.resolve() in {source.resolve(), *( [incremental.resolve()] if incremental else [])}:
        raise ValueError("DESTINATION_MUST_NOT_OVERWRITE_SOURCE")
    frames = [_read(source)] + ([_read(incremental)] if incremental else [])
    frame = pd.concat(frames, ignore_index=True)
    required_source = {"event_date", "publish_date", "available_at", "source"} | set(primary_key)
    missing = sorted(required_source.difference(frame.columns))
    if missing:
        return {"status": "BLOCKED", "missing_columns": missing, "rows_written": 0}
    for column in ("event_date", "publish_date", "available_at"):
        parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if parsed.isna().any():
            return {"status": "BLOCKED", "invalid_timestamp_column": column, "rows_written": 0}
        frame[column] = parsed
    if (frame["available_at"] < frame["publish_date"]).any() or (frame["publish_date"] < frame["event_date"]).any():
        return {"status": "BLOCKED", "reason": "LINEAGE_TIME_ORDER_VIOLATION", "rows_written": 0}
    duplicate = frame.duplicated(primary_key, keep=False)
    if duplicate.any():
        return {"status": "BLOCKED", "reason": "DUPLICATE_PRIMARY_KEY", "duplicate_rows": int(duplicate.sum()), "rows_written": 0}
    frame["ingested_at"] = datetime.now(timezone.utc).isoformat()
    frame["schema_version"] = "2.0"
    digest = _digest(frame.drop(columns=["snapshot_sha"], errors="ignore"))
    frame["snapshot_sha"] = digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("IMMUTABLE_DESTINATION_ALREADY_EXISTS")
    if destination.suffix.lower() == ".parquet":
        frame.to_parquet(destination, index=False)
    else:
        frame.to_csv(destination, index=False)
    return {"status": "MIGRATED", "rows_written": len(frame), "snapshot_sha": digest, "destination": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--incremental", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--primary-key", nargs="+", required=True)
    args = parser.parse_args()
    result = migrate(args.source, args.destination, args.primary_key, args.incremental)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
