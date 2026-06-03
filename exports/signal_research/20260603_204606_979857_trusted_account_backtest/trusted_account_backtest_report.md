# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 8 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|
| 2023-01-04   | 2026-06-02  |            823 |         500000 |       356524   | -28.70%        | -9.85%              | -71.32%        | 47.39%           | 17.10%     | -14.72%     | 98.09%               |              4.97327 |          1031 |         515 |          516 |                      0 |    199.921 |      46744.4 | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |           8 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |       215555   | -56.89%        | -22.74%             | -91.05%        | 46.66%           | 53.19%     | -29.85%     | 97.40%               |              4.97084 |          1023 |         514 |          509 |                      0 |    199.673 |      34817.8 | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |           8 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |        62150.3 | -87.57%        | -47.23%             | -94.36%        | 48.00%           | 22.52%     | -29.84%     | 94.12%               |              4.82989 |          1008 |         504 |          504 |                      0 |    196.526 |      26098   | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |           8 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |        20849.9 | -95.83%        | -62.24%             | -96.38%        | 44.23%           | 42.67%     | -31.61%     | 97.01%               |              4.96476 |          1018 |         511 |          507 |                      0 |    201.525 |      32862   | baseline_full_score                        | score                  | full             |       5 |           8 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_204606_979857_trusted_account_backtest/trusted_account_backtest_report.md`
