"""Canonical economic execution contract.

This module is deliberately small and dependency free.  It is the wire
contract used by the strict research ledger, the independent replay oracle,
and adapters for legacy/local/JoinQuant/shadow lanes.  A lane may keep its
native objects internally, but anything entering a trusted result must be
normalised to these records first.

The contract uses :class:`~decimal.Decimal` for money and quantities.  JSON
serialisation is deterministic (sorted keys, compact separators and explicit
Decimal strings) so a record hash is stable across Python versions and
between replay implementations.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Any, Mapping, Sequence


# Bump these only when the economic meaning of a field/rule changes.  Keep
# aliases for callers that used shorter names in early prototypes.
CANONICAL_KERNEL_ID = "ashare_canonical_economic_kernel"
CANONICAL_KERNEL_VERSION = "1.0.0"
CANONICAL_SCHEMA_VERSION = "canonical_execution_contract_v1"
KERNEL_ID = CANONICAL_KERNEL_ID
KERNEL_VERSION = CANONICAL_KERNEL_VERSION

DECIMAL_ZERO = Decimal("0")
CENT = Decimal("0.01")
DEFAULT_LOT_SIZE = 100


class CanonicalContractError(ValueError):
    """Raised when an input cannot be represented in the trusted contract."""


def _decimal(value: Any, *, name: str = "value", non_negative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CanonicalContractError(f"{name}_missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CanonicalContractError(f"{name}_not_decimal:{value!r}") from exc
    if not result.is_finite():
        raise CanonicalContractError(f"{name}_not_finite")
    if non_negative and result < 0:
        raise CanonicalContractError(f"{name}_negative")
    return result


def _money(value: Any, *, name: str = "money", non_negative: bool = False) -> Decimal:
    return _decimal(value, name=name, non_negative=non_negative).quantize(CENT, rounding=ROUND_HALF_UP)


def _date_string(value: Any, *, name: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        result = value.isoformat()
    else:
        result = str(value or "").strip()
    if not result:
        raise CanonicalContractError(f"{name}_missing")
    # We intentionally accept a timestamp (some broker feeds include it),
    # but compare the date portion for T/T+1 semantics.
    return result


def _day(value: Any) -> date:
    text = _date_string(value, name="date")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise CanonicalContractError(f"invalid_date:{text}") from exc


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").strip().upper()
    for suffix in (".SH", ".SZ", ".BJ", ".XSHG", ".XSHE"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    value = value.zfill(6)
    if not value.isdigit() or len(value) != 6:
        raise CanonicalContractError(f"invalid_symbol:{symbol}")
    return value


def _canonical_value(value: Any) -> Any:
    """Convert an arbitrary contract object to deterministic JSON values."""
    if isinstance(value, Decimal):
        # ``format(..., 'f')`` avoids exponent spelling differences.  Strip
        # insignificant trailing zeroes while retaining a valid zero.
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".") or "0"
        return text
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if is_dataclass(value):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=lambda key: str(key))}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical_value(item) for item in value), key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalContractError("non_finite_float")
        # Floats are converted through Decimal so 1.0 and Decimal("1.0") have
        # the same wire representation.
        return _canonical_value(Decimal(str(value)))
    if hasattr(value, "model_dump"):
        return _canonical_value(value.model_dump(mode="python"))
    return value


def canonical_json(value: Any) -> str:
    """Return compact, deterministic JSON for a contract record or payload."""
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    """SHA-256 hash of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    """Compatibility alias for :func:`canonical_hash`."""
    return canonical_hash(value)


def _record_dict(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        return {item.name: getattr(record, item.name) for item in fields(record)}
    if isinstance(record, Mapping):
        return dict(record)
    raise TypeError(f"unsupported_contract_record:{type(record)!r}")


def _record_hash(record: Any) -> str:
    return canonical_hash(_record_dict(record))


@dataclass(frozen=True)
class Order:
    """A planned order generated on T and executable only on T+1."""

    order_id: str
    symbol: str
    side: str
    shares: int
    signal_date: str
    execution_date: str
    planned_price: Decimal | str | float = DECIMAL_ZERO
    planned_notional: Decimal | str | float = DECIMAL_ZERO
    lot_size: int = DEFAULT_LOT_SIZE
    status: str = "PLANNED"
    release_id: str = ""
    run_id: str = ""
    cost_model_id: str = ""
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION
    sse_open_dates: Sequence[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", str(self.order_id).strip())
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        side = str(self.side).upper().strip()
        if side not in {"BUY", "SELL"}:
            raise CanonicalContractError(f"invalid_order_side:{self.side}")
        object.__setattr__(self, "side", side)
        if not self.order_id:
            raise CanonicalContractError("order_id_missing")
        if int(self.shares) <= 0:
            raise CanonicalContractError("order_shares_non_positive")
        if int(self.lot_size) <= 0:
            raise CanonicalContractError("order_lot_size_non_positive")
        signal = _date_string(self.signal_date, name="signal_date")
        execution = _date_string(self.execution_date, name="execution_date")
        if _day(execution) <= _day(signal):
            raise CanonicalContractError("same_day_execution_forbidden")
        if side == "BUY" and int(self.shares) % int(self.lot_size) != 0:
            raise CanonicalContractError("buy_shares_not_lot_multiple")
        if self.sse_open_dates is not None:
            validate_t_plus_one(signal, execution, self.sse_open_dates)
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        object.__setattr__(self, "shares", int(self.shares))
        object.__setattr__(self, "lot_size", int(self.lot_size))
        object.__setattr__(self, "planned_price", _decimal(self.planned_price, name="planned_price", non_negative=True))
        object.__setattr__(self, "planned_notional", _money(self.planned_notional, name="planned_notional", non_negative=True))

    @property
    def quantity(self) -> int:
        return self.shares

    @property
    def planned_shares(self) -> int:
        return self.shares

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "Order":
        return cls(
            order_id=str(payload.get("order_id") or payload.get("id") or ""),
            symbol=payload.get("symbol", payload.get("ts_code", "")),
            side=payload.get("side", payload.get("direction", "")),
            shares=int(payload.get("shares", payload.get("planned_shares", payload.get("qty", payload.get("quantity", 0))))),
            signal_date=payload.get("signal_date", payload.get("trade_date", "")),
            execution_date=payload.get("execution_date", payload.get("fill_date", "")),
            planned_price=payload.get("planned_price", payload.get("limit_price", payload.get("price", 0))),
            planned_notional=payload.get("planned_notional", payload.get("notional", 0)),
            lot_size=int(payload.get("lot_size", DEFAULT_LOT_SIZE) or DEFAULT_LOT_SIZE),
            status=str(payload.get("status", payload.get("order_status", "PLANNED"))),
            release_id=str(payload.get("release_id", "")), run_id=str(payload.get("run_id", "")),
            cost_model_id=str(payload.get("cost_model_id", "")),
            sse_open_dates=payload.get("sse_open_dates", payload.get("trading_dates")),
        )

    from_dict = from_mapping
    to_dict = as_dict


@dataclass(frozen=True)
class Fill:
    """A raw-price fill.  ``costs`` contains the component ledger."""

    fill_id: str
    order_id: str
    symbol: str
    side: str
    shares: int
    price: Decimal | str | float
    execution_date: str
    notional: Decimal | str | float | None = None
    costs: Mapping[str, Decimal | str | float] = field(default_factory=dict)
    raw_price_basis: str = "raw_open"
    fill_status: str = "FILLED"
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.fill_id).strip() or not str(self.order_id).strip():
            raise CanonicalContractError("fill_identity_missing")
        object.__setattr__(self, "fill_id", str(self.fill_id).strip())
        object.__setattr__(self, "order_id", str(self.order_id).strip())
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        side = str(self.side).upper().strip()
        if side not in {"BUY", "SELL"}:
            raise CanonicalContractError(f"invalid_fill_side:{self.side}")
        object.__setattr__(self, "side", side)
        if int(self.shares) <= 0:
            raise CanonicalContractError("fill_shares_non_positive")
        object.__setattr__(self, "shares", int(self.shares))
        price = _decimal(self.price, name="fill_price", non_negative=True)
        if price <= 0:
            raise CanonicalContractError("fill_price_non_positive")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "execution_date", _date_string(self.execution_date, name="execution_date"))
        notional = price * Decimal(self.shares) if self.notional is None else _money(self.notional, name="fill_notional", non_negative=True)
        object.__setattr__(self, "notional", notional)
        normalized_costs = {str(key): _money(value, name=f"cost_{key}", non_negative=True) for key, value in dict(self.costs).items()}
        object.__setattr__(self, "costs", normalized_costs)

    @property
    def filled_shares(self) -> int:
        return self.shares

    @property
    def filled_price(self) -> Decimal:
        return self.price

    @property
    def filled_notional(self) -> Decimal:
        return self.notional

    @property
    def total_cost(self) -> Decimal:
        return sum(self.costs.values(), DECIMAL_ZERO)

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    from_dict = from_mapping = classmethod(lambda cls, payload: cls(
        fill_id=str(payload.get("fill_id") or payload.get("broker_fill_id") or payload.get("id") or ""),
        order_id=str(payload.get("order_id") or payload.get("broker_order_id") or ""),
        symbol=payload.get("symbol", payload.get("ts_code", "")), side=payload.get("side", payload.get("direction", "")),
        shares=int(payload.get("shares", payload.get("filled_shares", payload.get("qty", 0)))),
        price=payload.get("price", payload.get("filled_price", payload.get("raw_price", 0))),
        execution_date=payload.get("execution_date", payload.get("trade_date", payload.get("fill_date", ""))),
        notional=payload.get("notional", payload.get("filled_notional", None)), costs=payload.get("costs", {}),
    ))
    to_dict = as_dict


@dataclass(frozen=True)
class Reject:
    order_id: str
    symbol: str
    side: str
    execution_date: str
    reason: str
    rejected_shares: int = 0
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.order_id).strip() or not str(self.reason).strip():
            raise CanonicalContractError("reject_identity_missing")
        object.__setattr__(self, "order_id", str(self.order_id).strip())
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        side = str(self.side).upper().strip()
        if side not in {"BUY", "SELL"}:
            raise CanonicalContractError(f"invalid_reject_side:{self.side}")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "execution_date", _date_string(self.execution_date, name="execution_date"))
        object.__setattr__(self, "reason", str(self.reason).strip())
        if int(self.rejected_shares) < 0:
            raise CanonicalContractError("rejected_shares_negative")
        object.__setattr__(self, "rejected_shares", int(self.rejected_shares))

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    from_dict = from_mapping = classmethod(lambda cls, payload: cls(
        order_id=str(payload.get("order_id") or payload.get("broker_order_id") or ""),
        symbol=payload.get("symbol", payload.get("ts_code", "000000")), side=payload.get("side", payload.get("direction", "BUY")),
        execution_date=payload.get("execution_date", payload.get("trade_date", "1970-01-01")),
        reason=payload.get("reason", payload.get("reject_reason", payload.get("cancel_reason", ""))),
        rejected_shares=int(payload.get("rejected_shares", payload.get("remaining_shares", 0)) or 0),
    ))
    to_dict = as_dict


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: str
    action_type: str = "dividend_cash"
    source_event_id: str = ""
    cash_per_share: Decimal | str | float = DECIMAL_ZERO
    share_ratio: Decimal | str | float = DECIMAL_ZERO
    rights_ratio: Decimal | str | float = DECIMAL_ZERO
    rights_price: Decimal | str | float | None = None
    split_ratio: Decimal | str | float = DECIMAL_ZERO
    settlement_price: Decimal | str | float | None = None
    new_symbol: str = ""
    source_complete: bool = True
    event_hash: str = ""
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "ex_date", _date_string(self.ex_date, name="ex_date"))
        action = str(self.action_type).strip().lower()
        allowed = {"dividend_cash", "stock_bonus", "split_merge", "rights_subscription", "share_conversion", "delist_cash_settlement", "delist_writeoff", "rename", "st_change"}
        if action not in allowed:
            raise CanonicalContractError(f"unknown_corporate_action_type:{action}")
        object.__setattr__(self, "action_type", action)
        object.__setattr__(self, "cash_per_share", _decimal(self.cash_per_share, name="cash_per_share", non_negative=True))
        object.__setattr__(self, "share_ratio", _decimal(self.share_ratio, name="share_ratio", non_negative=True))
        object.__setattr__(self, "rights_ratio", _decimal(self.rights_ratio, name="rights_ratio", non_negative=True))
        if self.rights_price is not None:
            object.__setattr__(self, "rights_price", _money(self.rights_price, name="rights_price", non_negative=True))
        object.__setattr__(self, "split_ratio", _decimal(self.split_ratio, name="split_ratio", non_negative=True))
        if self.settlement_price is not None:
            object.__setattr__(self, "settlement_price", _money(self.settlement_price, name="settlement_price", non_negative=True))
        if self.new_symbol:
            object.__setattr__(self, "new_symbol", normalize_symbol(self.new_symbol))
        if not self.event_hash:
            identity = {"symbol": self.symbol, "ex_date": self.ex_date, "source_event_id": self.source_event_id, "action_type": self.action_type}
            object.__setattr__(self, "event_hash", canonical_hash(identity))

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    to_dict = as_dict
    from_dict = from_mapping = classmethod(lambda cls, payload: cls(
        symbol=payload.get("symbol", payload.get("ts_code", "")),
        ex_date=payload.get("ex_date", payload.get("trade_date", payload.get("effective_date", ""))),
        action_type=payload.get("action_type", payload.get("corporate_action_type", "dividend_cash")),
        source_event_id=payload.get("source_event_id", payload.get("event_id", "")),
        cash_per_share=payload.get("cash_per_share", payload.get("cash_dividend", 0)),
        share_ratio=payload.get("share_ratio", payload.get("stock_ratio", payload.get("bonus_ratio", 0))),
        rights_ratio=payload.get("rights_ratio", payload.get("rights_issue_ratio", 0)),
        rights_price=payload.get("rights_price", payload.get("rights_issue_price", None)),
        split_ratio=payload.get("split_ratio", 0), settlement_price=payload.get("settlement_price", None),
        new_symbol=payload.get("new_symbol", payload.get("new_ts_code", "")), source_complete=payload.get("source_complete", True),
        event_hash=payload.get("event_hash", ""),
    ))


@dataclass(frozen=True)
class Position:
    symbol: str
    shares: int
    unit_cost: Decimal | str | float = DECIMAL_ZERO
    mark_price: Decimal | str | float = DECIMAL_ZERO
    market_value: Decimal | str | float | None = None
    as_of_date: str = ""
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if int(self.shares) < 0:
            raise CanonicalContractError("position_shares_negative")
        object.__setattr__(self, "shares", int(self.shares))
        object.__setattr__(self, "unit_cost", _money(self.unit_cost, name="unit_cost", non_negative=True))
        object.__setattr__(self, "mark_price", _money(self.mark_price, name="mark_price", non_negative=True))
        value = self.mark_price * Decimal(self.shares) if self.market_value is None else _money(self.market_value, name="market_value", non_negative=True)
        object.__setattr__(self, "market_value", value)
        if self.as_of_date:
            object.__setattr__(self, "as_of_date", _date_string(self.as_of_date, name="as_of_date"))

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    to_dict = as_dict


@dataclass(frozen=True)
class Cash:
    amount: Decimal | str | float
    as_of_date: str = ""
    delta: Decimal | str | float = DECIMAL_ZERO
    currency: str = "CNY"
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, name="cash_amount"))
        object.__setattr__(self, "delta", _money(self.delta, name="cash_delta"))
        if self.amount < 0:
            raise CanonicalContractError("cash_negative")
        if self.as_of_date:
            object.__setattr__(self, "as_of_date", _date_string(self.as_of_date, name="as_of_date"))
        if str(self.currency).upper() != "CNY":
            raise CanonicalContractError("unsupported_currency")
        object.__setattr__(self, "currency", "CNY")

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    to_dict = as_dict


@dataclass(frozen=True)
class NAV:
    trade_date: str
    cash: Decimal | str | float
    market_value: Decimal | str | float
    nav: Decimal | str | float | None = None
    kernel_id: str = CANONICAL_KERNEL_ID
    kernel_version: str = CANONICAL_KERNEL_VERSION
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "trade_date", _date_string(self.trade_date, name="trade_date"))
        cash = _money(self.cash, name="nav_cash")
        market_value = _money(self.market_value, name="nav_market_value")
        if cash < 0 or market_value < 0:
            raise CanonicalContractError("nav_component_negative")
        object.__setattr__(self, "cash", cash)
        object.__setattr__(self, "market_value", market_value)
        expected = cash + market_value
        actual = expected if self.nav is None else _money(self.nav, name="nav")
        if actual < 0 or abs(actual - expected) > CENT:
            raise CanonicalContractError("nav_conservation_violation")
        object.__setattr__(self, "nav", actual)

    @property
    def record_hash(self) -> str:
        return _record_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return _canonical_value(_record_dict(self))

    def canonical_json(self) -> str:
        return canonical_json(self)

    def canonical_hash(self) -> str:
        return self.record_hash

    to_dict = as_dict


def deterministic_order_id(
    symbol: Any,
    side: Any,
    shares: Any,
    signal_date: Any,
    execution_date: Any,
    *,
    release_id: str = "",
    run_id: str = "",
    sequence: int = 0,
) -> str:
    """Create a deterministic, collision-resistant order id."""
    payload = {
        "symbol": normalize_symbol(symbol),
        "side": str(side).upper().strip(),
        "shares": int(shares),
        "signal_date": _date_string(signal_date, name="signal_date"),
        "execution_date": _date_string(execution_date, name="execution_date"),
        "release_id": str(release_id),
        "run_id": str(run_id),
        "sequence": int(sequence),
        "kernel_id": CANONICAL_KERNEL_ID,
        "kernel_version": CANONICAL_KERNEL_VERSION,
    }
    return "ord_" + canonical_hash(payload)[:32]


def assert_canonical_kernel(record: Any, *, trusted: bool = True) -> None:
    """Fail closed when a trusted record is missing the canonical marker."""
    payload = _record_dict(record)
    if str(payload.get("kernel_id", "")) != CANONICAL_KERNEL_ID:
        raise CanonicalContractError("trusted_result_kernel_id_mismatch")
    if str(payload.get("kernel_version", "")) != CANONICAL_KERNEL_VERSION:
        raise CanonicalContractError("trusted_result_kernel_version_mismatch")
    if trusted and str(payload.get("schema_version", "")) != CANONICAL_SCHEMA_VERSION:
        raise CanonicalContractError("trusted_result_schema_version_mismatch")


def validate_t_plus_one(
    signal_date: Any,
    execution_date: Any,
    sse_open_dates: Sequence[Any] | None = None,
) -> None:
    """Require execution on the next SSE trading session after signal.

    With no calendar the historical diagnostic behaviour (strictly later
    date) is retained. Trusted adapters always provide ``sse_open_dates`` or
    an explicit expected execution date and therefore take the exact-session
    branch.
    """
    signal = _day(signal_date)
    execution = _day(execution_date)
    if execution <= signal:
        raise CanonicalContractError("same_day_execution_forbidden")
    if sse_open_dates is None:
        return
    days = sorted({_day(item) for item in sse_open_dates})
    next_days = [item for item in days if item > signal]
    if not next_days:
        raise CanonicalContractError("t_plus_one_calendar_missing_next_session")
    if execution != next_days[0]:
        raise CanonicalContractError(
            f"execution_not_next_sse_session:{execution.isoformat()}!={next_days[0].isoformat()}"
        )


# Descriptive aliases make the contract discoverable to adapters that use
# ``Canonical*`` naming while preserving the compact public names above.
CanonicalOrder = Order
CanonicalFill = Fill
CanonicalReject = Reject
CanonicalCorporateAction = CorporateAction
CanonicalPosition = Position
CanonicalCash = Cash
CanonicalNAV = NAV
serialize_canonical = canonical_json
hash_canonical = canonical_hash
canonical_sha256 = canonical_hash
build_order_id = deterministic_order_id


def canonical_record(record: Any) -> dict[str, Any]:
    """Return a JSON-safe canonical mapping for any contract record."""
    return _canonical_value(_record_dict(record))


__all__ = [
    "CANONICAL_KERNEL_ID", "CANONICAL_KERNEL_VERSION", "CANONICAL_SCHEMA_VERSION",
    "KERNEL_ID", "KERNEL_VERSION", "DEFAULT_LOT_SIZE", "CanonicalContractError",
    "Order", "Fill", "Reject", "CorporateAction", "Position", "Cash", "NAV",
    "CanonicalOrder", "CanonicalFill", "CanonicalReject", "CanonicalCorporateAction",
    "CanonicalPosition", "CanonicalCash", "CanonicalNAV",
    "normalize_symbol", "deterministic_order_id", "canonical_json", "canonical_hash",
    "stable_hash", "serialize_canonical", "hash_canonical", "canonical_sha256",
    "build_order_id", "canonical_record", "assert_canonical_kernel", "validate_t_plus_one",
]
