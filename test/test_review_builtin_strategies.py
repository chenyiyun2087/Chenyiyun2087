import numpy as np
import pandas as pd

from scripts.research.review_builtin_strategies import benchmark_metrics, nav_metrics, stress_matrix


def _nav(days: int, strategy: str = "s") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "strategy": strategy,
            "trade_date": pd.bdate_range("2025-01-02", periods=days).strftime("%Y-%m-%d"),
            "total_equity": 1_000_000 * np.cumprod(np.repeat(1.001, days)),
        }
    )


def test_window_requires_ninety_percent_coverage_and_suppresses_annualization():
    result = nav_metrics(_nav(60), "3m", 63, min_coverage=0.90)
    assert result["comparable"] is True
    assert result["coverage_status"] == "FULL"

    result = nav_metrics(_nav(50), "3m", 63, min_coverage=0.90)
    assert result["comparable"] is False
    assert result["coverage_status"] == "INSUFFICIENT_SAMPLE"
    assert np.isnan(result["annualized_return"])
    assert np.isnan(result["sharpe"])


def test_benchmark_metrics_include_excess_and_capture_ratios():
    nav = _nav(80)
    benchmark = pd.DataFrame(
        {
            "trade_date": nav.trade_date,
            "close": 4000 * np.cumprod(np.repeat(1.0005, len(nav))),
        }
    )
    result = benchmark_metrics(nav, benchmark)
    row = result[result.window.eq("3m")].iloc[0]
    assert row.benchmark == "CSI300"
    assert row.excess_return > 0
    assert "information_ratio" in result


def test_capacity_fails_closed_without_execution_proxies():
    summary = pd.DataFrame(
        [{"strategy": "s", "evaluation_status": "evaluated", "initial_cash": 500_000, "total_return": 0.2, "turnover": 2.0, "trade_count": 1}]
    )
    candidates = pd.DataFrame([{"strategy": "s", "adjusted_target_weight": 0.2}])
    result = stress_matrix(summary, candidates, capitals=(1_000_000,), cost_rates=(0.00075,), slippage_bps=(10,))
    assert result.iloc[0].capacity_status == "UNVERIFIED"
    assert not bool(result.iloc[0].proxy_available)


def test_capacity_marks_zero_trade_strategy_as_insufficient():
    summary = pd.DataFrame(
        [{"strategy": "s", "evaluation_status": "evaluated", "initial_cash": 500_000, "total_return": 0.0, "turnover": 0.0, "trade_count": 0}]
    )
    candidates = pd.DataFrame(columns=["strategy", "adjusted_target_weight", "estimated_turnover_impact", "unfilled_ratio_proxy"])
    result = stress_matrix(summary, candidates, capitals=(1_000_000,), cost_rates=(0.00075,), slippage_bps=(10,))
    assert result.iloc[0].capacity_status == "INSUFFICIENT_TRADES"
