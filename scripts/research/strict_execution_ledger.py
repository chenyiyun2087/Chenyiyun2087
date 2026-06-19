"""Point-in-time daily execution ledger primitives for strict-precommit research.

Signals may use adjusted prices; this module intentionally uses only raw prices
and explicit corporate actions for cash, shares, and NAV accounting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


LEDGER_SCHEMA_VERSION = "strict_daily_ledger_v1"
STRICT_SIZING_VERSION = "t_raw_close_limit_capped_10pct_v1"


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

    def apply_corporate_actions(self, actions: Iterable[CorporateAction]) -> None:
        for action in actions:
            if not action.source_complete:
                raise RuntimeError(f"incomplete_corporate_action:{action.symbol}:{action.ex_date}")
            held = int(self.shares.get(action.symbol, 0))
            if held <= 0:
                continue
            cash_delta = held * float(action.cash_per_share)
            stock_delta = int(round(held * float(action.stock_ratio)))
            split_delta = int(round(held * float(action.split_ratio)))
            if action.rights_ratio:
                # The project policy is fail-closed unless a separate rights
                # subscription cash source has been explicitly reconciled.
                raise RuntimeError(f"rights_issue_requires_reconciliation:{action.symbol}:{action.ex_date}")
            self.cash += cash_delta
            self.shares[action.symbol] = held + stock_delta + split_delta
            self.event_rows.append({"event_type": "corporate_action", "symbol": action.symbol, "cash_delta": cash_delta, "share_delta": stock_delta + split_delta})

    def execute(self, order: PrecommitOrder, fill_price: float | None, tradable: bool, fee_rate: float) -> dict:
        if not tradable or fill_price is None or fill_price <= 0:
            result = {"filled_shares": 0, "filled_price": None, "reject_reason": "not_tradable"}
            self.event_rows.append({"event_type": "reject", "symbol": order.symbol, "side": order.side, **result})
            return result
        planned = int(order.planned_shares)
        if order.side == "BUY":
            affordable = int(self.cash // (float(fill_price) * (1.0 + float(fee_rate))))
            filled = min(planned, affordable)
            gross = filled * float(fill_price)
            fee = gross * float(fee_rate)
            self.cash -= gross + fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) + filled
        else:
            filled = min(planned, int(self.shares.get(order.symbol, 0)))
            gross = filled * float(fill_price)
            fee = gross * float(fee_rate)
            self.cash += gross - fee
            self.shares[order.symbol] = int(self.shares.get(order.symbol, 0)) - filled
        result = {"filled_shares": filled, "filled_price": float(fill_price), "filled_notional": gross, "fee": fee, "reject_reason": "" if filled == planned else "partial_fill"}
        self.event_rows.append({"event_type": "fill", "symbol": order.symbol, "side": order.side, "planned_shares": planned, **result})
        return result

    def reconciliation_error_bps(self, raw_prices: dict[str, float]) -> float:
        equity = self.cash + sum(int(self.shares.get(symbol, 0)) * float(price) for symbol, price in raw_prices.items())
        if self.expected_equity is None or equity <= 0:
            return 0.0
        return abs(equity - self.expected_equity) / equity * 10_000.0
