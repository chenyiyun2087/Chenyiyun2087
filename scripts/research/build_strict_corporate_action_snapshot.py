"""Build an immutable, versioned strict corporate-action/lifecycle snapshot.

The input is a Tushare export (or a normalized equivalent).  This deliberately
does not write production tables: research runs consume the emitted CSV and its
manifest, which makes the exact point-in-time data set auditable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REQUIRED = {"symbol", "action_type", "effective_date", "source_event_id", "as_of_timestamp", "source_complete"}
OPTIONAL = ["announcement_date", "ex_date", "cash_per_share", "stock_ratio", "rights_ratio", "rights_price", "split_ratio", "settlement_price", "source_reason"]


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def build(source: Path, output_dir: Path, dataset_version: str, lifecycle_source: Path | None = None) -> dict:
    frame = pd.read_csv(source, dtype={"symbol": str, "source_event_id": str})
    missing = sorted(REQUIRED - set(frame.columns))
    if missing:
        raise RuntimeError(f"corporate-action source missing fields: {missing}")
    frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    for column in ["effective_date", "as_of_timestamp", "announcement_date", "ex_date"]:
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    if frame[["symbol", "action_type", "effective_date", "source_event_id", "as_of_timestamp"]].isna().any().any():
        raise RuntimeError("corporate-action source contains null identity/timing fields")
    for column in OPTIONAL:
        if column not in frame:
            frame[column] = None
    frame["source_complete"] = frame["source_complete"].map(
        lambda value: str(value).strip().lower() in {"1", "true", "t", "yes", "y"}
    )
    identity = frame[["symbol", "action_type", "effective_date", "source_event_id", "as_of_timestamp", *OPTIONAL, "source_complete"]].astype(str)
    frame["event_hash"] = identity.apply(lambda row: _digest(row.to_dict()), axis=1)
    frame = frame.sort_values(["effective_date", "symbol", "source_event_id"]).drop_duplicates("event_hash").reset_index(drop=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "strict_corporate_actions.csv"
    frame.to_csv(events_path, index=False)
    manifest = {
        "dataset_version": dataset_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "event_count": int(len(frame)),
        "complete_event_count": int(frame["source_complete"].sum()),
        "event_types": frame["action_type"].value_counts().to_dict(),
        "snapshot_sha256": hashlib.sha256(events_path.read_bytes()).hexdigest(),
    }
    if lifecycle_source:
        lifecycle = pd.read_csv(lifecycle_source, dtype={"symbol": str})
        lifecycle_required = {"symbol", "effective_date", "is_listed", "is_suspended"}
        if missing := sorted(lifecycle_required - set(lifecycle.columns)):
            raise RuntimeError(f"lifecycle source missing fields: {missing}")
        lifecycle["symbol"] = lifecycle["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
        lifecycle["effective_date"] = pd.to_datetime(lifecycle["effective_date"], errors="coerce")
        if lifecycle[["symbol", "effective_date"]].isna().any().any():
            raise RuntimeError("lifecycle source contains null identity/timing fields")
        lifecycle_path = output_dir / "strict_security_lifecycle.csv"
        lifecycle.sort_values(["effective_date", "symbol"]).to_csv(lifecycle_path, index=False)
        manifest["lifecycle_source"] = str(lifecycle_source)
        manifest["lifecycle_source_sha256"] = hashlib.sha256(lifecycle_source.read_bytes()).hexdigest()
        manifest["lifecycle_snapshot_sha256"] = hashlib.sha256(lifecycle_path.read_bytes()).hexdigest()
        manifest["lifecycle_row_count"] = int(len(lifecycle))
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create strict corporate-action research snapshot.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--lifecycle-source", type=Path, default=None)
    print(json.dumps(build(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
