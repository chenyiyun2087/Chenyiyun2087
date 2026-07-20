#!/usr/bin/env python3
"""Run strict and independent ledgers from one frozen input package."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd

from runtime.independent_ledger import replay_orders
from runtime.contracts import ReleaseIdentity
from runtime.ledger_reconciliation import reconcile_ledgers
from scripts.research.strict_execution_ledger import CorporateAction, ExecutionLedger, PrecommitOrder


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def _flag(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n", ""}:
            return False
        raise ValueError(f"primary_invalid_boolean:{value}")
    if pd.isna(value):
        raise ValueError("primary_missing_boolean")
    return bool(value)


def _rounded_price(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick


def _primary_replay(orders: pd.DataFrame, market: pd.DataFrame, corporate_actions: pd.DataFrame,
                    initial_capital: float) -> dict[str, object]:
    ledger = ExecutionLedger(cash=float(initial_capital))
    trades: list[dict[str, object]] = []
    positions: list[dict[str, object]] = []
    rejections: list[dict[str, object]] = []
    nav_rows: list[dict[str, object]] = []
    market = market.copy()
    market["trade_date"] = market["trade_date"].astype(str)
    orders = orders.copy()
    orders["execution_date"] = orders["execution_date"].astype(str)
    actions = corporate_actions.copy()
    if not actions.empty:
        actions["trade_date"] = actions["trade_date"].astype(str)
    index = market.set_index(["trade_date", "symbol"], drop=False)
    for trade_date in sorted(market["trade_date"].unique()):
        day_actions = []
        if not actions.empty:
            for row in actions[actions["trade_date"].eq(trade_date)].to_dict("records"):
                day_actions.append(CorporateAction(
                    symbol=str(row["symbol"]), ex_date=trade_date,
                    action_type=str(row.get("action_type") or "dividend_cash"),
                    source_event_id=str(row.get("source_event_id") or f"{row['symbol']}:{trade_date}"),
                    cash_per_share=float(row.get("cash_per_share") or 0),
                    stock_ratio=float(row.get("share_ratio") or 0),
                    rights_ratio=float(row.get("rights_ratio") or 0),
                    rights_price=float(row["rights_price"]) if pd.notna(row.get("rights_price")) else None,
                    split_ratio=float(row.get("share_ratio") or 0),
                    settlement_price=float(row["settlement_price"]) if pd.notna(row.get("settlement_price")) else None,
                    event_hash=str(row.get("event_hash") or ""),
                ))
        if day_actions:
            ledger.apply_corporate_actions(day_actions)
        for row in orders[orders["execution_date"].eq(trade_date)].sort_values("order_id").to_dict("records"):
            order = PrecommitOrder(
                symbol=str(row["symbol"]), side=str(row["side"]).upper(), planned_shares=int(row["shares"]),
                planned_price=float(row.get("planned_price") or 0), planned_notional=float(row.get("planned_notional") or 0),
                planned_fee=0.0, signal_date=row.get("signal_date") or "", execution_date=trade_date,
                order_id=str(row["order_id"]), cost_rate=float(row["cost_rate"]), lot_size=int(row.get("lot_size") or 1),
            )
            ledger.plan(order)
            try:
                snap = index.loc[(trade_date, order.symbol)]
            except KeyError:
                result = ledger.execute(order, None, False, order.cost_rate, "missing_market_snapshot", order.lot_size)
            else:
                tradable = _flag(snap["is_tradable"]) and _flag(snap["is_listed"]) and not _flag(snap["is_suspended"])
                open_price = float(snap["raw_open"]) if pd.notna(snap["raw_open"]) else None
                prev = float(snap["prev_raw_close"]) if pd.notna(snap["prev_raw_close"]) else 0.0
                ratio = Decimal("0.05") if _flag(snap["is_st"]) else Decimal("0.30") if str(order.symbol).startswith(("4", "8", "92")) else Decimal("0.20") if str(order.symbol).startswith(("300", "301", "688", "689")) else Decimal("0.10")
                tick = Decimal(str(snap.get("price_tick") or "0.01"))
                upper = _rounded_price(Decimal(str(prev)) * (Decimal("1") + ratio), tick)
                lower = _rounded_price(Decimal(str(prev)) * (Decimal("1") - ratio), tick)
                blocked = open_price is not None and ((order.side == "BUY" and Decimal(str(open_price)) >= upper) or (order.side == "SELL" and Decimal(str(open_price)) <= lower))
                result = ledger.execute(order, open_price, tradable, order.cost_rate, "limit_block" if blocked else "", order.lot_size)
            if int(result["filled_shares"] or 0) > 0:
                trades.append({"order_id": order.order_id, "trade_date": trade_date, "symbol": order.symbol,
                               "side": order.side, **{key: result[key] for key in ("filled_shares", "filled_price", "filled_notional", "fee")}})
            elif result.get("reject_reason"):
                rejections.append({"order_id": order.order_id, "trade_date": trade_date,
                                   "symbol": order.symbol, "reason": result["reject_reason"]})
        marks = {str(row.symbol): float(row.raw_close) for row in market[market["trade_date"].eq(trade_date)].itertuples(index=False)}
        for symbol, shares in sorted(ledger.shares.items()):
            if shares:
                positions.append({"trade_date": trade_date, "symbol": symbol, "shares": shares,
                                  "close_price": marks[symbol], "market_value": shares * marks[symbol]})
        nav = ledger.equity(marks)
        nav_rows.append({"trade_date": trade_date, "cash": ledger.cash,
                         "market_value": nav - ledger.cash, "nav": nav})
    nav_frame = pd.DataFrame(nav_rows)
    series = nav_frame["nav"] if not nav_frame.empty else pd.Series(dtype=float)
    metrics = {"total_return": float(series.iloc[-1] / initial_capital - 1) if len(series) else 0.0,
               "max_drawdown": float((series / series.cummax() - 1).min()) if len(series) else 0.0,
               "trade_count": float(len(trades))}
    return {
        "trades": pd.DataFrame(trades, columns=["order_id", "trade_date", "symbol", "side", "filled_shares", "filled_price", "filled_notional", "fee"]),
        "positions": pd.DataFrame(positions, columns=["trade_date", "symbol", "shares", "close_price", "market_value"]),
        "rejections": pd.DataFrame(rejections, columns=["order_id", "trade_date", "symbol", "reason"]),
        "nav": nav_frame, "metrics": metrics,
    }


def run(package: Path, output: Path) -> dict[str, object]:
    required_files = {
        "release_identity.json", "orders.parquet", "market_snapshot.parquet",
        "corporate_actions.parquet", "security_lifecycle.parquet", "calendar.parquet",
        "cost_model.json", "execution_model.json",
    }
    missing_files = sorted(name for name in required_files if not (package / name).is_file())
    if missing_files:
        raise FileNotFoundError(f"dual_ledger_package_incomplete:{','.join(missing_files)}")
    identity_payload = json.loads((package / "release_identity.json").read_text(encoding="utf-8"))
    identity = ReleaseIdentity(**identity_payload)
    component_hashes = {
        "data_snapshot_sha": package / "market_snapshot.parquet",
        "calendar_snapshot_sha": package / "calendar.parquet",
        "corporate_action_snapshot_sha": package / "corporate_actions.parquet",
        "lifecycle_snapshot_sha": package / "security_lifecycle.parquet",
    }
    for field, path in component_hashes.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if getattr(identity, field) != actual:
            raise ValueError(f"dual_ledger_identity_hash_mismatch:{field}")
    orders = _read(package / "orders.parquet")
    missing_identity = sorted({"release_id", "run_id"} - set(orders.columns))
    if missing_identity:
        raise ValueError(f"dual_ledger_orders_identity_missing:{','.join(missing_identity)}")
    if set(orders["release_id"].astype(str)) != {identity.release_id} or set(orders["run_id"].astype(str)) != {identity.run_id}:
        raise ValueError("dual_ledger_orders_identity_mismatch")
    market = _read(package / "market_snapshot.parquet")
    actions = _read(package / "corporate_actions.parquet")
    primary = _primary_replay(orders, market, actions, float(identity.initial_capital))
    oracle = replay_orders(orders, market, initial_capital=float(identity.initial_capital), corporate_actions=actions)
    report = reconcile_ledgers(
        release_id=identity.release_id, run_id=identity.run_id,
        primary_trades=primary["trades"], oracle_trades=oracle.trades,
        primary_positions=primary["positions"], oracle_positions=oracle.positions,
        primary_nav=primary["nav"], oracle_nav=oracle.daily_nav,
        primary_metrics=primary["metrics"], oracle_metrics=oracle.metrics,
        primary_rejections=primary["rejections"], oracle_rejections=oracle.rejections,
    )
    output.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    (output / "dual_ledger_reconciliation.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pd.DataFrame([item for item in payload["differences"] if item["scope"] == "ORDER"]).to_csv(output / "order_reconciliation.csv", index=False)
    pd.DataFrame([item for item in payload["differences"] if item["scope"] == "POSITION"]).to_csv(output / "position_reconciliation.csv", index=False)
    pd.DataFrame([item for item in payload["differences"] if item["scope"] in {"NAV", "CASH"}]).to_csv(output / "nav_reconciliation.csv", index=False)
    pd.DataFrame(payload["differences"][:1]).to_csv(output / "first_divergence_report.csv", index=False)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.package, args.output)
    print(f"dual_ledger_status={report['status']}")
    if report["status"] != "VERIFIED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
