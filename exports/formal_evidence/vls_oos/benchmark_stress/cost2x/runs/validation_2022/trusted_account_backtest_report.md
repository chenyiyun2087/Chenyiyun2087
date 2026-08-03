# 可信策略账户级回测报告

## 口径

- 初始资金：500,000.00
- 信号：T 日收盘后选股，T+1 开盘调仓。
- 组合：Top 10，未满 20 个交易日的持仓不卖、不减仓，并先占用预算。
- 持仓上限：10。
- 风险档位：`adaptive`；自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。
- Adaptive 版本：`v2.2`；AShare 权重：`prod_stage1`；放权档位：`production_stage1`；补位上限：2 只。
- 目标总仓位：100%。
- 硬止损：不启用。
- 撮合：按 100 股整数手，单笔低于 500.00 不交易。
- 成本：单边交易成本 0.1500%，单边滑点 0.2000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                     | sort_col             | pool   |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   rebalance_score_buffer |   rebalance_weight_drift_band |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                      | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-----------------------------|:---------------------|:-------|--------:|------------:|------------------:|----------------:|-------------------------:|------------------------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:--------------------------------------------------------------|:--------------|---------------------:|
| 2022-01-05   | 2022-12-30  |            241 |         500000 |         287960 | -42.41%        | -43.98%             | -43.09%        | 41.08%           | 12.41%     | -11.22%     | 97.55%               |                   10 |            40 |          25 |           15 |                      0 |    4.68241 |      2533.19 | vls_mom_contrarian_v1_frozen | dynamic_factor_score | full   |      10 |          20 |            0.0015 |           0.002 |                      0.1 |                             0 |        100 |               500 |                    10 |                1 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy                     | window   | requested_window   |   requested_window_days | actual_start   | actual_end   |   actual_trading_days |   coverage_ratio | coverage_status       | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:-----------------------------|:---------|:-------------------|------------------------:|:---------------|:-------------|----------------------:|-----------------:|:----------------------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| vls_mom_contrarian_v1_frozen | 3m       | 3m                 |                      63 | 2022-09-30     | 2022-12-30   |                    61 |         0.968254 | PASS                  | 2022-09-30     | 2022-12-30   |             61 |         500000 |                306440 |              287960 | -6.03%         | -9.59%         | 10.26%                  | 40.98%           | 98.46%               |                   10 |
| vls_mom_contrarian_v1_frozen | 6m       | 6m                 |                     126 | 2022-06-30     | 2022-12-30   |                   126 |         1        | PASS                  | 2022-06-30     | 2022-12-30   |            126 |         500000 |                348110 |              287960 | -17.28%        | -17.68%        | 29.29%                  | 42.86%           | 98.23%               |                   10 |
| vls_mom_contrarian_v1_frozen | 1y       | 1y                 |                     252 | 2022-01-05     | 2022-12-30   |                   241 |         0.956349 | PASS                  | 2022-01-05     | 2022-12-30   |            241 |         500000 |                500312 |              287960 | -42.44%        | -43.09%        | 27.20%                  | 41.08%           | 97.55%               |                   10 |
| vls_mom_contrarian_v1_frozen | 3y       | 3y                 |                     756 | 2022-01-05     | 2022-12-30   |                   241 |         0.318783 | INSUFFICIENT_COVERAGE | 2022-01-05     | 2022-12-30   |            241 |         500000 |                500312 |              287960 | -42.44%        | -43.09%        | 27.20%                  | 41.08%           | 97.55%               |                   10 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_adaptive_perf.csv`
- ledger_events_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_ledger_events.csv`
- ledger_prices_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_ledger_prices.csv`
- execution_snapshot_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_ledger_execution_snapshot.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/benchmark_stress/cost2x/runs/validation_2022/trusted_account_backtest_report.md`
