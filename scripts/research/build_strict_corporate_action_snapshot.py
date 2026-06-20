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
ATOMIC_TYPES = {"dividend_cash": 40, "stock_bonus": 30, "split_merge": 20, "rights_subscription": 50, "delist_cash_settlement": 60}


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _atomic_rows(row: pd.Series) -> list[dict]:
    """Turn one source announcement into independently auditable economic legs."""
    raw_type = str(row["action_type"])
    aliases = {"split": "split_merge", "merge": "split_merge", "bonus": "stock_bonus", "cash_dividend": "dividend_cash"}
    raw_type = aliases.get(raw_type, raw_type)
    if raw_type not in set(ATOMIC_TYPES) | {"dividend_stock"}:
        raise RuntimeError(f"unknown corporate action type: {raw_type}")
    parent = str(row["source_event_id"])
    shared = row.to_dict()
    rows: list[dict] = []
    def add(event_type: str, **values: object) -> None:
        event = {**shared, **values, "action_type": event_type, "parent_source_event_id": parent, "event_id": f"{parent}:{event_type}", "priority": ATOMIC_TYPES[event_type]}
        rows.append(event)
    cash = pd.to_numeric(row.get("cash_per_share"), errors="coerce")
    stock = pd.to_numeric(row.get("stock_ratio"), errors="coerce")
    split = pd.to_numeric(row.get("split_ratio"), errors="coerce")
    rights = pd.to_numeric(row.get("rights_ratio"), errors="coerce")
    if raw_type == "delist_cash_settlement":
        add(raw_type)
    elif raw_type in {"rights_subscription", "split_merge", "stock_bonus", "dividend_cash"}:
        add(raw_type)
    else:
        if pd.notna(split) and split != 0: add("split_merge")
        if pd.notna(stock) and stock != 0: add("stock_bonus")
        if pd.notna(cash) and cash != 0: add("dividend_cash")
        if pd.notna(rights) and rights != 0: add("rights_subscription")
        if not rows:  # incomplete/no-economic-leg source must not become a benign no-op.
            raise RuntimeError(f"corporate action has no economic leg: {parent}")
    return rows


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
    atomic = pd.DataFrame([event for _, row in frame.iterrows() for event in _atomic_rows(row)])
    identity_columns = ["event_id", "parent_source_event_id", "symbol", "action_type", "effective_date", "as_of_timestamp", *OPTIONAL, "source_complete", "priority"]
    identity = atomic[identity_columns].astype(str)
    atomic["event_hash"] = identity.apply(lambda row: _digest(row.to_dict()), axis=1)
    if atomic["event_id"].duplicated().any() or atomic["event_hash"].duplicated().any():
        raise RuntimeError("duplicate corporate action atomic event or hash")
    frame = atomic.sort_values(["effective_date", "symbol", "parent_source_event_id", "priority"]).reset_index(drop=True)
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
