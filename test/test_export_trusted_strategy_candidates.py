import pandas as pd

from scripts.ops.export_trusted_strategy_candidates import _build_rebalance_orders


def test_rebalance_orders_respect_min_holding_gate_and_reserve_locked_weight():
    candidates = pd.DataFrame(
        [
            {
                "signal_date": "2026-05-12",
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.5,
                "latest_close": 10.0,
                "strategy": "demo",
            },
            {
                "signal_date": "2026-05-12",
                "symbol": "000002",
                "name": "B",
                "effective_weight": 0.5,
                "latest_close": 20.0,
                "strategy": "demo",
            },
        ]
    )
    positions = {
        "000003": {"shares": 2000, "holding_trade_days": 3},
    }

    orders = _build_rebalance_orders(
        candidates=candidates,
        positions=positions,
        latest_price_lookup={"000003": 10.0},
        total_equity=100000.0,
        lot_size=100,
        min_trade_value=100.0,
        include_sells=True,
        min_holding_days=10,
    )

    assert "000003" in orders.attrs["hold_gate_locked_symbols"]
    assert not ((orders["ts_code"] == "000003") & (orders["side"] == "SELL")).any()
    assert round(float(orders["target_weight"].sum()), 6) == 0.8
