# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 风险档位：`adaptive`；自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。
- Adaptive 版本：`v2.2`；AShare 权重：`prod_stage1`；放权档位：`production_stage1`；补位上限：2 只。
- 目标总仓位：70%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost |   adaptive_switch_count |   adaptive_attack_days |   adaptive_recent_champion_days |   adaptive_balanced_days |   adaptive_robust_days |   adaptive_defensive_days |   adaptive_fallback_days |   dual_attack_days |   dual_neutral_days |   dual_defensive_days |   dual_freeze_days |   adaptive_avg_target_position_ratio |   adaptive_min_target_position_ratio |   adaptive_max_target_position_ratio |   execution_safe_uplift_recovery_days |   execution_safe_uplift_fallback_days |   execution_safe_uplift_incremental_hard_block_days |   execution_safe_uplift_preflight_unknown_days |   execution_safe_uplift_warning_only_days | strategy                                                     | sort_col   | pool     |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                                                                         | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|------------------------:|-----------------------:|--------------------------------:|-------------------------:|-----------------------:|--------------------------:|-------------------------:|-------------------:|--------------------:|----------------------:|-------------------:|-------------------------------------:|-------------------------------------:|-------------------------------------:|--------------------------------------:|--------------------------------------:|----------------------------------------------------:|-----------------------------------------------:|------------------------------------------:|:-------------------------------------------------------------|:-----------|:---------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:-----------------------------------------------------------------------------------------------------------------|:--------------|---------------------:|
| 2026-01-06   | 2026-01-30  |             19 |         500000 |         538435 | 7.69%          | 182.03%             | -10.15%        | 52.63%           | 6.42%      | -4.63%      | 69.17%               |                    5 |            15 |          10 |            5 |                      0 |    1.97636 |      812.586 |                       0 |                      0 |                              19 |                        0 |                      0 |                         0 |                        0 |                  0 |                   0 |                     0 |                  0 |                                  0.7 |                                  0.7 |                                  0.7 |                                     0 |                                     0 |                                                   0 |                                              0 |                                         0 | production_governed_vol_position_v1_2b_execution_safe_uplift | adaptive   | adaptive |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |              0.7 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy                                                     | window   | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:-------------------------------------------------------------|:---------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| production_governed_vol_position_v1_2b_execution_safe_uplift | 3m       | 2026-01-06     | 2026-01-30   |             19 |         500000 |                511829 |              538435 | 5.20%          | -10.15%        | 37.96%                  | 52.63%           | 69.17%               |                    5 |
| production_governed_vol_position_v1_2b_execution_safe_uplift | 6m       | 2026-01-06     | 2026-01-30   |             19 |         500000 |                511829 |              538435 | 5.20%          | -10.15%        | 37.96%                  | 52.63%           | 69.17%               |                    5 |
| production_governed_vol_position_v1_2b_execution_safe_uplift | 1y       | 2026-01-06     | 2026-01-30   |             19 |         500000 |                511829 |              538435 | 5.20%          | -10.15%        | 37.96%                  | 52.63%           | 69.17%               |                    5 |
| production_governed_vol_position_v1_2b_execution_safe_uplift | 3y       | 2026-01-06     | 2026-01-30   |             19 |         500000 |                511829 |              538435 | 5.20%          | -10.15%        | 37.96%                  | 52.63%           | 69.17%               |                    5 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260619_130006_034896_trusted_account_backtest/trusted_account_backtest_report.md`
