from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.config import BacktestConfig
from backtest_engine.core.types import Bar, Order, Trade, OrderRejection
from runtime.canonical_execution_kernel import AccountState, execute_order
from scripts.research.execution_costs import ExecutionCostModel


@dataclass
class Broker:
    config: BacktestConfig
    last_rejection: OrderRejection | None = None

    def match_order(
        self,
        order: Order,
        bar: Bar,
        available_qty: int | None = None,
        *,
        trusted: bool = False,
        execution_price: float | None = None,
        available_cash: float | None = None,
    ) -> Trade | None:
        self.last_rejection = None
        if trusted and str(order.ts)[:10] >= str(bar.ts)[:10]:
            raise ValueError("same_day_execution_forbidden")
        if order.qty <= 0:
            self.last_rejection = OrderRejection(bar.ts, order.symbol, order.side, order.qty, "quantity_non_positive")
            return None
        if available_qty is not None and order.side == "SELL":
            order_qty = min(order.qty, max(0, available_qty))
            if order_qty <= 0:
                self.last_rejection = OrderRejection(bar.ts, order.symbol, order.side, order.qty, "insufficient_shares")
                return None
        else:
            order_qty = order.qty

        slip_ratio = self.config.slippage_bps / 10000.0
        base_price = float(execution_price if execution_price is not None else (bar.open if trusted else bar.close))
        fill_price = base_price * (1 + slip_ratio) if order.side == "BUY" else base_price * (1 - slip_ratio)
        state = AccountState(
            float(available_cash if available_cash is not None else 10**18),
            {order.symbol: int(available_qty or 0)} if order.side == "SELL" else {},
        )
        executed = execute_order(
            state, order_id=f"{order.ts}:{order.symbol}:{order.side}", symbol=order.symbol,
            side=order.side, planned_shares=order_qty, price=fill_price, tradable=True,
            lot_size=max(1, int(self.config.lot_size)),
            cost_model=ExecutionCostModel(
                commission_rate=self.config.commission_rate,
                stamp_duty_rate=0.0,
                transfer_fee_rate=0.0,
                min_commission_cny=0.0,
            ),
        ).as_dict()
        if executed["status"] == "REJECTED":
            self.last_rejection = OrderRejection(bar.ts, order.symbol, order.side, order.qty, executed["reject_reason"])
            return None
        order_qty = int(executed["filled_shares"])
        commission = float(executed["costs"]["total_cost"])
        slippage = 0.0

        return Trade(
            ts=bar.ts,
            symbol=order.symbol,
            side=order.side,
            qty=order_qty,
            price=fill_price,
            commission=commission,
            slippage=slippage,
        )
