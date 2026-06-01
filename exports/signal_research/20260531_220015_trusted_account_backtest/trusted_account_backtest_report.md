# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         966468 | 93.29%         | 496.43%             | -20.60%        | 63.83%           | 10.26%     | -6.00%      | 98.08%               |              14.4574 |           268 |         141 |          127 |    18.6118 |     10514.5  | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         914742 | 82.95%         | 413.84%             | -20.87%        | 59.57%           | 22.94%     | -16.67%     | 98.53%               |              18.3617 |           338 |         179 |          159 |    18.8456 |     10032    | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          10 |           0.00075 |               0 |        100 |               500 |
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         911142 | 82.23%         | 408.38%             | -30.89%        | 58.51%           | 47.90%     | -24.84%     | 99.20%               |              19.3511 |           361 |         191 |          170 |    19.2117 |     11363.8  | baseline_full_score                        | score                  | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |
| 2026-01-06   | 2026-05-29  |             94 |         500000 |         820853 | 64.17%         | 283.16%             | -17.25%        | 57.45%           | 10.44%     | -6.70%      | 98.25%               |               9.1383 |           177 |          92 |           85 |    18.8497 |      9209.93 | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_220015_trusted_account_backtest/trusted_account_backtest_report.md`
