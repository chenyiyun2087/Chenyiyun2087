from backtest_engine.config import BacktestConfig
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.datafeed.mock_feed import MockFeed
from backtest_engine.examples.demo_strategy import DemoStrategy


def test_engine_smoke():
    engine = BacktestEngine(feed=MockFeed(), strategy=DemoStrategy(qty=10), config=BacktestConfig())

    result = engine.run(
        start="2024-01-01",
        end="2024-01-10",
        universe=["000001.SZ", "000002.SZ"],
        freq="1d",
    )

    assert len(result.nav_series) > 0
    assert len(result.trades) > 0
    assert result.nav_series[-1][1] > 0
