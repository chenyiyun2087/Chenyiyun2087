"""Broker-neutral, offline-only statement adapter.

It parses files exported manually by a broker.  There is deliberately no
connect, submit, cancel, or network method in this interface.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class OfflineAccountStatement:
    account_id: str
    statement_date: str
    cash: pd.DataFrame
    positions: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    source_hashes: dict[str, str]


class BrokerStatementAdapter:
    REQUIRED = {
        "cash": {"account_id", "statement_date", "available_cash", "total_equity"},
        "positions": {"account_id", "statement_date", "symbol", "shares", "market_value"},
        "orders": {"account_id", "statement_date", "broker_order_id", "symbol", "side", "order_shares", "status"},
        "fills": {"account_id", "statement_date", "broker_fill_id", "broker_order_id", "symbol", "side", "shares", "price", "fee", "fill_timestamp"},
    }

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path, dtype={"symbol": str, "account_id": str,
                                        "broker_order_id": str, "broker_fill_id": str})

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def load(self, *, account_id: str, statement_date: str) -> OfflineAccountStatement:
        frames: dict[str, pd.DataFrame] = {}
        hashes: dict[str, str] = {}
        for name, required in self.REQUIRED.items():
            candidates = [self.root / f"{name}.parquet", self.root / f"{name}.csv"]
            path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if path is None:
                raise FileNotFoundError(f"broker_statement_missing:{name}")
            frame = self._read(path)
            missing = sorted(required - set(frame.columns))
            if missing:
                raise ValueError(f"broker_statement_missing_columns:{name}:{','.join(missing)}")
            frame = frame[(frame["account_id"].astype(str) == account_id) & (frame["statement_date"].astype(str).str[:10] == statement_date)]
            if frame.empty:
                raise ValueError(f"broker_statement_scope_empty:{name}")
            frames[name] = frame.reset_index(drop=True)
            hashes[name] = self._sha(path)
        return OfflineAccountStatement(account_id, statement_date, frames["cash"], frames["positions"],
                                       frames["orders"], frames["fills"], hashes)


def reconcile_offline_statement(*, statement: OfflineAccountStatement,
                                target_positions: pd.DataFrame, local_orders: pd.DataFrame,
                                local_fills: pd.DataFrame, local_positions: pd.DataFrame,
                                local_cash: float, cash_tolerance: float = 0.01) -> dict[str, object]:
    """Compare the six offline account views and halt on every unexplained difference."""
    violations: list[dict[str, object]] = []
    broker_cash = float(pd.to_numeric(statement.cash["available_cash"], errors="raise").iloc[-1])
    if abs(float(local_cash) - broker_cash) > cash_tolerance:
        violations.append({"scope": "CASH", "local": local_cash, "broker": broker_cash, "reason": "cash_mismatch"})

    def shares(frame: pd.DataFrame) -> dict[str, int]:
        if frame.empty:
            return {}
        return frame.assign(symbol=frame["symbol"].astype(str)).groupby("symbol")["shares"].sum().astype(int).to_dict()

    local_position_map = shares(local_positions)
    broker_position_map = shares(statement.positions)
    for symbol in sorted(set(local_position_map) | set(broker_position_map)):
        if local_position_map.get(symbol, 0) != broker_position_map.get(symbol, 0):
            violations.append({"scope": "POSITION", "symbol": symbol,
                               "local": local_position_map.get(symbol, 0), "broker": broker_position_map.get(symbol, 0),
                               "reason": "position_mismatch"})
    broker_order_ids = set(statement.orders["broker_order_id"].astype(str))
    local_broker_order_ids = set(local_orders.get("broker_order_id", pd.Series(dtype=str)).dropna().astype(str))
    for order_id in sorted(broker_order_ids ^ local_broker_order_ids):
        violations.append({"scope": "ORDER", "broker_order_id": order_id, "reason": "order_set_mismatch"})
    broker_fill_ids = set(statement.fills["broker_fill_id"].astype(str))
    local_fill_ids = set(local_fills.get("broker_fill_id", pd.Series(dtype=str)).dropna().astype(str))
    for fill_id in sorted(broker_fill_ids ^ local_fill_ids):
        violations.append({"scope": "FILL", "broker_fill_id": fill_id, "reason": "fill_set_mismatch"})
    target_map = shares(target_positions) if "shares" in target_positions else {}
    target_gap = {symbol: target_map.get(symbol, 0) - local_position_map.get(symbol, 0)
                  for symbol in set(target_map) | set(local_position_map)
                  if target_map.get(symbol, 0) != local_position_map.get(symbol, 0)}
    # A target/local difference is explainable only by the remaining shares of
    # a live local manual order.  This keeps desired targets from being treated
    # as broker truth while still detecting missing or stray manual orders.
    pending: dict[str, int] = {}
    active = {"DRAFT", "RISK_APPROVED", "MANUAL_SUBMITTED", "PARTIAL_FILL"}
    if not local_orders.empty and {"symbol", "side"}.issubset(local_orders.columns):
        for row in local_orders.to_dict("records"):
            status = str(row.get("order_status", row.get("status", ""))).upper()
            if status not in active:
                continue
            planned = int(row.get("order_shares", row.get("shares", row.get("planned_shares", 0))) or 0)
            filled = int(row.get("filled_shares", 0) or 0)
            remaining = max(0, planned - filled)
            sign = 1 if str(row.get("side", "")).upper() == "BUY" else -1
            symbol = str(row["symbol"])
            pending[symbol] = pending.get(symbol, 0) + sign * remaining
    for symbol, gap in target_gap.items():
        if gap != pending.get(symbol, 0):
            violations.append({"scope": "TARGET", "symbol": symbol, "target_gap": gap,
                               "pending_order_delta": pending.get(symbol, 0),
                               "reason": "target_gap_unexplained"})
    return {"status": "VERIFIED" if not violations else "HALT_NEW_ORDERS",
            "production_state": "READY" if not violations else "HALT_NEW_ORDERS",
            "target_vs_local_position_gap": target_gap,
            "source_hashes": statement.source_hashes, "violations": violations}
