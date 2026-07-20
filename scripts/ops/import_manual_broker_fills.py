"""Validate and import manually executed fills.

The importer never creates orders and never calls a broker.  Every fill must
reference an existing, identity-matched local order and has an immutable
``fill_id`` so retries cannot duplicate economic events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.contracts import ManualFill
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops.order_repository import DEFAULT_ORDER_TABLE, _validate_table_name


DEFAULT_FILL_TABLE = "chenyiyun.ads_manual_broker_fills"
DERIVED_COLUMNS = {"broker_fill_id", "broker_order_id", "source_file_sha", "imported_at"}
REQUIRED_COLUMNS = set(ManualFill.model_fields) - DERIVED_COLUMNS

DDL_MANUAL_FILLS = """
CREATE TABLE IF NOT EXISTS chenyiyun.ads_manual_broker_fills (
  fill_id VARCHAR(128) PRIMARY KEY,
  broker_fill_id VARCHAR(128) NOT NULL,
  broker_order_id VARCHAR(128) NOT NULL,
  order_id BIGINT NOT NULL,
  account_id VARCHAR(64) NOT NULL,
  release_id VARCHAR(128) NOT NULL,
  run_id VARCHAR(128) NOT NULL,
  symbol VARCHAR(16) NOT NULL,
  side VARCHAR(8) NOT NULL,
  shares BIGINT NOT NULL,
  price DECIMAL(20,8) NOT NULL,
  fee DECIMAL(20,8) NOT NULL,
  submitted_at DATETIME NOT NULL,
  fill_timestamp DATETIME NOT NULL,
  execution_mode VARCHAR(48) NOT NULL,
  fallback_reason VARCHAR(255) NOT NULL DEFAULT '',
  source_file_sha CHAR(64) NOT NULL,
  imported_at DATETIME NOT NULL,
  payload_sha CHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_manual_fill_order_time(order_id, fill_timestamp, shares, price),
  KEY idx_manual_fill_identity(release_id, run_id, account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _safe_table_name(table: str) -> str:
    return _validate_table_name(str(table or ""))


def _normalize_symbol(value: object) -> str:
    raw = str(value or "").strip().upper()
    digits = "".join(char for char in raw if char.isdigit())[-6:]
    if not digits:
        raise ValueError(f"invalid symbol: {value}")
    return digits.zfill(6)


def load_manual_fills(csv_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(csv_path, dtype={"symbol": str, "order_id": str, "fill_id": str})
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    source_file_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    imported_at = datetime.now(timezone.utc)
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in frame.to_dict("records"):
        raw["symbol"] = _normalize_symbol(raw.get("symbol"))
        raw["fallback_reason"] = "" if pd.isna(raw.get("fallback_reason")) else str(raw.get("fallback_reason"))
        raw["broker_fill_id"] = f"offline:{raw.get('fill_id')}" if pd.isna(raw.get("broker_fill_id")) or not raw.get("broker_fill_id") else str(raw.get("broker_fill_id"))
        raw["broker_order_id"] = f"offline:{raw.get('order_id')}" if pd.isna(raw.get("broker_order_id")) or not raw.get("broker_order_id") else str(raw.get("broker_order_id"))
        raw["source_file_sha"] = source_file_sha
        raw["imported_at"] = imported_at
        raw = {name: raw.get(name) for name in ManualFill.model_fields}
        fill = ManualFill.model_validate(raw)
        if fill.fill_id in seen:
            raise ValueError(f"duplicate fill_id in input: {fill.fill_id}")
        seen.add(fill.fill_id)
        records.append(fill.model_dump(mode="json"))
    return pd.DataFrame(records, columns=list(ManualFill.model_fields))


def ensure_manual_fill_schema(engine, fill_table: str = DEFAULT_FILL_TABLE) -> None:
    table = _safe_table_name(fill_table)
    ddl = DDL_MANUAL_FILLS.replace(DEFAULT_FILL_TABLE, table)
    with engine.begin() as connection:
        connection.execute(text(ddl))


def import_manual_fills(
    engine,
    fills: pd.DataFrame,
    order_table: str = DEFAULT_ORDER_TABLE,
    fill_table: str = DEFAULT_FILL_TABLE,
) -> int:
    order_table = _safe_table_name(order_table)
    fill_table = _safe_table_name(fill_table)
    ensure_manual_fill_schema(engine, fill_table)
    imported = 0
    with engine.begin() as connection:
        for payload in fills.to_dict("records"):
            fill = ManualFill.model_validate(payload)
            order = connection.execute(text(
                f"SELECT id,account_id,release_id,run_id,execution_date,ts_code,side,target_shares,order_status "
                f"FROM {order_table} WHERE id=:order_id FOR UPDATE"
            ), {"order_id": fill.order_id}).mappings().first()
            if order is None:
                raise ValueError(f"manual_fill_unknown_order:{fill.order_id}")
            comparisons = {
                "account_id": (str(order.get("account_id") or ""), fill.account_id),
                "release_id": (str(order.get("release_id") or ""), fill.release_id),
                "run_id": (str(order.get("run_id") or ""), fill.run_id),
                "symbol": (_normalize_symbol(order.get("ts_code")), fill.symbol),
                "side": (str(order.get("side") or "").upper(), fill.side),
            }
            mismatches = [name for name, pair in comparisons.items() if pair[0] != pair[1]]
            if mismatches:
                raise ValueError(f"manual_fill_order_identity_mismatch:{fill.order_id}:{','.join(mismatches)}")
            execution_date = pd.Timestamp(order.get("execution_date")).date()
            fill_date = pd.Timestamp(fill.fill_timestamp).date()
            fill_time = pd.Timestamp(fill.fill_timestamp).time()
            if fill_date != execution_date or not pd.Timestamp("09:15").time() <= fill_time <= pd.Timestamp("15:10").time():
                raise ValueError(f"manual_fill_execution_window_invalid:{fill.order_id}")
            if str(order.get("order_status") or "").lower() not in {
                "risk_approved", "manual_submitted", "partial_fill",
                "submitted_manually", "submitted", "partial",
            }:
                raise ValueError(f"manual_fill_invalid_order_state:{fill.order_id}:{order.get('order_status')}")
            existing = connection.execute(
                text(f"SELECT payload_sha FROM {fill_table} WHERE fill_id=:fill_id"),
                {"fill_id": fill.fill_id},
            ).scalar()
            encoded = json.dumps(fill.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            payload_sha = hashlib.sha256(encoded.encode()).hexdigest()
            if existing:
                if str(existing) != payload_sha:
                    raise ValueError(f"manual_fill_immutable_conflict:{fill.fill_id}")
                continue
            connection.execute(text(
                f"""INSERT INTO {fill_table}
                (fill_id,broker_fill_id,broker_order_id,order_id,account_id,release_id,run_id,symbol,side,shares,price,fee,
                 submitted_at,fill_timestamp,execution_mode,fallback_reason,source_file_sha,imported_at,payload_sha)
                VALUES (:fill_id,:broker_fill_id,:broker_order_id,:order_id,:account_id,:release_id,:run_id,:symbol,:side,:shares,
                        :price,:fee,:submitted_at,:fill_timestamp,:execution_mode,:fallback_reason,:source_file_sha,:imported_at,:payload_sha)"""
            ), {**fill.model_dump(), "payload_sha": payload_sha})
            totals = connection.execute(
                text(f"SELECT COALESCE(SUM(shares),0),COALESCE(SUM(shares*price),0),COALESCE(SUM(fee),0) "
                     f"FROM {fill_table} WHERE order_id=:order_id"),
                {"order_id": fill.order_id},
            ).fetchone()
            filled_total = int(totals[0] or 0)
            filled_notional = float(totals[1] or 0)
            filled_fee = float(totals[2] or 0)
            filled_vwap = filled_notional / filled_total if filled_total else 0.0
            target_shares = abs(int(order.get("target_shares") or 0))
            if target_shares <= 0 or int(filled_total) > target_shares:
                raise ValueError(f"manual_fill_share_conservation:{fill.order_id}")
            new_status = "FILLED" if int(filled_total) == target_shares else "PARTIAL_FILL"
            connection.execute(text(
                f"UPDATE {order_table} SET order_status=:status,submitted_at=:submitted_at,"
                "filled_shares=:filled_shares,filled_price=:filled_price,filled_notional=:filled_notional,"
                "filled_fee=:filled_fee,status_reason=:reason "
                "WHERE id=:order_id"
            ), {
                "status": new_status, "submitted_at": fill.submitted_at,
                "filled_shares": int(filled_total), "filled_price": filled_vwap,
                "filled_notional": filled_notional, "filled_fee": filled_fee,
                "reason": f"manual_fill:{fill.fill_id}", "order_id": fill.order_id,
            })
            imported += 1
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description="Import immutable, identity-matched manual fills.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--order-table", default=DEFAULT_ORDER_TABLE)
    parser.add_argument("--fill-table", default=DEFAULT_FILL_TABLE)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    fills = load_manual_fills(args.input)
    if not args.execute:
        print(f"validated_rows={len(fills)} dry_run=1")
        return
    engine = create_engine(build_sqlalchemy_url())
    imported = import_manual_fills(engine, fills, args.order_table, args.fill_table)
    print(f"validated_rows={len(fills)} imported_rows={imported}")


if __name__ == "__main__":
    main()
