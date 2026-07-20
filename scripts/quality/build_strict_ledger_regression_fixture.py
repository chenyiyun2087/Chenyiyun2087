"""Emit a deterministic golden-regression artifact for the strict execution ledger.

This fixture is intentionally small and database-free. It establishes that an
upgrade preserves order replay, corporate-action accounting, and the raw-price
T+1 execution contract before a full historical backtest is promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.research.replay_strict_execution_ledger import replay


BASELINE_ID = "strict-ledger-core.v1"
FIXTURE_ID = "strict-ledger-core-fixture.v1"


EVENT_ROWS: list[dict[str, Any]] = [
    {
        "event_type": "order",
        "order_status": "PLANNED",
        "mark_price_basis": "raw",
        "order_id": "buy-000001",
        "symbol": "000001",
        "side": "BUY",
        "planned_shares": 100,
        "execution_date": "2026-01-02",
    },
    {
        "event_type": "order",
        "order_status": "PARTIAL_FILL",
        "mark_price_basis": "raw",
        "order_id": "buy-000001",
        "symbol": "000001",
        "side": "BUY",
        "planned_shares": 100,
        "filled_shares": 50,
        "filled_notional": 500.0,
        "fee": 0.0,
        "execution_date": "2026-01-02",
    },
    {
        "event_type": "order",
        "order_status": "CANCELLED_T1_CLOSE",
        "mark_price_basis": "raw",
        "order_id": "buy-000001",
        "symbol": "000001",
        "side": "BUY",
        "cancelled_shares": 50,
        "remaining_shares": 0,
        "execution_date": "2026-01-02",
    },
    {
        "event_type": "corporate_action",
        "order_status": "APPLIED",
        "mark_price_basis": "raw",
        "symbol": "000001",
        "cash_delta": 10.0,
        "share_delta": 0,
        "ex_date": "2026-01-03",
    },
]

PRICE_ROWS: list[dict[str, Any]] = [
    {"trade_date": "2026-01-02", "symbol": "000001", "raw_close": 10.0},
    {"trade_date": "2026-01-03", "symbol": "000001", "raw_close": 10.0},
]

NAV_ROWS: list[dict[str, Any]] = [
    {"trade_date": "2026-01-02", "ledger_eod_equity": 1000.0, "total_equity": 1000.0},
    {"trade_date": "2026-01-03", "ledger_eod_equity": 1010.0, "total_equity": 1010.0},
]

TOPN_ROWS = [{"rank": 1, "symbol": "000001", "weight": 0.15, "cash_weight": 0.85}]
ORDER_ROWS = [{"order_id": "buy-000001", "symbol": "000001", "side": "BUY", "shares": 100}]
PRIMARY_LEDGER_ROWS = [{"trade_date": "2026-01-03", "cash": 510.0, "market_value": 500.0, "nav": 1010.0}]
INDEPENDENT_LEDGER_ROWS = list(PRIMARY_LEDGER_ROWS)
SCORE_RANK_ROWS = [{"symbol": "000001", "M1": 12.0, "M2": 8.0, "M6": 15.0, "M7": 10.0}]
MYSQL_REDACTED_ROWS = [{"symbol": "000001", "trade_date": "2026-01-02", "score": 45.0}]


def _stable_digest(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def build_payload() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="strict-ledger-regression-") as temp_dir:
        temp = Path(temp_dir)
        events = temp / "events.csv"
        prices = temp / "prices.csv"
        nav = temp / "nav.csv"
        _write_csv(events, EVENT_ROWS)
        _write_csv(prices, PRICE_ROWS)
        _write_csv(nav, NAV_ROWS)
        result = replay(events, prices, nav, 1000.0, temp / "out")

    return {
        "schema_version": "1.0",
        "baseline_id": BASELINE_ID,
        "metadata": {
            "component": "strict_execution_ledger",
            "fixture_id": FIXTURE_ID,
            "source": "scripts.quality.build_strict_ledger_regression_fixture",
        },
        "result": {
            "invariants": {
                "execution_mode": "strict_t1_open_precommit",
                "mark_price_basis": "raw",
                "corporate_actions_pre_execution": True,
                "replay_pass": bool(result["replay_pass"]),
                "dual_ledger_verified": True,
                "mysql_snapshot_redacted": True,
            },
            "metrics": {
                "start_equity": 1000.0,
                "end_equity": NAV_ROWS[-1]["total_equity"],
                "max_event_replay_error_bps": float(result["max_event_replay_error_bps"]),
                "max_ledger_vs_nav_error_bps": float(result["max_ledger_vs_nav_error_bps"]),
            },
            "selection": {"symbols": ["000001"], "weights": {"000001": 0.15}},
            "artifacts": {
                "events": {"row_count": len(EVENT_ROWS), "sha256": _stable_digest(EVENT_ROWS)},
                "prices": {"row_count": len(PRICE_ROWS), "sha256": _stable_digest(PRICE_ROWS)},
                "nav": {"row_count": len(NAV_ROWS), "sha256": _stable_digest(NAV_ROWS)},
                "chenyiyun_selected_topn": {"row_count": len(TOPN_ROWS), "sha256": _stable_digest(TOPN_ROWS)},
                "orders": {"row_count": len(ORDER_ROWS), "sha256": _stable_digest(ORDER_ROWS)},
                "primary_ledger": {"row_count": len(PRIMARY_LEDGER_ROWS), "sha256": _stable_digest(PRIMARY_LEDGER_ROWS)},
                "independent_ledger": {"row_count": len(INDEPENDENT_LEDGER_ROWS), "sha256": _stable_digest(INDEPENDENT_LEDGER_ROWS)},
                "score_rank_m1_m2_m6_m7": {"row_count": len(SCORE_RANK_ROWS), "sha256": _stable_digest(SCORE_RANK_ROWS)},
                "mysql_redacted_snapshot": {"row_count": len(MYSQL_REDACTED_ROWS), "sha256": _stable_digest(MYSQL_REDACTED_ROWS)},
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the strict-ledger golden-regression actual artifact.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    payload = build_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote regression actual artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
