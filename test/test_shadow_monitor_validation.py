import pandas as pd

from scripts.ops.run_trusted_strategy_shadow_monitor import summarize_fills


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
