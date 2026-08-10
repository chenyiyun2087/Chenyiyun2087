"""One-way adapters into the canonical economic execution contract.

Legacy/local/JoinQuant/shadow lanes may remain diagnostic implementations, but
they cannot be treated as trusted evidence until their events pass through this
module.  In particular, a same-day order or adjusted-price fill fails closed
in trusted mode.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    CANONICAL_SCHEMA_VERSION,
    CorporateAction,
    Fill,
    NAV,
    Order,
    Position,
    Reject,
    canonical_hash,
    canonical_record,
    deterministic_order_id,
    normalize_symbol,
    validate_t_plus_one,
)


def _get(payload: Mapping[str, Any] | object, *names: str, default: Any = None) -> Any:
    if isinstance(payload, Mapping):
        for name in names:
            if name in payload and payload[name] is not None:
                return payload[name]
        return default
    for name in names:
        value = getattr(payload, name, None)
        if value is not None:
            return value
    return default


def adapt_order(payload: Mapping[str, Any] | object, *, trusted: bool = True, source: str = "legacy") -> Order:
    signal_date = _get(payload, "signal_date", "trade_date", "created_date")
    expected_execution = _get(payload, "expected_execution_date", default=None)
    execution_date = _get(payload, "execution_date", "fill_date", default=expected_execution)
    if signal_date is None or execution_date is None:
        raise ValueError("canonical_order_dates_missing")
    trading_dates = _get(payload, "trading_dates", "sse_open_dates", default=None)
    if trusted:
        if trading_dates is None and expected_execution is None:
            raise ValueError(f"{source}_trading_dates_or_expected_execution_required")
        if expected_execution is not None and str(execution_date) != str(expected_execution):
            raise ValueError(f"{source}_execution_date_mismatch_expected")
        # An explicitly supplied expected date is itself a precomputed T+1
        # contract.  Still call the validator with the smallest explicit
        # calendar so same-day/ordering checks cannot be bypassed.
        validate_t_plus_one(
            signal_date,
            execution_date,
            trading_dates if trading_dates is not None else [signal_date, execution_date],
        )
    shares = _get(payload, "shares", "planned_shares", "quantity", "qty")
    symbol = _get(payload, "symbol", "ts_code")
    side = _get(payload, "side", "direction")
    order_id = _get(payload, "order_id", "id", "broker_order_id", default="")
    if not order_id:
        order_id = deterministic_order_id(symbol, side, shares, signal_date, execution_date, sequence=int(_get(payload, "sequence", default=0) or 0))
    try:
        result = Order(
            order_id=str(order_id), symbol=str(symbol), side=str(side), shares=int(shares),
            signal_date=str(signal_date), execution_date=str(execution_date),
            planned_price=_get(payload, "planned_price", "limit_price", "price", default=0),
            planned_notional=_get(payload, "planned_notional", "notional", "amount", default=0),
            lot_size=int(_get(payload, "lot_size", default=100) or 100),
            status=str(_get(payload, "status", "order_status", default="PLANNED")),
            release_id=str(_get(payload, "release_id", default="")), run_id=str(_get(payload, "run_id", default="")),
            cost_model_id=str(_get(payload, "cost_model_id", default="")),
            sse_open_dates=trading_dates,
        )
    except ValueError as exc:
        # Canonical Order enforces T+1.  Keep a useful source marker in the
        # exception while preserving fail-closed behaviour.
        raise ValueError(f"{source}_order_rejected:{exc}") from exc
    return result


def adapt_fill(payload: Mapping[str, Any] | object, *, trusted: bool = True, source: str = "legacy") -> Fill:
    raw_explicit = _get(payload, "raw_price", "raw_open", default=None)
    raw_basis = str(_get(payload, "raw_price_basis", "price_basis", default="")).lower()
    if trusted and raw_explicit is None and raw_basis != "raw_open":
        raise ValueError(f"{source}_raw_price_proof_required")
    if trusted and raw_explicit is None and _get(payload, "adjusted_price", "adj_open", "adj_close", default=None) is not None:
        raise ValueError(f"{source}_adjusted_fill_forbidden")
    raw_price = raw_explicit if raw_explicit is not None else _get(payload, "filled_price", "price", default=None)
    if raw_price is None:
        raise ValueError(f"{source}_raw_price_missing")
    if trusted:
        kernel_id = _get(payload, "canonical_kernel_id", "kernel_id", default="")
        kernel_version = _get(payload, "canonical_kernel_version", "kernel_version", default="")
        proof = _get(payload, "kernel_execution_sha256", default="")
        if str(kernel_id) != CANONICAL_KERNEL_ID or str(kernel_version) != CANONICAL_KERNEL_VERSION:
            raise ValueError(f"{source}_canonical_kernel_identity_required")
        if not isinstance(proof, str) or len(proof) != 64:
            raise ValueError(f"{source}_canonical_kernel_execution_proof_required")
    order_id = _get(payload, "order_id", "broker_order_id")
    symbol = _get(payload, "symbol", "ts_code")
    side = _get(payload, "side", "direction")
    shares = _get(payload, "shares", "filled_shares", "quantity", "qty")
    execution_date = _get(payload, "execution_date", "trade_date", "fill_date")
    fill_id = _get(payload, "fill_id", "broker_fill_id", "id", default="")
    if not fill_id:
        fill_id = "fill_" + canonical_hash({"order_id": order_id, "symbol": normalize_symbol(symbol), "date": str(execution_date), "shares": int(shares), "price": str(raw_price)})[:32]
    return Fill(
        fill_id=str(fill_id), order_id=str(order_id), symbol=str(symbol), side=str(side), shares=int(shares),
        price=raw_price, execution_date=str(execution_date),
        notional=_get(payload, "filled_notional", "notional", "amount", default=None),
        costs=_get(payload, "costs", default={}) or {}, raw_price_basis="raw_open",
        fill_status=str(_get(payload, "fill_status", "order_status", default="FILLED")),
    )


def adapt_reject(payload: Mapping[str, Any] | object, *, source: str = "legacy") -> Reject:
    order_id = _get(payload, "order_id", "broker_order_id")
    symbol = _get(payload, "symbol", "ts_code", default="000000")
    side = _get(payload, "side", "direction", default="BUY")
    execution_date = _get(payload, "execution_date", "trade_date", default="1970-01-01")
    reason = _get(payload, "reason", "reject_reason", "cancel_reason")
    if not reason:
        raise ValueError(f"{source}_reject_reason_missing")
    return Reject(order_id=str(order_id), symbol=str(symbol), side=str(side), execution_date=str(execution_date), reason=str(reason), rejected_shares=int(_get(payload, "rejected_shares", "remaining_shares", default=0) or 0))


def adapt_corporate_action(payload: Mapping[str, Any] | object, *, trusted: bool = True, source: str = "legacy") -> CorporateAction:
    source_complete = _get(payload, "source_complete", default=None)
    source_event_id = _get(payload, "source_event_id", "event_id", default=None)
    event_hash = _get(payload, "event_hash", default=None)
    if trusted and (source_complete is not True or not str(source_event_id or "").strip() or not str(event_hash or "").strip()):
        raise ValueError(f"{source}_corporate_action_proof_required")
    return CorporateAction(
        symbol=str(_get(payload, "symbol", "ts_code")), ex_date=str(_get(payload, "ex_date", "trade_date", "effective_date")),
        action_type=str(_get(payload, "action_type", "corporate_action_type", default="dividend_cash")).lower(),
        source_event_id=str(source_event_id or ""),
        cash_per_share=_get(payload, "cash_per_share", "cash_dividend", default=0),
        share_ratio=_get(payload, "share_ratio", "stock_ratio", "bonus_ratio", default=0),
        rights_ratio=_get(payload, "rights_ratio", "rights_issue_ratio", default=0),
        rights_price=_get(payload, "rights_price", "rights_issue_price", default=None),
        split_ratio=_get(payload, "split_ratio", default=0),
        settlement_price=_get(payload, "settlement_price", default=None),
        new_symbol=str(_get(payload, "new_symbol", "new_ts_code", default="") or ""),
        source_complete=bool(source_complete) if source_complete is not None else False, event_hash=str(event_hash or ""),
    )


def adapt_event(event: Mapping[str, Any] | object, *, trusted: bool = True, source: str = "legacy") -> dict[str, Any]:
    kind = str(_get(event, "event_type", "type", "kind", default="")).lower()
    if kind in {"order", "planned", "submit", "buy", "sell"}:
        record = adapt_order(event, trusted=trusted, source=source)
        record_type = "order"
    elif kind in {"fill", "trade", "filled", "partial_fill"}:
        record = adapt_fill(event, trusted=trusted, source=source)
        record_type = "fill"
    elif kind in {"reject", "rejected", "cancel", "cancelled"}:
        record = adapt_reject(event, source=source)
        record_type = "reject"
    elif kind in {"corporate_action", "ca", "dividend", "split", "bonus", "delist"} or _get(event, "action_type", "corporate_action_type") is not None:
        record = adapt_corporate_action(event, trusted=trusted, source=source)
        record_type = "corporate_action"
    else:
        raise ValueError(f"{source}_event_type_unsupported:{kind}")
    return {"record_type": record_type, **canonical_record(record), "record_hash": record.record_hash}


def adapt_events(events: Iterable[Mapping[str, Any] | object], *, trusted: bool = True, source: str = "legacy") -> list[dict[str, Any]]:
    return [adapt_event(event, trusted=trusted, source=source) for event in events]


def assert_adapter_parity(*event_sets: Iterable[Mapping[str, Any] | object]) -> None:
    """Require local/legacy/JQ/shadow adapters to produce equal records."""
    normalized = [adapt_events(events, trusted=True) for events in event_sets]
    if not normalized:
        return
    baseline = [canonical_hash(item) for item in normalized[0]]
    for index, records in enumerate(normalized[1:], 1):
        hashes = [canonical_hash(item) for item in records]
        if hashes != baseline:
            raise ValueError(f"adapter_golden_parity_mismatch:{index}")


__all__ = ["adapt_order", "adapt_fill", "adapt_reject", "adapt_corporate_action", "adapt_event", "adapt_events", "assert_adapter_parity"]
