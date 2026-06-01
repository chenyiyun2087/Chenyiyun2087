# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 5 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   turnover |   total_cost | strategy                                   | sort_col             | pool   |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------:|-------------:|:-------------------------------------------|:---------------------|:-------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         645926 | 29.19%         | 100.15%             | -31.60%        | 52.13%           | 10.71%     | -11.17%     | 96.50%               |              4.97872 |           180 |          91 |           89 |    33.9002 |      13895.2 | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score | full   |       5 |           5 |           0.00075 |               0 |        100 |               500 |                     5 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_222014_trusted_account_backtest/trusted_account_backtest_report.md`
