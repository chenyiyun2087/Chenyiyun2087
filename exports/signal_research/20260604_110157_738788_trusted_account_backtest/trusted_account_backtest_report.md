# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 风险档位：`adaptive`；自适应档：按T日市场风格在进攻/均衡/防守策略间切换，并动态调整50%-100%仓位。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost |   adaptive_switch_count |   adaptive_attack_days |   adaptive_balanced_days |   adaptive_robust_days |   adaptive_defensive_days |   adaptive_fallback_days |   adaptive_avg_target_position_ratio |   adaptive_min_target_position_ratio |   adaptive_max_target_position_ratio | strategy              | sort_col   | pool     |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                                    | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|------------------------:|-----------------------:|-------------------------:|-----------------------:|--------------------------:|-------------------------:|-------------------------------------:|-------------------------------------:|-------------------------------------:|:----------------------|:-----------|:---------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:----------------------------------------------------------------------------|:--------------|---------------------:|
| 2026-03-03   | 2026-06-02  |             62 |         500000 |         627937 | 25.59%         | 156.31%             | -5.88%         | 59.68%           | 4.19%      | -2.93%      | 59.13%               |              4.80645 |            54 |          30 |           24 |                      0 |    6.87526 |      2720.61 |                       8 |                      8 |                       25 |                      0 |                        10 |                       19 |                              0.63871 |                                  0.5 |                                    1 | adaptive_market_style | adaptive   | adaptive |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 | adaptive       | 自适应档：按T日市场风格在进攻/均衡/防守策略间切换，并动态调整50%-100%仓位。 | False         |                    0 |

## 窗口收益风险

| strategy              | window   | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:----------------------|:---------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| adaptive_market_style | 3m       | 2026-03-03     | 2026-06-02   |             62 |         500000 |                492349 |              627937 | 27.54%         | -5.88%         | 25.90%                  | 59.68%           | 59.13%               |              4.80645 |
| adaptive_market_style | 6m       | 2026-03-03     | 2026-06-02   |             62 |         500000 |                492349 |              627937 | 27.54%         | -5.88%         | 25.90%                  | 59.68%           | 59.13%               |              4.80645 |
| adaptive_market_style | 1y       | 2026-03-03     | 2026-06-02   |             62 |         500000 |                492349 |              627937 | 27.54%         | -5.88%         | 25.90%                  | 59.68%           | 59.13%               |              4.80645 |
| adaptive_market_style | 3y       | 2026-03-03     | 2026-06-02   |             62 |         500000 |                492349 |              627937 | 27.54%         | -5.88%         | 25.90%                  | 59.68%           | 59.13%               |              4.80645 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110157_738788_trusted_account_backtest/trusted_account_backtest_report.md`
