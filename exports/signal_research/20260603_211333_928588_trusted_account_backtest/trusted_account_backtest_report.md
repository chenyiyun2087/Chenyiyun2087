# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 12 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|
| 2023-01-04   | 2026-06-02  |            823 |         500000 |       489717   | -2.06%         | -0.64%              | -65.17%        | 48.72%           | 26.80%     | -22.57%     | 98.08%               |              4.98663 |           690 |         346 |          344 |                      0 |    135.242 |      41999.9 | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |          12 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |       293764   | -41.25%        | -15.04%             | -76.70%        | 48.85%           | 14.42%     | -13.81%     | 98.25%               |              4.99635 |           688 |         345 |          343 |                      0 |    134.937 |      32055.4 | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          12 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |       169603   | -66.08%        | -28.21%             | -77.03%        | 47.14%           | 72.48%     | -37.94%     | 98.31%               |              5.00122 |           685 |         345 |          340 |                      0 |    136.844 |      28799.3 | baseline_full_score                        | score                  | full             |       5 |          12 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |        73817.3 | -85.24%        | -44.37%             | -95.50%        | 48.00%           | 77.72%     | -49.25%     | 95.51%               |              4.90036 |           678 |         341 |          337 |                      0 |    134.149 |      18372.5 | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |          12 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_211333_928588_trusted_account_backtest/trusted_account_backtest_report.md`
