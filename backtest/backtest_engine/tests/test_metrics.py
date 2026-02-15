from backtest_engine.metrics.performance import calc_performance


def test_calc_performance_basic():
    nav = [
        ("2024-01-01", 1.0),
        ("2024-01-02", 1.1),
        ("2024-01-03", 1.05),
    ]
    turnover = [("2024-01-01", 0.1), ("2024-01-02", 0.2), ("2024-01-03", 0.0)]

    result = calc_performance(nav, turnover, initial_cash=1.0)

    assert round(result["total_return"], 4) == 0.05
    assert result["max_drawdown"] < 0
    assert round(result["turnover"], 4) == 0.3
