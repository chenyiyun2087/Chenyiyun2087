"""Data snapshot manifest — cryptographically binds backtest inputs to a run.

Every backtest run must reference a DataSnapshot with hashes of all input
data sources. This enables:
  - Deterministic replay: same snapshot → same results
  - Audit trail: which data version was used for this decision
  - Staleness detection: is the production config using stale data
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class DataSnapshot:
    """Immutable fingerprint of all data inputs for a backtest or production run."""

    snapshot_date: str                 # YYYY-MM-DD — the "as of" date for all data
    scores_hash: str                   # SHA-256 of score_rank_daily snapshot
    prices_hash: str                   # SHA-256 of dwd_stock_daily_standard snapshot
    trade_cal_hash: str                # SHA-256 of dim_trade_cal snapshot
    corporate_action_hash: str         # SHA-256 of corporate_action_snapshot
    lifecycle_hash: str                # SHA-256 of security_lifecycle_snapshot
    label_hash: str = ""               # SHA-256 of dwd_stock_label_daily snapshot
    factor_hash: str = ""              # SHA-256 of factor data
    created_at: str = field(default_factory=lambda: date.today().isoformat())

    def fingerprint(self) -> str:
        """Deterministic hash of all component hashes."""
        payload = json.dumps({
            "scores": self.scores_hash,
            "prices": self.prices_hash,
            "trade_cal": self.trade_cal_hash,
            "corporate_action": self.corporate_action_hash,
            "lifecycle": self.lifecycle_hash,
            "label": self.label_hash,
            "factor": self.factor_hash,
            "date": self.snapshot_date,
        }, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_dict(self) -> dict[str, str]:
        return {
            "snapshot_date": self.snapshot_date,
            "scores_hash": self.scores_hash,
            "prices_hash": self.prices_hash,
            "trade_cal_hash": self.trade_cal_hash,
            "corporate_action_hash": self.corporate_action_hash,
            "lifecycle_hash": self.lifecycle_hash,
            "label_hash": self.label_hash,
            "factor_hash": self.factor_hash,
            "fingerprint": self.fingerprint(),
            "created_at": self.created_at,
        }

    def is_compatible_with(self, other: "DataSnapshot") -> bool:
        """Check if two snapshots share the same core data."""
        return (
            self.corporate_action_hash == other.corporate_action_hash
            and self.lifecycle_hash == other.lifecycle_hash
            and self.trade_cal_hash == other.trade_cal_hash
        )


def hash_dataframe(df) -> str:
    """Compute SHA-256 of a DataFrame's sorted CSV representation."""
    import pandas as pd
    csv_bytes = df.sort_index(axis=1).to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()[:16]


def freeze_data_snapshot(
    engine,
    snapshot_date: str,
    score_table: str = "chenyiyun.score_rank_daily",
    price_table: str = "tushare_stock.dwd_stock_daily_standard",
    trade_cal_table: str = "chenyiyun.dim_trade_cal",
) -> DataSnapshot:
    """Create a DataSnapshot by hashing the current state of input tables.

    Reads all rows for snapshot_date from each table, computes SHA-256 hashes,
    and returns an immutable DataSnapshot.
    """
    import pandas as pd
    from sqlalchemy import text

    def _hash_table(table: str, date_col: str, date_val: str) -> str:
        try:
            with engine.connect() as conn:
                df = pd.read_sql(
                    f"SELECT * FROM {table} WHERE {date_col} = :d",
                    conn, params={"d": date_val},
                )
            return hash_dataframe(df) if not df.empty else "EMPTY_TABLE"
        except Exception as exc:
            return f"ERROR: {exc}"

    return DataSnapshot(
        snapshot_date=snapshot_date,
        scores_hash=_hash_table(score_table, "trade_date", snapshot_date),
        prices_hash=_hash_table(price_table, "trade_date", snapshot_date),
        trade_cal_hash=_hash_table(trade_cal_table, "cal_date", snapshot_date),
        corporate_action_hash="PENDING",   # requires CA snapshot builder
        lifecycle_hash="PENDING",           # requires lifecycle snapshot builder
    )
