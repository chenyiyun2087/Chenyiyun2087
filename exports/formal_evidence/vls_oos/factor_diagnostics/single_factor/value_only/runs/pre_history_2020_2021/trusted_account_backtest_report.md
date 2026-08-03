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
- 成本：单边交易成本 0.0750%，单边滑点 0.1000%。

## 汇总

| first_date   | last_date   |   trading_days |   initial_cash |   final_equity | total_return   | annualized_return   | max_drawdown   | daily_win_rate   | best_day   | worst_day   | avg_gross_exposure   |   avg_position_count |   trade_count |   buy_count |   sell_count |   stop_loss_sell_count |   turnover |   total_cost | strategy                     | sort_col             | pool   |   top_n |   hold_days |   trade_cost_rate |   slippage_rate |   rebalance_score_buffer |   rebalance_weight_drift_band |   lot_size |   min_trade_value |   max_total_positions |   position_ratio | risk_profile   | risk_profile_description                                      | market_gate   |   hard_stop_loss_pct |
|:-------------|:------------|---------------:|---------------:|---------------:|:---------------|:--------------------|:---------------|:-----------------|:-----------|:------------|:---------------------|---------------------:|--------------:|------------:|-------------:|-----------------------:|-----------:|-------------:|:-----------------------------|:---------------------|:-------|--------:|------------:|------------------:|----------------:|-------------------------:|------------------------------:|-----------:|------------------:|----------------------:|-----------------:|:---------------|:--------------------------------------------------------------|:--------------|---------------------:|
| 2020-05-06   | 2021-12-31  |            407 |         500000 |         507534 | 1.51%          | 0.93%               | -22.55%        | 40.79%           | 17.78%     | -14.80%     | 77.35%               |              8.00983 |            16 |          13 |            3 |                      0 |    1.79667 |      662.655 | vls_mom_contrarian_v1_frozen | dynamic_factor_score | full   |      10 |          20 |           0.00075 |           0.001 |                      0.1 |                             0 |        100 |               500 |                    10 |                1 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy                     | window   | requested_window   |   requested_window_days | actual_start   | actual_end   |   actual_trading_days |   coverage_ratio | coverage_status       | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:-----------------------------|:---------|:-------------------|------------------------:|:---------------|:-------------|----------------------:|-----------------:|:----------------------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| vls_mom_contrarian_v1_frozen | 3m       | 3m                 |                      63 | 2021-09-30     | 2021-12-31   |                    62 |         0.984127 | PASS                  | 2021-09-30     | 2021-12-31   |             62 |         500000 |                463919 |              507534 | 9.40%          | -10.60%        | 38.74%                  | 48.39%           | 96.03%               |             10       |
| vls_mom_contrarian_v1_frozen | 6m       | 6m                 |                     126 | 2021-06-30     | 2021-12-31   |                   126 |         1        | PASS                  | 2021-06-30     | 2021-12-31   |            126 |         500000 |                496665 |              507534 | 2.19%          | -22.55%        | 37.70%                  | 56.35%           | 96.78%               |             10       |
| vls_mom_contrarian_v1_frozen | 1y       | 1y                 |                     252 | 2020-12-31     | 2021-12-31   |                   244 |         0.968254 | PASS                  | 2020-12-31     | 2021-12-31   |            244 |         500000 |                461815 |              507534 | 9.90%          | -22.55%        | 29.44%                  | 53.69%           | 97.39%               |             10       |
| vls_mom_contrarian_v1_frozen | 3y       | 3y                 |                     756 | 2020-05-06     | 2021-12-31   |                   407 |         0.53836  | INSUFFICIENT_COVERAGE | 2020-05-06     | 2021-12-31   |            407 |         500000 |                500000 |              507534 | 1.51%          | -22.55%        | 23.50%                  | 40.79%           | 77.35%               |              8.00983 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_adaptive_perf.csv`
- ledger_events_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_ledger_events.csv`
- ledger_prices_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_ledger_prices.csv`
- execution_snapshot_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_ledger_execution_snapshot.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/single_factor/value_only/runs/pre_history_2020_2021/trusted_account_backtest_report.md`
