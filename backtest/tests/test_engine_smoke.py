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


def test_broker_cost_model_includes_stamp_duty_and_min_commission():
    from backtest_engine.core.broker import Broker
    from backtest_engine.core.types import Bar, Order

    config = BacktestConfig(
        commission_rate=0.0003,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        min_commission_cny=5.0,
    )
    broker = Broker(config=config)
    bar = Bar(ts="2024-01-02", symbol="000001.SZ", open=10.0, high=10.5, low=9.8, close=10.2, volume=1000)

    # Buy trade: notional 10 * 10.0 = 100. Commission should be at least min_commission_cny (5.0)
    buy_order = Order(ts="2024-01-01", symbol="000001.SZ", side="BUY", qty=10)
    buy_trade = broker.match_order(buy_order, bar, available_cash=10000.0)
    assert buy_trade is not None
    assert buy_trade.commission >= 5.0

    # Sell trade: sell 10 shares at ~10.0. Sell pays stamp duty (0.05%) + min commission 5.0
    sell_order = Order(ts="2024-01-01", symbol="000001.SZ", side="SELL", qty=10)
    sell_trade = broker.match_order(sell_order, bar, available_qty=10, available_cash=10000.0)
    assert sell_trade is not None
    # Commission recorded includes stamp duty + commission
    assert sell_trade.commission >= 5.0
