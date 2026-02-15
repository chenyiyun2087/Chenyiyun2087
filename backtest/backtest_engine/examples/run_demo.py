from __future__ import annotations

from pathlib import Path

from backtest_engine.config import BacktestConfig
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.datafeed.mock_feed import MockFeed
from backtest_engine.examples.demo_strategy import DemoStrategy
from backtest_engine.metrics.performance import calc_performance
from backtest_engine.reporting.exporter import build_report, export_report_json


def main() -> None:
    start, end, freq = "2024-01-01", "2024-02-01", "1d"
    universe = ["000001.SZ", "000002.SZ", "600000.SH"]

    config = BacktestConfig(initial_cash=1_000_000.0, commission_rate=0.0003, slippage_bps=5)
    engine = BacktestEngine(feed=MockFeed(), strategy=DemoStrategy(qty=100), config=config)

    result = engine.run(start=start, end=end, universe=universe, freq=freq)
    metrics = calc_performance(result.nav_series, result.daily_turnover, initial_cash=config.initial_cash)

    report = build_report(
        strategy_id="demo",
        start=start,
        end=end,
        freq=freq,
        universe_size=len(universe),
        result=result,
        metrics=metrics,
    )

    out = Path(__file__).resolve().parents[2] / "results" / "demo_result.json"
    export_report_json(report, str(out))
    print(f"demo report exported to: {out}")


if __name__ == "__main__":
    main()
