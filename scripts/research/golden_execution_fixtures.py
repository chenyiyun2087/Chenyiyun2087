"""Small deterministic event fixture shared by adapter parity tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scripts.research.canonical_execution_adapters import adapt_events, assert_adapter_parity


def golden_events() -> list[dict[str, Any]]:
    return [
        {"event_type": "order", "order_id": "golden-o1", "symbol": "600000.SH", "side": "BUY", "shares": 100, "signal_date": "2026-01-05", "execution_date": "2026-01-06", "trading_dates": ["2026-01-05", "2026-01-06", "2026-01-07"], "planned_price": 10.0, "planned_notional": 1000.0},
        {"event_type": "fill", "fill_id": "golden-f1", "order_id": "golden-o1", "symbol": "600000.SH", "side": "BUY", "shares": 100, "raw_price": 10.1, "execution_date": "2026-01-06", "costs": {"commission": 5.0, "transfer_fee": 0.01}, "canonical_kernel_id": "ashare_canonical_economic_kernel", "canonical_kernel_version": "1.0.0", "kernel_execution_sha256": "a" * 64},
        {"event_type": "corporate_action", "symbol": "600000.SH", "action_type": "dividend_cash", "ex_date": "2026-01-07", "cash_per_share": 0.1, "source_event_id": "golden-ca1", "source_complete": True, "event_hash": "golden-ca-hash"},
        {"event_type": "reject", "order_id": "golden-o2", "symbol": "000001", "side": "BUY", "execution_date": "2026-01-06", "reason": "limit_up_block", "rejected_shares": 100},
    ]


def golden_adapter_records() -> list[dict[str, Any]]:
    return adapt_events(golden_events(), trusted=True, source="golden")


def assert_golden_adapter_parity() -> None:
    events = golden_events()
    # Simulate local, legacy, JoinQuant and shadow naming differences while
    # retaining the same economic events.
    local = deepcopy(events)
    legacy = deepcopy(events)
    jq = deepcopy(events)
    shadow = deepcopy(events)
    for event in legacy:
        if event["event_type"] == "order":
            event["ts_code"] = event.pop("symbol")
            event["qty"] = event.pop("shares")
    for event in jq:
        if event["event_type"] == "fill":
            event["filled_shares"] = event.pop("shares")
            event["filled_price"] = event.pop("raw_price")
            event["raw_price_basis"] = "raw_open"
    assert_adapter_parity(local, legacy, jq, shadow)


__all__ = ["golden_events", "golden_adapter_records", "assert_golden_adapter_parity"]
