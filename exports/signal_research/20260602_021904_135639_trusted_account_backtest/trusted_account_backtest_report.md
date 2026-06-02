# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost |   adaptive_switch_count |   adaptive_attack_days |   adaptive_balanced_days |   adaptive_defensive_days |   adaptive_fallback_days | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|------------------------:|-----------------------:|-------------------------:|--------------------------:|-------------------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|
| 2025-01-03   | 2026-05-29  |            337 |         500000 |         643486 | 28.70%         | 20.83%              | -44.02%        | 50.74%           | 10.45%     | -10.34%     | 98.14%               |              4.97626 |           341 |         174 |          167 |                      0 |    65.6891 |      22716.6 |                     nan |                    nan |                      nan |                       nan |                      nan | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2025-01-03   | 2026-05-29  |            337 |         500000 |         440050 | -11.99%        | -9.13%              | -54.70%        | 48.07%           | 26.43%     | -19.68%     | 98.30%               |              4.99703 |           336 |         170 |          166 |                      0 |    65.9201 |      21975.1 |                     nan |                    nan |                      nan |                       nan |                      nan | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2025-01-03   | 2026-05-29  |            337 |         500000 |         356061 | -28.79%        | -22.48%             | -62.27%        | 51.04%           | 22.22%     | -14.29%     | 97.48%               |              4.95846 |           337 |         172 |          165 |                      0 |    64.9982 |      14882.4 |                     nan |                    nan |                      nan |                       nan |                      nan | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2025-01-03   | 2026-05-29  |            337 |         500000 |         327685 | -34.46%        | -27.16%             | -56.59%        | 50.15%           | 21.84%     | -17.31%     | 96.70%               |              5.00297 |           336 |         170 |          166 |                      0 |    65.394  |      17312.6 |                      29 |                    167 |                       18 |                       135 |                       17 | adaptive_style_switch                      | adaptive               | adaptive         |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |
| 2025-01-03   | 2026-05-29  |            337 |         500000 |         225733 | -54.85%        | -44.92%             | -88.06%        | 46.88%           | 361.60%    | -75.09%     | 98.58%               |              4.9822  |           336 |         170 |          166 |                      0 |    66.3694 |      13655.5 |                     nan |                    nan |                      nan |                       nan |                      nan | baseline_full_score                        | score                  | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest/trusted_account_backtest_report.md`
