# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 风险档位：`adaptive`；自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。
- Adaptive 版本：`v2.2`；AShare 权重：`prod_stage1`；放权档位：`production_stage1`；补位上限：2 只。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost |   adaptive_switch_count |   adaptive_attack_days |   adaptive_recent_champion_days |   adaptive_balanced_days |   adaptive_robust_days |   adaptive_defensive_days |   adaptive_fallback_days |   dual_attack_days |   dual_neutral_days |   dual_defensive_days |   dual_freeze_days |   adaptive_avg_target_position_ratio |   adaptive_min_target_position_ratio |   adaptive_max_target_position_ratio | strategy              | sort_col   | pool     |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                                                                         | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|------------------------:|-----------------------:|--------------------------------:|-------------------------:|-----------------------:|--------------------------:|-------------------------:|-------------------:|--------------------:|----------------------:|-------------------:|-------------------------------------:|-------------------------------------:|-------------------------------------:|:----------------------|:-----------|:---------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:-----------------------------------------------------------------------------------------------------------------|:--------------|---------------------:|
| 2023-01-05   | 2026-06-04  |            824 |         500000 |         710445 | 42.09%         | 11.36%              | -37.33%        | 47.57%           | 12.13%     | -8.52%      | 54.08%               |              4.72573 |           835 |         419 |          416 |                      0 |    88.5429 |      36335.9 |                     111 |                     48 |                             413 |                        0 |                      0 |                       363 |                        0 |                  0 |                   0 |                     0 |                  0 |                             0.553459 |                                 0.45 |                                  0.8 | adaptive_market_style | adaptive   | adaptive |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy              | window   | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:----------------------|:---------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| adaptive_market_style | 3m       | 2026-03-04     | 2026-06-04   |             63 |         500000 |                612067 |              710445 | 16.07%         | -8.92%         | 27.54%                  | 58.73%           | 59.19%               |              4.7619  |
| adaptive_market_style | 6m       | 2025-12-04     | 2026-06-04   |            119 |         500000 |                472888 |              710445 | 50.24%         | -22.24%        | 39.31%                  | 54.62%           | 60.60%               |              4.81513 |
| adaptive_market_style | 1y       | 2025-06-04     | 2026-06-04   |            244 |         500000 |                466187 |              710445 | 52.39%         | -22.24%        | 35.24%                  | 52.46%           | 61.15%               |              4.84836 |
| adaptive_market_style | 3y       | 2023-06-05     | 2026-06-04   |            726 |         500000 |                651363 |              710445 | 9.07%          | -37.33%        | 27.63%                  | 47.11%           | 53.63%               |              4.74105 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_report.md`
