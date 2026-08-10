"""Daily raw-price execution ledger used by strict-precommit research only.

Signals deliberately live outside this module and can use adjusted prices.  This
ledger is the sole source for strict cash, shares, orders, fills, corporate
actions, and mark-to-market equity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Iterable

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    CANONICAL_SCHEMA_VERSION,
    Fill as CanonicalFill,
    Order as CanonicalOrder,
    Reject as CanonicalReject,
    canonical_hash,
    deterministic_order_id,
    normalize_symbol,
)
from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel


LEDGER_SCHEMA_VERSION = "strict_daily_ledger_v2"
STRICT_SIZING_VERSION = "t_raw_close_limit_capped_10pct_v1"
CANONICAL_EXECUTION_KERNEL_ID = CANONICAL_KERNEL_ID
CANONICAL_EXECUTION_KERNEL_VERSION = CANONICAL_KERNEL_VERSION

PLANNED = "PLANNED"
REJECTED_T1_NOT_TRADABLE = "REJECTED_T1_NOT_TRADABLE"
REJECTED_LIMIT_BLOCK = "REJECTED_LIMIT_BLOCK"
PARTIAL_FILL = "PARTIAL_FILL"
FILLED = "FILLED"
CANCELLED_T1_CLOSE = "CANCELLED_T1_CLOSE"
# Compatibility alias for callers importing the old public constant.
CANCELLED = CANCELLED_T1_CLOSE
CORPORATE_ACTION_FREEZE = "CORPORATE_ACTION_FREEZE"
ATOMIC_ACTION_TYPES = {
    "dividend_cash", "stock_bonus", "split_merge", "rights_subscription",
    "share_conversion", "delist_cash_settlement", "delist_writeoff",
}


@dataclass(frozen=True)
class PrecommitOrder:
    symbol: str
    side: str
    planned_shares: int
    planned_price: float
    planned_notional: float
    planned_fee: float
    signal_date: object
    execution_date: object
    order_id: str = ""
    cost_rate: float = 0.0
    lot_size: int = 0

    @property
    def canonical_order_id(self) -> str:
        return self.order_id or deterministic_order_id(
            self.symbol, self.side, self.planned_shares, self.signal_date,
            self.execution_date,
        )

    def to_canonical(self) -> CanonicalOrder:
        """Adapt the legacy order shape into the trusted wire contract."""
        return CanonicalOrder(
            order_id=self.canonical_order_id,
            symbol=self.symbol,
            side=self.side,
            shares=int(self.planned_shares),
            planned_price=self.planned_price,
            planned_notional=self.planned_notional,
            signal_date=self.signal_date,
            execution_date=self.execution_date,
            lot_size=int(self.lot_size or 100),
            cost_model_id="legacy_rate" if self.cost_rate else "",
        )


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: object
    action_type: str = "dividend_cash"
    source_event_id: str = ""
    announcement_date: object | None = None
    effective_date: object | None = None
    as_of_timestamp: str = ""
    cash_per_share: float = 0.0
    stock_ratio: float = 0.0
    rights_ratio: float = 0.0
    rights_price: float | None = None
    split_ratio: float = 0.0
    settlement_price: float | None = None
    new_ts_code: str | None = None
    source_complete: bool = True
    source_reason: str = ""
    event_hash: str = ""


class CorporateActionProcessor:
    """Applies point-in-time actions before an execution session opens."""

    @staticmethod
    def apply(ledger: "ExecutionLedger", actions: Iterable[CorporateAction]) -> None:
        ledger.apply_corporate_actions(actions)


@dataclass
class ExecutionLedger:
    cash: float
    shares: dict[str, int] = field(default_factory=dict)
    expected_equity: float | None = None
    event_rows: list[dict] = field(default_factory=list)
    applied_corporate_action_ids: set[str] = field(default_factory=set)
    _order_results: dict[str, dict] = field(default_factory=dict, init=False, repr=False)

    def _append(self, event_type: str, **payload: object) -> None:
        self.event_rows.append({
            "event_type": event_type,
            "mark_price_basis": "raw",
            "canonical_kernel_id": CANONICAL_KERNEL_ID,
            "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            **payload,
        })

    def plan(self, order: PrecommitOrder) -> None:
        order_payload = asdict(order)
        order_payload["order_id"] = order.canonical_order_id
        order_payload["canonical_order_hash"] = canonical_hash(order.to_canonical())
        self._append("order", order_status=PLANNED, event_timestamp=f"{order.signal_date}T15:00:00+08:00", **order_payload)

    def apply_corporate_actions(self, actions: Iterable[CorporateAction]) -> None:
        actions = list(actions)
        incomplete_parents = {action.source_event_id for action in actions if not action.source_complete}
        if incomplete_parents:
            self.freeze(actions[0].ex_date if actions else "", "incomplete_corporate_action_bundle")
            raise RuntimeError("incomplete_corporate_action_bundle")
        identities = [action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}" for action in actions]
        if len(identities) != len(set(identities)) or any(identity in self.applied_corporate_action_ids for identity in identities):
            self.freeze(actions[0].ex_date if actions else "", "duplicate_corporate_action_atomic_event")
            raise RuntimeError("duplicate_corporate_action_atomic_event")
        for action in sorted(actions, key=lambda value: ({"dividend_cash": 20, "split_merge": 30, "stock_bonus": 40, "rights_subscription": 50, "share_conversion": 55, "delist_cash_settlement": 60, "delist_writeoff": 70}.get(value.action_type, 999), value.source_event_id)):
            if action.action_type not in ATOMIC_ACTION_TYPES:
                self.freeze(action.ex_date, "unknown_corporate_action_type")
                raise RuntimeError(f"unknown_corporate_action_type:{action.action_type}")
            if not action.source_complete:
                raise RuntimeError(f"incomplete_corporate_action:{action.symbol}:{action.ex_date}:{action.source_reason}")
            held = int(self.shares.get(action.symbol, 0))
            if held <= 0:
                self._append("corporate_action", order_status="NO_POSITION", symbol=action.symbol, ex_date=action.ex_date, event_timestamp=f"{action.ex_date}T09:25:00+08:00",
                             cash_delta=0.0, share_delta=0, source_reason=action.source_reason)
                self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")
                continue
            # The research policy is deterministic: subscribe in full only
            # when cash covers the full entitlement.  A shortfall is not
            # silently partially subscribed because broker treatment differs.
            if action.action_type == "rights_subscription":
                if action.rights_price is None or action.rights_price < 0:
                    raise RuntimeError(f"rights_issue_requires_reconciliation:{action.symbol}:{action.ex_date}")
                rights_shares = int(round(held * float(action.rights_ratio)))
                required_cash = rights_shares * float(action.rights_price)
                if self.cash + 1e-9 < required_cash:
                    self.freeze(action.ex_date, "rights_cash_insufficient")
                    raise RuntimeError(f"rights_cash_insufficient:{action.symbol}:{action.ex_date}")
                self.cash -= required_cash
                self.shares[action.symbol] = held + rights_shares
                self._append("corporate_action", order_status="APPLIED", symbol=action.symbol, ex_date=action.ex_date,
                             event_timestamp=f"{action.ex_date}T09:25:00+08:00", cash_delta=-required_cash,
                             share_delta=rights_shares, action_type="rights_subscription", source_event_id=action.source_event_id,
                             source_reason=action.source_reason, event_hash=action.event_hash)
                self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")
                continue
            if action.action_type == "delist_cash_settlement":
                if action.settlement_price is None or action.settlement_price < 0:
                    raise RuntimeError(f"delist_settlement_requires_price:{action.symbol}:{action.ex_date}")
                cash_delta = held * float(action.settlement_price)
                self.cash += cash_delta
                self.shares[action.symbol] = 0
                self._append("corporate_action", order_status="APPLIED", symbol=action.symbol, ex_date=action.ex_date,
                             event_timestamp=f"{action.ex_date}T09:25:00+08:00", cash_delta=cash_delta,
                             share_delta=-held, action_type=action.action_type, source_event_id=action.source_event_id,
                             source_reason=action.source_reason, event_hash=action.event_hash)
                self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")
                continue
            if action.action_type == "delist_writeoff":
                self.shares[action.symbol] = 0
                self._append(
                    "corporate_action", order_status="APPLIED",
                    symbol=action.symbol, ex_date=action.ex_date,
                    event_timestamp=f"{action.ex_date}T09:25:00+08:00",
                    cash_delta=0.0, share_delta=-held,
                    action_type=action.action_type,
                    source_event_id=action.source_event_id,
                    source_reason=action.source_reason, event_hash=action.event_hash,
                )
                self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")
                continue
            if action.action_type == "share_conversion":
                ratio = float(action.split_ratio)
                new_symbol = str(action.new_ts_code or "").split(".")[0].zfill(6)
                if ratio <= 0 or not new_symbol.isdigit():
                    raise RuntimeError(
                        f"share_conversion_requires_terms:{action.symbol}:{action.ex_date}"
                    )
                converted = int(round(held * ratio))
                self.shares[action.symbol] = 0
                self.shares[new_symbol] = int(self.shares.get(new_symbol, 0)) + converted
                self._append(
                    "corporate_action", order_status="APPLIED",
                    symbol=action.symbol, new_symbol=new_symbol,
                    ex_date=action.ex_date,
                    event_timestamp=f"{action.ex_date}T09:25:00+08:00",
                    cash_delta=0.0, share_delta=-held,
                    converted_shares=converted, action_type=action.action_type,
                    source_event_id=action.source_event_id,
                    source_reason=action.source_reason, event_hash=action.event_hash,
                )
                self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")
                continue
            if action.action_type == "dividend_cash":
                cash_delta, share_delta = held * float(action.cash_per_share), 0
            elif action.action_type == "stock_bonus":
                cash_delta, share_delta = 0.0, int(round(held * float(action.stock_ratio)))
            elif action.action_type == "split_merge":
                cash_delta, share_delta = 0.0, int(round(held * float(action.split_ratio)))
            else:  # Kept defensive even though the type set is checked above.
                self.freeze(action.ex_date, "unknown_corporate_action_type")
                raise RuntimeError(f"unknown_corporate_action_type:{action.action_type}")
            self.cash += cash_delta
            self.shares[action.symbol] = held + share_delta
            self._append("corporate_action", order_status="APPLIED", symbol=action.symbol, ex_date=action.ex_date, event_timestamp=f"{action.ex_date}T09:25:00+08:00",
                         cash_delta=cash_delta, share_delta=share_delta, action_type=action.action_type,
                         source_event_id=action.source_event_id, source_reason=action.source_reason, event_hash=action.event_hash)
            self.applied_corporate_action_ids.add(action.event_hash or f"{action.symbol}:{action.ex_date}:{action.source_event_id}:{action.action_type}")

    def freeze(self, event_date: object, reason: str) -> None:
        """Record a fail-closed corporate-action halt without mutating balances."""
        self._append("corporate_action", order_status=CORPORATE_ACTION_FREEZE, event_date=event_date, event_timestamp=f"{event_date}T09:25:00+08:00",
                     source_reason=reason, cash_delta=0.0, share_delta=0)

    def execute(
        self,
        order: PrecommitOrder,
        fill_price: float | None,
        tradable: bool,
        fee_rate: float,
        reject_reason: str = "",
        lot_size: int = 0,
        cost_model: ExecutionCostModel | None = None,
    ) -> dict:
        # Replaying the exact order is idempotent.  Returning a copy protects
        # callers from mutating the ledger's stored result.
        key = order.canonical_order_id
        if key in self._order_results:
            return dict(self._order_results[key])
        planned = max(0, int(order.planned_shares))
        if reject_reason or not tradable or fill_price is None or fill_price <= 0:
            status = (
                REJECTED_LIMIT_BLOCK
                if reject_reason in {"limit_block", "limit_up_block", "limit_down_block"}
                else REJECTED_T1_NOT_TRADABLE
            )
            reason = reject_reason or "t1_not_tradable"
            result = {"order_status": status, "filled_shares": 0, "filled_price": None, "filled_notional": 0.0,
                      "fee": 0.0, "total_cost": 0.0, "costs": {}, "reject_reason": reason, "remaining_shares": planned,
                      "canonical_kernel_id": CANONICAL_KERNEL_ID, "canonical_kernel_version": CANONICAL_KERNEL_VERSION}
            self._append("order", order_id=key, symbol=order.symbol, side=order.side, event_timestamp=f"{order.execution_date}T09:30:00+08:00",
                         signal_date=order.signal_date, execution_date=order.execution_date, planned_shares=planned, **result)
            self.cancel(order, planned, reason)
            self._order_results[key] = dict(result)
            return result

        price = float(fill_price)
        # The expanded model is the formal path.  ``fee_rate`` is retained as
        # an explicitly noncanonical compatibility mode.
        breakdown_for_order = None
        if order.side == "BUY":
            if cost_model is not None:
                # Conservative affordability uses the model's proportional
                # rates; the minimum commission is checked after sizing.
                proportional = (
                    cost_model.commission_rate + cost_model.transfer_fee_rate
                    + cost_model.effective_open_auction_rate
                    + cost_model.effective_gap_rate + cost_model.effective_spread_rate
                    + cost_model.effective_impact_rate
                )
                affordable = int(self.cash // (price * (1.0 + proportional)))
            else:
                affordable = int(self.cash // (price * (1.0 + float(fee_rate))))
            filled = min(planned, affordable)
            if lot_size > 0:
                filled = filled // int(lot_size) * int(lot_size)
            gross = filled * price
            if cost_model is not None:
                # Re-check the fixed minimum commission after proportional
                # sizing; shrink one lot until the cash invariant holds.
                while filled > 0:
                    gross = filled * price
                    candidate = CostBreakdown.calculate(gross, "BUY", cost_model)
                    if gross + candidate.total_cost <= self.cash + 1e-9:
                        breakdown_for_order = candidate
                        break
                    filled -= int(lot_size) if lot_size > 0 else 1
                if filled <= 0:
                    gross = 0.0
                    breakdown_for_order = CostBreakdown.calculate(0.0, "BUY", cost_model)
                fee = breakdown_for_order.total_cost
            else:
                fee = gross * float(fee_rate)
            self.cash -= gross + fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) + filled
        else:
            filled = min(planned, int(self.shares.get(order.symbol, 0)))
            gross = filled * price
            if cost_model is not None:
                breakdown_for_order = CostBreakdown.calculate(gross, "SELL", cost_model)
                fee = breakdown_for_order.total_cost
            else:
                fee = gross * float(fee_rate)
            self.cash += gross - fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) - filled
        remaining = planned - filled
        status = FILLED if remaining == 0 else PARTIAL_FILL
        result = {"order_status": status, "filled_shares": filled, "filled_price": price if filled else None,
                  "filled_notional": gross, "fee": fee, "total_cost": fee,
                  "costs": breakdown_for_order.canonical_dict() if breakdown_for_order is not None else {},
                  "reject_reason": "" if filled else "insufficient_cash_or_shares",
                  "remaining_shares": remaining,
                  "canonical_kernel_id": CANONICAL_KERNEL_ID,
                  "canonical_kernel_version": CANONICAL_KERNEL_VERSION}
        self._append("order", order_id=key, symbol=order.symbol, side=order.side, event_timestamp=f"{order.execution_date}T09:30:00+08:00",
                     signal_date=order.signal_date, execution_date=order.execution_date, planned_shares=planned, **result)
        if remaining:
            self.cancel(order, remaining, "unfilled_at_t1_close")
        self._order_results[key] = dict(result)
        return result

    def cancel(self, order: PrecommitOrder, shares: int, reason: str) -> None:
        if shares > 0:
            self._append("order", order_id=order.canonical_order_id, symbol=order.symbol, side=order.side, event_timestamp=f"{order.execution_date}T15:00:00+08:00",
                         signal_date=order.signal_date, execution_date=order.execution_date,
                         order_status=CANCELLED_T1_CLOSE, cancelled_shares=int(shares), cancel_reason=reason,
                         remaining_shares=0)

    def equity(self, raw_prices: dict[str, float]) -> float:
        return float(self.cash) + sum(int(self.shares.get(symbol, 0)) * float(raw_prices.get(symbol, 0.0) or 0.0) for symbol in self.shares)

    def reconciliation_error_bps(self, raw_prices: dict[str, float]) -> float:
        equity = self.equity(raw_prices)
        if self.expected_equity is None or equity <= 0:
            return 0.0
        return abs(equity - self.expected_equity) / equity * 10_000.0
