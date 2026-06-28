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

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                                    | sort_col               | pool   |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                      | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:--------------------------------------------|:-----------------------|:-------|--------:|------------:|------------------:|----------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:--------------------------------------------------------------|:--------------|---------------------:|
| 2025-09-02   | 2026-06-24  |            193 |         500000 |         870338 | 74.07%         | 106.99%             | -17.60%        | 56.48%           | 9.61%      | -17.60%     | 68.96%               |              4.97927 |           200 |         104 |           96 |                      0 |    26.0788 |      12559.8 | baseline_full_liquidity_detail_vol_position | liquidity_detail_score | full   |       5 |          10 |           0.00075 |               0 |        100 |               500 |                     5 |              0.7 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy                                    | window   | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:--------------------------------------------|:---------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| baseline_full_liquidity_detail_vol_position | 3m       | 2026-03-24     | 2026-06-24   |             62 |         500000 |                670839 |              870338 | 29.74%         | -17.60%        | 49.12%                  | 54.84%           | 68.45%               |              4.96774 |
| baseline_full_liquidity_detail_vol_position | 6m       | 2025-12-24     | 2026-06-24   |            118 |         500000 |                503724 |              870338 | 72.78%         | -17.60%        | 47.17%                  | 57.63%           | 69.25%               |              4.98305 |
| baseline_full_liquidity_detail_vol_position | 1y       | 2025-09-02     | 2026-06-24   |            193 |         500000 |                492830 |              870338 | 76.60%         | -17.60%        | 41.87%                  | 56.48%           | 68.96%               |              4.97927 |
| baseline_full_liquidity_detail_vol_position | 3y       | 2025-09-02     | 2026-06-24   |            193 |         500000 |                492830 |              870338 | 76.60%         | -17.60%        | 41.87%                  | 56.48%           | 68.96%               |              4.97927 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv`
- ledger_events_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_ledger_events.csv`
- ledger_prices_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_ledger_prices.csv`
- execution_snapshot_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_ledger_execution_snapshot.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260625_085301_602839_trusted_account_backtest/trusted_account_backtest_report.md`
