from datetime import date

import pandas as pd

from chenyiyunSelected.strategy.daily_signal_runner import (
    _normalize_target_weights,
    build_rebalance_orders,
    format_signal_message,
)


def test_normalize_target_weights():
    signals = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "target_weight": 0.6},
            {"ts_code": "000002.SZ", "target_weight": 0.3},
            {"ts_code": "000002.SZ", "target_weight": 0.1},
        ]
    )
    out = _normalize_target_weights(signals)
    assert out["000001.SZ"] == 0.6
    assert out["000002.SZ"] == 0.4


def test_build_rebalance_orders_buy_and_sell():
    orders = build_rebalance_orders(
        trade_date=date(2026, 2, 20),
        target_weights={"000001.SZ": 0.5, "000002.SZ": 0.5},
        current_positions={"000001.SZ": 2000, "000003.SZ": 1000},
        prices={"000001.SZ": 10.0, "000002.SZ": 20.0, "000003.SZ": 10.0},
        total_equity=100000,
        lot_size=100,
        min_trade_value=100,
    )

    # Should include sell of removed symbol and buy/increase for target symbol(s)
    by_code = {(o.ts_code, o.side): o for o in orders}
    assert ("000003.SZ", "SELL") in by_code
    assert ("000002.SZ", "BUY") in by_code


def test_format_signal_message_empty():
    text = format_signal_message(date(2026, 2, 20), [])
    assert "no rebalance orders" in text.lower()
