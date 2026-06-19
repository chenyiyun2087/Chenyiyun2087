"""Daily raw-price execution ledger used by strict-precommit research only.

Signals deliberately live outside this module and can use adjusted prices.  This
ledger is the sole source for strict cash, shares, orders, fills, corporate
actions, and mark-to-market equity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable


LEDGER_SCHEMA_VERSION = "strict_daily_ledger_v2"
STRICT_SIZING_VERSION = "t_raw_close_limit_capped_10pct_v1"

PLANNED = "PLANNED"
REJECTED_T1_NOT_TRADABLE = "REJECTED_T1_NOT_TRADABLE"
REJECTED_LIMIT_BLOCK = "REJECTED_LIMIT_BLOCK"
PARTIAL_FILL = "PARTIAL_FILL"
FILLED = "FILLED"
CANCELLED_T1_CLOSE = "CANCELLED_T1_CLOSE"
# Compatibility alias for callers importing the old public constant.
CANCELLED = CANCELLED_T1_CLOSE
CORPORATE_ACTION_FREEZE = "CORPORATE_ACTION_FREEZE"


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


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: object
    cash_per_share: float = 0.0
    stock_ratio: float = 0.0
    rights_ratio: float = 0.0
    rights_price: float | None = None
    split_ratio: float = 0.0
    source_complete: bool = True
    source_reason: str = ""


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

    def _append(self, event_type: str, **payload: object) -> None:
        self.event_rows.append({"event_type": event_type, "mark_price_basis": "raw", **payload})

    def plan(self, order: PrecommitOrder) -> None:
        self._append("order", order_status=PLANNED, **asdict(order))

    def apply_corporate_actions(self, actions: Iterable[CorporateAction]) -> None:
        for action in actions:
            if not action.source_complete:
                raise RuntimeError(f"incomplete_corporate_action:{action.symbol}:{action.ex_date}:{action.source_reason}")
            held = int(self.shares.get(action.symbol, 0))
            # Unknown rights participation cannot be inferred from a daily bar.
            if action.rights_ratio:
                raise RuntimeError(f"rights_issue_requires_reconciliation:{action.symbol}:{action.ex_date}")
            if held <= 0:
                self._append("corporate_action", order_status="NO_POSITION", symbol=action.symbol, ex_date=action.ex_date,
                             cash_delta=0.0, share_delta=0, source_reason=action.source_reason)
                continue
            cash_delta = held * float(action.cash_per_share)
            stock_delta = int(round(held * float(action.stock_ratio)))
            split_delta = int(round(held * float(action.split_ratio)))
            self.cash += cash_delta
            self.shares[action.symbol] = held + stock_delta + split_delta
            self._append("corporate_action", order_status="APPLIED", symbol=action.symbol, ex_date=action.ex_date,
                         cash_delta=cash_delta, share_delta=stock_delta + split_delta, source_reason=action.source_reason)

    def freeze(self, event_date: object, reason: str) -> None:
        """Record a fail-closed corporate-action halt without mutating balances."""
        self._append("corporate_action", order_status=CORPORATE_ACTION_FREEZE, event_date=event_date,
                     source_reason=reason, cash_delta=0.0, share_delta=0)

    def execute(
        self,
        order: PrecommitOrder,
        fill_price: float | None,
        tradable: bool,
        fee_rate: float,
        reject_reason: str = "",
        lot_size: int = 0,
    ) -> dict:
        planned = max(0, int(order.planned_shares))
        if reject_reason or not tradable or fill_price is None or fill_price <= 0:
            status = REJECTED_LIMIT_BLOCK if reject_reason == "limit_block" else REJECTED_T1_NOT_TRADABLE
            reason = reject_reason or "t1_not_tradable"
            result = {"order_status": status, "filled_shares": 0, "filled_price": None, "filled_notional": 0.0,
                      "fee": 0.0, "reject_reason": reason, "remaining_shares": planned}
            self._append("order", order_id=order.order_id, symbol=order.symbol, side=order.side,
                         signal_date=order.signal_date, execution_date=order.execution_date, planned_shares=planned, **result)
            self.cancel(order, planned, reason)
            return result

        price = float(fill_price)
        if order.side == "BUY":
            affordable = int(self.cash // (price * (1.0 + float(fee_rate))))
            filled = min(planned, affordable)
            if lot_size > 0:
                filled = filled // int(lot_size) * int(lot_size)
            gross = filled * price
            fee = gross * float(fee_rate)
            self.cash -= gross + fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) + filled
        else:
            filled = min(planned, int(self.shares.get(order.symbol, 0)))
            gross = filled * price
            fee = gross * float(fee_rate)
            self.cash += gross - fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) - filled
        remaining = planned - filled
        status = FILLED if remaining == 0 else PARTIAL_FILL
        result = {"order_status": status, "filled_shares": filled, "filled_price": price if filled else None,
                  "filled_notional": gross, "fee": fee, "reject_reason": "" if filled else "insufficient_cash_or_shares",
                  "remaining_shares": remaining}
        self._append("order", order_id=order.order_id, symbol=order.symbol, side=order.side,
                     signal_date=order.signal_date, execution_date=order.execution_date, planned_shares=planned, **result)
        if remaining:
            self.cancel(order, remaining, "unfilled_at_t1_close")
        return result

    def cancel(self, order: PrecommitOrder, shares: int, reason: str) -> None:
        if shares > 0:
            self._append("order", order_id=order.order_id, symbol=order.symbol, side=order.side,
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
