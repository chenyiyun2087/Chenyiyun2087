# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 5，未满 10 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：5。
- 目标总仓位：70%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.0750%，单边滑点 0.0000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                   | sort_col               | pool             |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-------------------------------------------|:-----------------------|:-----------------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|---------------------:|
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         413652 | -17.27%        | -5.65%              | -54.17%        | 50.30%           | 9.12%      | -8.69%      | 68.65%               |              4.97327 |           837 |         416 |          421 |                      0 |    112.063 |      31138.9 | baseline_full_liquidity_detail_market_gate | liquidity_detail_score | full             |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |              0.7 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         344962 | -31.01%        | -10.76%             | -87.64%        | 46.17%           | 13.92%     | -35.00%     | 67.95%               |              4.87728 |           804 |         404 |          400 |                      0 |    111.335 |      17090.7 | tiered_liquidity_then_bs_v2_market_gate    | bs_score_v2            | liquidity_tiered |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |              0.7 |                    0 |
| 2023-01-04   | 2026-06-02  |            823 |         500000 |         204346 | -59.13%        | -23.99%             | -75.21%        | 46.90%           | 29.46%     | -20.87%     | 67.82%               |              4.98542 |           821 |         411 |          410 |                      0 |    112.248 |      21352.6 | tiered_liquidity_then_bs_v2                | bs_score_v2            | liquidity_tiered |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |              0.7 |                    0 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260603_231338_292185_trusted_account_backtest/trusted_account_backtest_report.md`
