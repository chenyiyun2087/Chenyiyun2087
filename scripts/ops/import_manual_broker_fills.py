"""Import manually recorded broker fills into the local production order table.

This is the CSV bridge for the semi-automatic loop. It intentionally updates
only lifecycle fields and does not create broker API dependencies.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


DEFAULT_ORDER_TABLE = "chenyiyun.ads_local_strategy_orders"
REQUIRED_COLUMNS = {
    "trade_date",
    "ts_code",
    "side",
    "submitted_at",
    "filled_shares",
    "filled_price",
    "order_status",
    "status_reason",
}
VALID_STATUSES = {"planned", "submitted_manually", "submitted", "filled", "partial", "rejected", "cancelled"}


def _safe_table_name(table: str) -> str:
    value = str(table or "").strip()
    if not value:
        raise ValueError("empty table name")
    if not all(part.replace("_", "").isalnum() for part in value.split(".")):
        raise ValueError(f"invalid table name: {table}")
    return value


def _normalize_ts_code(value: object) -> str:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())[-6:]
    return digits.zfill(6) if digits else raw


def _normalize_date(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def load_manual_fills(csv_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(csv_path)
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    frame = frame.copy()
    frame["trade_date"] = frame["trade_date"].map(_normalize_date)
    frame["ts_code"] = frame["ts_code"].map(_normalize_ts_code)
    frame["side"] = frame["side"].astype(str).str.upper().str.strip()
    frame["order_status"] = frame["order_status"].astype(str).str.lower().str.strip()
    invalid_status = sorted(set(frame["order_status"]) - VALID_STATUSES)
    if invalid_status:
        raise ValueError(f"invalid order_status values: {', '.join(invalid_status)}")
    invalid_side = sorted(set(frame["side"]) - {"BUY", "SELL"})
    if invalid_side:
        raise ValueError(f"invalid side values: {', '.join(invalid_side)}")
    frame["filled_shares"] = pd.to_numeric(frame["filled_shares"], errors="coerce").fillna(0).astype(int)
    frame["filled_price"] = pd.to_numeric(frame["filled_price"], errors="coerce")
    frame["submitted_at"] = pd.to_datetime(frame["submitted_at"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    frame.loc[frame["submitted_at"].eq("NaT"), "submitted_at"] = None
    return frame[
        [
            "trade_date",
            "ts_code",
            "side",
            "submitted_at",
            "filled_shares",
            "filled_price",
            "order_status",
            "status_reason",
        ]
    ]


def import_manual_fills(engine, fills: pd.DataFrame, order_table: str = DEFAULT_ORDER_TABLE) -> int:
    table = _safe_table_name(order_table)
    rows = fills.where(pd.notna(fills), None).to_dict("records")
    if not rows:
        return 0
    sql = text(
        f"""
        INSERT INTO {table}
            (trade_date, ts_code, side, submitted_at, filled_shares, filled_price, order_status, status_reason)
        VALUES
            (:trade_date, :ts_code, :side, :submitted_at, :filled_shares, :filled_price, :order_status, :status_reason)
        ON DUPLICATE KEY UPDATE
            submitted_at=VALUES(submitted_at),
            filled_shares=VALUES(filled_shares),
            filled_price=VALUES(filled_price),
            order_status=VALUES(order_status),
            status_reason=VALUES(status_reason)
        """
    )
    with engine.begin() as conn:
        result = conn.execute(sql, rows)
    return int(result.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import manually recorded broker fills into ads_local_strategy_orders.")
    parser.add_argument("--input", required=True, help="CSV with trade_date,ts_code,side,submitted_at,filled_shares,filled_price,order_status,status_reason")
    parser.add_argument("--order-table", default=DEFAULT_ORDER_TABLE)
    parser.add_argument("--execute", action="store_true", help="Write updates to the database. Omit for validation-only dry run.")
    args = parser.parse_args()

    fills = load_manual_fills(Path(args.input))
    if not args.execute:
        print(f"validated_rows={len(fills)} dry_run=1")
        return
    engine = create_engine(build_sqlalchemy_url())
    affected = import_manual_fills(engine, fills, args.order_table)
    print(f"imported_rows={len(fills)} affected_rows={affected}")


if __name__ == "__main__":
    main()
