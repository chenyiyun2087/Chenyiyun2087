# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.1000%，单边滑点 0.1000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool   |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         935239 | 87.05%         | 445.64%             | -20.81%        | 63.83%           | 10.23%     | -5.97%      | 98.34%               |             14.0532  |           261 |         137 |          124 |    18.6594 |      13891.4 | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full   |       5 |          10 |             0.001 |           0.001 |        100 |               500 |
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         770276 | 54.06%         | 222.51%             | -17.67%        | 56.38%           | 10.45%     | -6.71%      | 98.20%               |              9.15957 |           178 |          94 |           84 |    19.0065 |      12167.3 | baseline_full_liquidity_detail             | liquidity_detail_score | full   |       5 |          10 |             0.001 |           0.001 |        100 |               500 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220259_trusted_account_backtest/trusted_account_backtest_report.md`
