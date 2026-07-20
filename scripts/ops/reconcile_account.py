#!/usr/bin/env python3
"""Read-only account reconciliation.

The former script deleted and rebuilt ``live_positions``.  Reconciliation is
now evidence, never a repair action: mismatches halt new orders and require a
separate, reviewed maintenance task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sina.live_tracker import live_tracker_db as db
from sina.live_tracker.live_tracker_config import LIVE_CONFIG


def reconcile_records(
    trades: list[dict],
    stored_positions: list[dict],
    *,
    initial_capital: float,
) -> dict[str, object]:
    cash = float(initial_capital)
    expected: dict[str, int] = {}
    violations: list[dict[str, object]] = []
    for trade in sorted(trades, key=lambda row: (str(row.get("trade_date")), int(row.get("id") or 0))):
        symbol = str(trade.get("symbol") or "")
        shares = int(trade.get("shares") or 0)
        amount = float(trade.get("amount") or 0)
        commission = float(trade.get("commission") or 0)
        direction = str(trade.get("direction") or "").lower()
        if not symbol or shares <= 0 or amount < 0 or commission < 0 or direction not in {"buy", "sell"}:
            violations.append({"scope": "TRADE", "key": trade.get("id"), "reason": "invalid_trade_record"})
            continue
        if direction == "buy":
            cash -= amount + commission
            expected[symbol] = expected.get(symbol, 0) + shares
        else:
            if expected.get(symbol, 0) < shares:
                violations.append({"scope": "TRADE", "key": trade.get("id"), "reason": "sell_exceeds_replayed_position"})
            cash += amount - commission
            expected[symbol] = expected.get(symbol, 0) - shares
            if expected[symbol] == 0:
                del expected[symbol]
    actual = {str(row.get("symbol")): int(row.get("shares") or 0) for row in stored_positions}
    for symbol in sorted(set(expected) | set(actual)):
        if expected.get(symbol, 0) != actual.get(symbol, 0):
            violations.append({
                "scope": "POSITION", "key": symbol,
                "expected_shares": expected.get(symbol, 0), "actual_shares": actual.get(symbol, 0),
                "reason": "position_share_mismatch",
            })
    return {
        "status": "VERIFIED" if not violations else "HALT_NEW_ORDERS",
        "production_state": "READY" if not violations else "HALT_NEW_ORDERS",
        "expected_cash_before_marks": round(cash, 2),
        "expected_positions": expected,
        "actual_positions": actual,
        "violations": violations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local account reconciliation")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = reconcile_records(
        db.get_trades(), db.get_all_positions(),
        initial_capital=float(LIVE_CONFIG["initial_capital"]),
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if report["status"] != "VERIFIED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
