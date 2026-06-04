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
| 2023-01-05   | 2026-06-02  |            822 |         500000 |         280836 | -43.83%        | -16.23%             | -52.88%        | 45.86%           | 7.94%      | -8.13%      | 50.69%               |              4.18005 |           796 |         419 |          377 |                      0 |    69.4232 |      21160.3 |                      40 |                     95 |                       37 |                     10 |                       662 |                       18 |                             0.572749 |                                  0.5 |                                    1 | adaptive_market_style | adaptive   | adaptive |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 | adaptive       | 自适应档：按T日市场风格在进攻/均衡/防守策略间切换，并动态调整50%-100%仓位。 | False         |                    0 |

## 窗口收益风险

| strategy              | window   | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:----------------------|:---------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| adaptive_market_style | 3m       | 2026-03-02     | 2026-06-02   |             63 |         500000 |                276784 |              280836 | 1.46%          | -8.29%         | 20.90%                  | 41.27%           | 42.27%               |              2.80952 |
| adaptive_market_style | 6m       | 2025-12-02     | 2026-06-02   |            119 |         500000 |                354286 |              280836 | -20.73%        | -42.21%        | 36.00%                  | 47.06%           | 53.22%               |              3.38655 |
| adaptive_market_style | 1y       | 2025-06-03     | 2026-06-02   |            243 |         500000 |                333485 |              280836 | -15.79%        | -42.21%        | 33.93%                  | 48.97%           | 57.54%               |              3.87243 |
| adaptive_market_style | 3y       | 2023-06-02     | 2026-06-02   |            725 |         500000 |                471316 |              280836 | -40.41%        | -52.29%        | 26.78%                  | 45.10%           | 49.77%               |              4.19034 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_110057_642626_trusted_account_backtest/trusted_account_backtest_report.md`
