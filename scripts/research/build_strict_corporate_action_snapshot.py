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
OPTIONAL = ["announcement_date", "ex_date", "cash_per_share", "stock_ratio", "rights_ratio", "rights_price", "split_ratio", "settlement_price", "new_ts_code", "source_reason"]
SNAPSHOT_SCHEMA_VERSION = "strict_corporate_lifecycle_snapshot_v2"
# Cash entitlement is based on pre-adjustment shares.  Rights then sees the
# deterministic post split/bonus share count.
ATOMIC_TYPES = {
    "dividend_cash": 20,
    "split_merge": 30,
    "stock_bonus": 40,
    "rights_subscription": 50,
    "share_conversion": 55,
    "delist_cash_settlement": 60,
    "delist_writeoff": 70,
}
ECONOMIC_FIELDS = {"cash_per_share", "stock_ratio", "rights_ratio", "rights_price", "split_ratio", "settlement_price"}


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
    # Never carry a source row's other economic legs into an atomic event.
    # The ledger dispatches strictly by action_type, but zeroing here makes the
    # snapshot independently safe to inspect and consume.
    shared = {**row.to_dict(), **{field: None for field in ECONOMIC_FIELDS}}
    rows: list[dict] = []
    def add(event_type: str, **values: object) -> None:
        event = {**shared, **values, "action_type": event_type, "parent_source_event_id": parent, "event_id": f"{parent}:{event_type}", "priority": ATOMIC_TYPES[event_type]}
        rows.append(event)
    cash = pd.to_numeric(row.get("cash_per_share"), errors="coerce")
    stock = pd.to_numeric(row.get("stock_ratio"), errors="coerce")
    split = pd.to_numeric(row.get("split_ratio"), errors="coerce")
    rights = pd.to_numeric(row.get("rights_ratio"), errors="coerce")
    if raw_type == "delist_cash_settlement":
        add(raw_type, settlement_price=row.get("settlement_price"))
    elif raw_type == "delist_writeoff":
        add(raw_type)
    elif raw_type == "share_conversion":
        add(
            raw_type,
            split_ratio=row.get("split_ratio"),
            new_ts_code=row.get("new_ts_code"),
        )
    elif raw_type == "rights_subscription":
        add(raw_type, rights_ratio=row.get("rights_ratio"), rights_price=row.get("rights_price"))
    elif raw_type == "split_merge":
        add(raw_type, split_ratio=row.get("split_ratio"))
    elif raw_type == "stock_bonus":
        add(raw_type, stock_ratio=row.get("stock_ratio"))
    elif raw_type == "dividend_cash":
        add(raw_type, cash_per_share=row.get("cash_per_share"))
    else:
        if pd.notna(split) and split != 0: add("split_merge", split_ratio=row.get("split_ratio"))
        if pd.notna(stock) and stock != 0: add("stock_bonus", stock_ratio=row.get("stock_ratio"))
        if pd.notna(cash) and cash != 0: add("dividend_cash", cash_per_share=row.get("cash_per_share"))
        if pd.notna(rights) and rights != 0: add("rights_subscription", rights_ratio=row.get("rights_ratio"), rights_price=row.get("rights_price"))
        if not rows:  # incomplete/no-economic-leg source must not become a benign no-op.
            raise RuntimeError(f"corporate action has no economic leg: {parent}")
    return rows


def _build_lifecycle_panel(lifecycle: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    if "trade_date" not in calendar:
        raise RuntimeError("lifecycle calendar missing trade_date")
    sessions = pd.to_datetime(calendar["trade_date"], errors="coerce").dropna().drop_duplicates().sort_values()
    if sessions.empty:
        raise RuntimeError("lifecycle calendar has no valid sessions")
    if lifecycle.duplicated(["symbol", "effective_date"]).any():
        raise RuntimeError("lifecycle source has duplicate symbol/effective_date")
    rows: list[pd.DataFrame] = []
    for symbol, events in lifecycle.groupby("symbol", sort=False):
        events = events.sort_values("effective_date")
        if events["effective_date"].iloc[0] > sessions.iloc[0]:
            raise RuntimeError(f"lifecycle source missing initial state: {symbol}")
        base = pd.DataFrame({"trade_date": sessions})
        panel = pd.merge_asof(base, events.sort_values("effective_date"), left_on="trade_date", right_on="effective_date", direction="backward")
        if panel[["is_listed", "is_suspended"]].isna().any().any():
            raise RuntimeError(f"lifecycle source cannot cover calendar: {symbol}")
        panel["symbol"] = symbol
        rows.append(panel[["symbol", "trade_date", "is_listed", "is_suspended"]])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["symbol", "trade_date", "is_listed", "is_suspended"])


def build(source: Path, output_dir: Path, dataset_version: str, lifecycle_source: Path | None = None, lifecycle_calendar: Path | None = None) -> dict:
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
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
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
        if lifecycle_calendar is None:
            raise RuntimeError("lifecycle calendar is required for a daily lifecycle snapshot")
        lifecycle = pd.read_csv(lifecycle_source, dtype={"symbol": str})
        lifecycle_required = {"symbol", "effective_date", "is_listed", "is_suspended"}
        if missing := sorted(lifecycle_required - set(lifecycle.columns)):
            raise RuntimeError(f"lifecycle source missing fields: {missing}")
        lifecycle["symbol"] = lifecycle["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
        lifecycle["effective_date"] = pd.to_datetime(lifecycle["effective_date"], errors="coerce")
        if lifecycle[["symbol", "effective_date"]].isna().any().any():
            raise RuntimeError("lifecycle source contains null identity/timing fields")
        for column in ("is_listed", "is_suspended"):
            lifecycle[column] = pd.to_numeric(lifecycle[column], errors="coerce")
            if lifecycle[column].isna().any() or not lifecycle[column].isin([0, 1]).all():
                raise RuntimeError(f"lifecycle source has invalid {column} values")
        calendar = pd.read_csv(lifecycle_calendar)
        lifecycle_panel = _build_lifecycle_panel(lifecycle, calendar)
        lifecycle_path = output_dir / "strict_security_lifecycle.csv"
        lifecycle_panel.sort_values(["trade_date", "symbol"]).to_csv(lifecycle_path, index=False)
        manifest["lifecycle_source"] = str(lifecycle_source)
        manifest["lifecycle_source_sha256"] = hashlib.sha256(lifecycle_source.read_bytes()).hexdigest()
        manifest["lifecycle_calendar"] = str(lifecycle_calendar)
        manifest["lifecycle_calendar_sha256"] = hashlib.sha256(lifecycle_calendar.read_bytes()).hexdigest()
        manifest["lifecycle_snapshot_sha256"] = hashlib.sha256(lifecycle_path.read_bytes()).hexdigest()
        manifest["lifecycle_row_count"] = int(len(lifecycle_panel))
        manifest["lifecycle_panel_start"] = str(lifecycle_panel["trade_date"].min())
        manifest["lifecycle_panel_end"] = str(lifecycle_panel["trade_date"].max())
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create strict corporate-action research snapshot.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--lifecycle-source", type=Path, default=None)
    parser.add_argument("--lifecycle-calendar", type=Path, default=None)
    print(json.dumps(build(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
