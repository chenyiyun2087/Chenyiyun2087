"""Wave3 canonical economic-kernel regression tests."""

from __future__ import annotations

import pandas as pd
import pytest

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    NAV,
    Order,
    canonical_hash,
)
from runtime.independent_ledger import replay_orders
from runtime.portfolio_risk import evaluate_canonical_risk
from scripts.research.build_capacity_stress_matrix import build_capacity_stress_matrix
from scripts.research.canonical_execution_adapters import adapt_event
from scripts.research.execution_costs import ExecutionCostModel
from scripts.research.golden_execution_fixtures import assert_golden_adapter_parity
from scripts.research.strict_execution_ledger import ExecutionLedger, PrecommitOrder


def _market() -> pd.DataFrame:
    return pd.DataFrame([{
        "trade_date": "2026-01-02", "symbol": "000001", "raw_open": 10.0,
        "raw_close": 10.5, "prev_raw_close": 10.0, "is_tradable": True,
        "is_suspended": False, "is_listed": True, "is_st": False,
        "price_tick": 0.01,
    }])


def test_order_t1_and_hash_are_stable():
    order = Order("o1", "000001.SZ", "BUY", 100, "2026-01-01", "2026-01-02")
    assert order.symbol == "000001" and order.kernel_id == CANONICAL_KERNEL_ID
    assert order.canonical_hash() == canonical_hash(order)
    with pytest.raises(ValueError, match="same_day"):
        Order("o2", "000001", "BUY", 100, "2026-01-01", "2026-01-01")
    with pytest.raises(ValueError, match="next_sse"):
        Order("o3", "000001", "BUY", 100, "2026-01-01", "2026-01-05", sse_open_dates=["2026-01-01", "2026-01-02", "2026-01-05"])
    with pytest.raises(ValueError, match="lot"):
        Order("o4", "000001", "BUY", 150, "2026-01-01", "2026-01-02")
    # Odd-lot sells are allowed by the contract.
    Order("o5", "000001", "SELL", 150, "2026-01-01", "2026-01-02")
    assert NAV("2026-01-02", 100, 100).nav == 200


def test_cost_parity_strict_and_independent_decimal_oracle():
    model = ExecutionCostModel(min_commission_cny=5, open_auction_slippage_bps=10)
    orders = pd.DataFrame([{
        "order_id": "o1", "signal_date": "2026-01-01", "execution_date": "2026-01-02",
        "symbol": "000001", "side": "BUY", "shares": 100, "lot_size": 100,
        "cost_rate": 0.00075,
    }])
    oracle = replay_orders(orders, _market(), initial_capital=500_000, cost_model=model)
    ledger = ExecutionLedger(500_000)
    order = PrecommitOrder("000001", "BUY", 100, 10, 1000, 0, "2026-01-01", "2026-01-02", "o1", lot_size=100)
    result = ledger.execute(order, 10, True, 0, lot_size=100, cost_model=model)
    assert result["fee"] == oracle.trades.iloc[0]["fee"]
    assert ledger.cash == oracle.daily_nav.iloc[0]["cash"]


def test_golden_adapters_and_same_day_sentinel():
    assert_golden_adapter_parity()
    with pytest.raises(ValueError, match="same_day"):
        adapt_event({"event_type": "order", "symbol": "000001", "side": "BUY", "shares": 100, "signal_date": "2026-01-01", "execution_date": "2026-01-01", "trading_dates": ["2026-01-01", "2026-01-02"]})
    with pytest.raises(ValueError, match="raw_price_proof"):
        adapt_event({"event_type": "fill", "fill_id": "f", "order_id": "o", "symbol": "000001", "side": "BUY", "shares": 100, "price": 10, "execution_date": "2026-01-02"})
    with pytest.raises(ValueError, match="canonical_kernel_identity_required"):
        adapt_event({"event_type": "fill", "fill_id": "f", "order_id": "o", "symbol": "000001", "side": "BUY", "shares": 100, "price": 10, "raw_price_basis": "raw_open", "execution_date": "2026-01-02"})
    adapt_event({"event_type": "fill", "fill_id": "f", "order_id": "o", "symbol": "000001", "side": "BUY", "shares": 100, "price": 10, "raw_price_basis": "raw_open", "execution_date": "2026-01-02", "canonical_kernel_id": CANONICAL_KERNEL_ID, "canonical_kernel_version": "1.0.0", "kernel_execution_sha256": "a" * 64})
    with pytest.raises(ValueError, match="corporate_action_proof"):
        adapt_event({"event_type": "corporate_action", "symbol": "000001", "action_type": "dividend_cash", "ex_date": "2026-01-02"})


def test_risk_gates_fail_closed_and_stress_grid_is_4x4():
    decision = evaluate_canonical_risk([
        {"symbol": "000001", "market_value": 50_000, "industry": "I", "theme": "T", "beta": 1.0, "liquidity": 0.01, "risk_contribution": 0.10, "annualized_volatility": 0.10, "max_drawdown": 0.20},
    ], account_nav=500_000)
    assert decision.passed
    blocked = evaluate_canonical_risk([{"symbol": "000001", "market_value": 50_000, "industry": "I", "theme": "T"}], account_nav=500_000)
    assert not blocked.passed
    matrix = build_capacity_stress_matrix([{"planned_notional": 1000, "adv": 100000, "filled_notional": 900}])
    assert len(matrix) == 16 and set(matrix["slippage_bps"]) == {10, 25, 50, 100}
    low = matrix[matrix["capital_cny"] == 50_000].iloc[0]
    high = matrix[matrix["capital_cny"] == 5_000_000].iloc[0]
    assert high.p50_impact_bps >= low.p50_impact_bps
    assert high.cost_erosion_cny >= low.cost_erosion_cny
    blocked = build_capacity_stress_matrix()
    assert set(blocked["status"]) == {"BLOCKED"} and not bool(blocked["formal"].any())
    assert set(build_capacity_stress_matrix([{"planned_notional": 1000, "adv": 0}])["status"]) == {"BLOCKED"}
