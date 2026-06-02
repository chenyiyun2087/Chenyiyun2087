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

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |   adaptive_switch_count |   adaptive_attack_days |   adaptive_balanced_days |   adaptive_defensive_days |   adaptive_fallback_days |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|------------------------:|-----------------------:|-------------------------:|--------------------------:|-------------------------:|
| 2025-12-02   | 2026-05-29  |            117 |         500000 |         608348 | 21.67%         | 53.13%              | -36.55%        | 50.43%           | 7.30%      | -8.81%      | 97.04%               |              5       |           117 |          60 |           57 |                      0 |    22.1372 |      9476.3  | baseline_full_liquidity_detail             | liquidity_detail_score | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |                     nan |                    nan |                      nan |                       nan |                      nan |
| 2025-12-02   | 2026-05-29  |            117 |         500000 |         520089 | 4.02%          | 8.93%               | -39.51%        | 49.57%           | 11.03%     | -12.89%     | 95.79%               |              4.96581 |           115 |          60 |           55 |                      0 |    22.2015 |      8817.72 | baseline_full_dynamic_factor_industry_cap2 | dynamic_factor_score   | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |                     nan |                    nan |                      nan |                       nan |                      nan |
| 2025-12-02   | 2026-05-29  |            117 |         500000 |         478874 | -4.23%         | -8.95%              | -35.74%        | 50.43%           | 37.76%     | -23.62%     | 98.98%               |              4.99145 |           115 |          60 |           55 |                      0 |    23.0871 |      9194.93 | baseline_full_score                        | score                  | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |                     nan |                    nan |                      nan |                       nan |                      nan |
| 2025-12-02   | 2026-05-29  |            117 |         500000 |         427755 | -14.45%        | -28.75%             | -42.99%        | 50.43%           | 8.08%      | -6.36%      | 97.49%               |              5       |           116 |          60 |           56 |                      0 |    22.5993 |      7852.73 | adaptive_style_switch                      | adaptive               | adaptive         |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |                       7 |                     65 |                        3 |                        29 |                       20 |
| 2025-12-02   | 2026-05-29  |            117 |         500000 |         359190 | -28.16%        | -51.25%             | -46.57%        | 49.57%           | 8.32%      | -6.32%      | 97.43%               |              4.98291 |           116 |          60 |           56 |                      0 |    22.9318 |      8116.69 | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |                1 |                    0 |                     nan |                    nan |                      nan |                       nan |                      nan |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest/trusted_account_backtest_report.md`
