import pandas as pd

from scripts.ops.run_trusted_strategy_shadow_monitor import _classify_order, summarize_fills


def test_shadow_monitor_summary_includes_validation_fields():
    fills = pd.DataFrame(
        [
            {
                "side": "BUY",
                "tradable_flag": 0,
                "tradable_status": "BUY_LIMIT_UP_OPEN",
                "planned_amount": 10000.0,
                "execution_amount": 0.0,
                "slippage_bps": None,
                "ts_code": "000001",
            },
            {
                "side": "SELL",
                "tradable_flag": 1,
                "tradable_status": "EXECUTABLE_WITH_WARNING",
                "planned_amount": 10000.0,
                "execution_amount": 9500.0,
                "slippage_bps": 350.0,
                "ts_code": "000002",
            },
        ]
    )

    summary = summarize_fills(fills, "2026-06-17", "2026-06-18")

    assert "unfilled_ratio" in summary
    assert "large_slippage_ratio" in summary
    assert "limit_up_buy_ratio" in summary
    assert "planned_vs_executable_ratio" in summary
    assert "validation_status" in summary
    assert "validation_actions" in summary
    assert summary["validation_status"] == "fail"


def test_shadow_monitor_uses_configured_slippage_threshold_for_classification_and_summary():
    order = {"side": "BUY", "price": 100.0, "delta_shares": 100, "stock_name": "A", "ts_code": "000001"}
    price_350_bps = {"adj_open": 103.5, "amount": 1_000_000.0}
    price_250_bps = {"adj_open": 102.5, "amount": 1_000_000.0}

    assert _classify_order(order, price_350_bps, prev_close=100.0)["tradable_status"] == "EXECUTABLE_WITH_WARNING"
    assert _classify_order(order, price_250_bps, prev_close=100.0)["tradable_status"] == "EXECUTABLE"

    fail_summary = summarize_fills(
        pd.DataFrame(
            [
                {
                    "side": "BUY",
                    "tradable_flag": 1,
                    "tradable_status": "EXECUTABLE_WITH_WARNING",
                    "planned_amount": 10000.0,
                    "execution_amount": 10350.0,
                    "slippage_bps": 350.0,
                    "ts_code": "000001",
                }
            ]
        ),
        "2026-06-17",
        "2026-06-18",
    )
    pass_summary = summarize_fills(
        pd.DataFrame(
            [
                {
                    "side": "BUY",
                    "tradable_flag": 1,
                    "tradable_status": "EXECUTABLE",
                    "planned_amount": 10000.0,
                    "execution_amount": 10250.0,
                    "slippage_bps": 250.0,
                    "ts_code": "000001",
                }
            ]
        ),
        "2026-06-17",
        "2026-06-18",
    )

    assert fail_summary["validation_status"] == "fail"
    assert fail_summary["validation_actions"] == "reduce_position"
    assert pass_summary["validation_status"] == "pass"
