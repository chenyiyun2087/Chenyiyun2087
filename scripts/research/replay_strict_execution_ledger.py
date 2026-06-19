"""Independently replay strict ledger exports using events and raw-price snapshots.

This intentionally does not import ``ExecutionLedger`` or the account simulator.
It is an audit replay, not a second execution engine.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


FINAL_REJECTS = {"REJECTED_T1_NOT_TRADABLE", "REJECTED_LIMIT_BLOCK"}
FREEZE = "CORPORATE_ACTION_FREEZE"


def _number(value: object, default: float = 0.0) -> float:
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) else default


def _date(value: object) -> str:
    return str(pd.to_datetime(value, errors="coerce").date()) if pd.notna(pd.to_datetime(value, errors="coerce")) else ""


def _equity(cash: float, shares: dict[str, int], prices: dict[str, float]) -> float:
    return cash + sum(int(quantity) * float(prices.get(symbol, 0.0)) for symbol, quantity in shares.items())


def replay(events_path: Path, prices_path: Path, nav_path: Path, initial_cash: float, output_dir: Path) -> dict[str, object]:
    events = pd.read_csv(events_path).fillna("")
    prices = pd.read_csv(prices_path)
    nav = pd.read_csv(nav_path)
    for frame in (events, prices, nav):
        if "strategy" in frame.columns:
            strict_mask = frame["strategy"].astype(str).str.contains("strict_precommit", na=False)
            if strict_mask.any():
                frame.drop(frame.index[~strict_mask], inplace=True)
    required_events = {"event_type", "order_status", "mark_price_basis"}
    if missing := sorted(required_events - set(events.columns)):
        raise RuntimeError(f"ledger events missing fields: {missing}")
    required_prices = {"trade_date", "symbol", "raw_close"}
    if missing := sorted(required_prices - set(prices.columns)):
        raise RuntimeError(f"raw price snapshot missing fields: {missing}")

    events = events.reset_index(names="event_sequence")
    cash, shares, orders, failures = float(initial_cash), {}, {}, []
    daily = []
    price_dates = sorted(pd.to_datetime(prices["trade_date"], errors="coerce").dropna().dt.date.unique())
    events_by_date: dict[str, pd.DataFrame] = {}
    for date, frame in events.groupby(events.apply(lambda row: _date(row.get("execution_date") or row.get("ex_date") or row.get("event_date")), axis=1)):
        if date:
            events_by_date[date] = frame.sort_values("event_sequence")

    for date in price_dates:
        date_key = str(date)
        for _, event in events_by_date.get(date_key, pd.DataFrame()).iterrows():
            event_type, status = str(event.get("event_type")), str(event.get("order_status"))
            if str(event.get("mark_price_basis")) != "raw":
                failures.append(f"non_raw_mark_basis:{event.get('order_id', '')}")
            if event_type == "corporate_action":
                if status == FREEZE:
                    failures.append(f"corporate_action_freeze:{date_key}:{event.get('source_reason', '')}")
                    continue
                symbol = str(event.get("symbol") or "").zfill(6)
                cash += _number(event.get("cash_delta"))
                shares[symbol] = int(shares.get(symbol, 0)) + int(_number(event.get("share_delta")))
                continue
            if event_type != "order":
                failures.append(f"unknown_event_type:{event_type}")
                continue
            order_id = str(event.get("order_id") or "")
            if not order_id:
                failures.append("missing_order_id")
                continue
            planned = int(_number(event.get("planned_shares")))
            order = orders.setdefault(order_id, {"planned": planned, "filled": 0, "cancelled": 0, "rejected": False})
            order["planned"] = max(order["planned"], planned)
            if status == "PLANNED":
                continue
            if status in FINAL_REJECTS:
                order["rejected"] = True
                if int(_number(event.get("filled_shares"))) != 0:
                    failures.append(f"rejected_order_filled:{order_id}")
                continue
            if status in {"FILLED", "PARTIAL_FILL"}:
                filled = int(_number(event.get("filled_shares")))
                gross, fee = _number(event.get("filled_notional")), _number(event.get("fee"))
                symbol, side = str(event.get("symbol") or "").zfill(6), str(event.get("side"))
                if side == "BUY":
                    cash -= gross + fee
                    shares[symbol] = int(shares.get(symbol, 0)) + filled
                elif side == "SELL":
                    cash += gross - fee
                    shares[symbol] = int(shares.get(symbol, 0)) - filled
                else:
                    failures.append(f"unknown_side:{order_id}")
                order["filled"] += filled
                continue
            if status == "CANCELLED_T1_CLOSE":
                order["cancelled"] += int(_number(event.get("cancelled_shares")))
                if int(_number(event.get("remaining_shares"))) != 0:
                    failures.append(f"cancel_not_zero_remaining:{order_id}")
                continue
            failures.append(f"unknown_order_status:{status}:{order_id}")

        day = prices[pd.to_datetime(prices["trade_date"], errors="coerce").dt.date.eq(date)]
        raw = {str(row.symbol).zfill(6): _number(row.raw_close) for row in day.itertuples()}
        replayed_equity = _equity(cash, shares, raw)
        nav_day = nav[pd.to_datetime(nav["trade_date"], errors="coerce").dt.date.eq(date)]
        ledger_equity = _number(nav_day["ledger_eod_equity"].iloc[0], np.nan) if "ledger_eod_equity" in nav_day and not nav_day.empty else np.nan
        nav_equity = _number(nav_day["total_equity"].iloc[0], np.nan) if "total_equity" in nav_day and not nav_day.empty else np.nan
        replay_error = abs(replayed_equity - ledger_equity) / replayed_equity * 10_000 if replayed_equity > 0 and np.isfinite(ledger_equity) else np.nan
        nav_error = abs(ledger_equity - nav_equity) / ledger_equity * 10_000 if ledger_equity > 0 and np.isfinite(nav_equity) else np.nan
        daily.append({"trade_date": date_key, "replayed_cash": cash, "replayed_shares": json.dumps(shares, sort_keys=True),
                      "replayed_equity": replayed_equity, "ledger_equity": ledger_equity, "nav_equity": nav_equity,
                      "event_replay_error_bps": replay_error, "ledger_vs_nav_error_bps": nav_error})

    for order_id, order in orders.items():
        if order["planned"] != order["filled"] + order["cancelled"]:
            failures.append(f"order_conservation_failed:{order_id}")
        if order["rejected"] and order["filled"]:
            failures.append(f"rejected_order_filled:{order_id}")
    daily_frame = pd.DataFrame(daily)
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_frame.to_csv(output_dir / "strict_ledger_replay_daily.csv", index=False)
    result = {
        "replay_pass": not failures,
        "failure_count": len(failures), "failure_reasons": sorted(set(failures)),
        "max_event_replay_error_bps": float(pd.to_numeric(daily_frame.get("event_replay_error_bps"), errors="coerce").max()) if not daily_frame.empty else None,
        "max_ledger_vs_nav_error_bps": float(pd.to_numeric(daily_frame.get("ledger_vs_nav_error_bps"), errors="coerce").max()) if not daily_frame.empty else None,
        "order_count": len(orders), "output": str(output_dir / "strict_ledger_replay_daily.csv"),
    }
    (output_dir / "strict_ledger_replay_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Independently replay strict execution-ledger exports.")
    parser.add_argument("--ledger-events", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--nav", required=True, type=Path)
    parser.add_argument("--initial-cash", required=True, type=float)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.ledger_events, args.prices, args.nav, args.initial_cash, args.output_dir), ensure_ascii=False, indent=2))
