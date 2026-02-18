"""Run local backtest (Phase-A) for chenyiyun strategy via backtest_engine."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

# allow importing backtest_engine package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backtest", "src"))

from backtest_engine.config import BacktestConfig
from backtest_engine.core.engine import BacktestEngine
from backtest_engine.datafeed.tushare_feed import TushareDailyFeed
from backtest_engine.metrics.performance import calc_performance
from backtest_engine.reporting.exporter import build_report, export_report_json
from backtest_engine.strategies.high_dividend_local import HighDividendLocalStrategy, RebalancePlan

from .local_strategy_adapter import (
    DBConfig,
    LocalHighDividendStrategy,
    StrategyConfig,
    TushareWarehouseProvider,
)


def _build_rebalance_plan(local_strategy: LocalHighDividendStrategy, start: str, end: str) -> dict[str, list[str]]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    
    trading_days = local_strategy.provider.get_all_trading_days(start_dt, end_dt)
    if not trading_days:
        return {}

    plan: dict[str, list[str]] = {}
    holding_days = local_strategy.config.holding_days
    
    # Rebalance every 'holding_days' trading days
    for i in range(0, len(trading_days), holding_days):
        rebalance_date = trading_days[i]
        ts = rebalance_date.strftime("%Y-%m-%d")
        try:
            picked = local_strategy.pick(rebalance_date)
            plan[ts] = picked["ts_code"].head(local_strategy.config.stock_num).tolist()
        except Exception as exc:
            print(f"[warn] rebalance pick failed at {ts}: {exc}")
            plan[ts] = []
            
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local backtest for chenyiyun strategy")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="tushare_stock")
    parser.add_argument("--index", default=None, help="filter pool by index code, e.g. 000300.SH")
    parser.add_argument("--stock-num", type=int, default=10)
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--slippage-bps", type=float, default=20.0)
    parser.add_argument("--output", default="backtest/results/chenyiyun_local_result.json")
    args = parser.parse_args()

    db_cfg = DBConfig(args.host, args.port, args.user, args.password, args.database)
    picker = LocalHighDividendStrategy(
        TushareWarehouseProvider(db_cfg),
        StrategyConfig(stock_num=args.stock_num, index_code=args.index, holding_days=args.holding_days)
    )

    weekly_plan = _build_rebalance_plan(picker, args.start, args.end)
    universe = sorted({s for symbols in weekly_plan.values() for s in symbols})
    if not universe:
        raise RuntimeError("回测区间没有可用股票池，请检查数仓数据")

    feed = TushareDailyFeed(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
    )
    config = BacktestConfig(
        initial_cash=args.initial_cash,
        commission_rate=args.commission_rate,
        slippage_bps=args.slippage_bps,
    )
    strategy = HighDividendLocalStrategy(
        rebalance_plan=RebalancePlan(plan=weekly_plan, target_position_count=picker.config.stock_num)
    )
    engine = BacktestEngine(feed=feed, strategy=strategy, config=config)

    result = engine.run(start=args.start, end=args.end, universe=universe, freq="1d")
    metrics = calc_performance(result.nav_series, result.daily_turnover, initial_cash=config.initial_cash)

    report = build_report(
        strategy_id="chenyiyun_local",
        start=args.start,
        end=args.end,
        freq="1d",
        universe_size=len(universe),
        result=result,
        metrics=metrics,
    )
    export_report_json(report, args.output)
    print(f"backtest report exported to: {args.output}")


if __name__ == "__main__":
    main()
