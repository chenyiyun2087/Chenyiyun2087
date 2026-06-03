# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 15 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         462566 | -7.49%         | -2.36%              | -74.27%        | 49.33%           | 37.61%     | -31.96%     | 98.18%               |              4.99757 |           547 |         276 |          271 |                      0 |    107.173 |      31624.5 | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |          15 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         216655 | -56.67%        | -22.62%             | -76.65%        | 48.60%           | 81.01%     | -40.76%     | 98.11%               |              5.00851 |           546 |         275 |          271 |                      0 |    107.674 |      28776.2 | baseline_full_score                        | score                  | full             |       5 |          15 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         163732 | -67.25%        | -28.98%             | -83.42%        | 47.87%           | 15.56%     | -13.55%     | 98.03%               |              4.98906 |           549 |         276 |          273 |                      0 |    107.651 |      20135.9 | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          15 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |          68856 | -86.23%        | -45.55%             | -90.89%        | 47.14%           | 68.99%     | -39.63%     | 95.99%               |              4.90036 |           542 |         274 |          268 |                      0 |    107.328 |      17308.8 | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |          15 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_212756_857678_trusted_account_backtest/trusted_account_backtest_report.md`
