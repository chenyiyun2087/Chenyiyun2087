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
| 2025-01-03   | 2026-07-31  |            381 |         500000 |         472570 | -5.49%         | -3.67%              | -34.90%        | 49.87%           | 9.25%      | -11.61%     | 88.40%               |              9.55906 |           133 |          71 |           62 |                      0 |    11.4524 |      4922.96 | vls_mom_contrarian_v1_frozen | dynamic_factor_score | full   |      10 |          20 |           0.00075 |           0.001 |                      0.1 |                             0 |        100 |               500 |                    10 |                1 | adaptive       | 自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。 | False         |                    0 |

## 窗口收益风险

| strategy                     | window   | requested_window   |   requested_window_days | actual_start   | actual_end   |   actual_trading_days |   coverage_ratio | coverage_status       | window_start   | window_end   |   trading_days |   initial_cash |   window_start_equity |   window_end_equity | total_return   | max_drawdown   | annualized_volatility   | daily_win_rate   | avg_gross_exposure   |   avg_position_count |
|:-----------------------------|:---------|:-------------------|------------------------:|:---------------|:-------------|----------------------:|-----------------:|:----------------------|:---------------|:-------------|---------------:|---------------:|----------------------:|--------------------:|:---------------|:---------------|:------------------------|:-----------------|:---------------------|---------------------:|
| vls_mom_contrarian_v1_frozen | 3m       | 3m                 |                      63 | 2026-04-30     | 2026-07-31   |                    63 |         1        | PASS                  | 2026-04-30     | 2026-07-31   |             63 |         500000 |                619889 |              472570 | -23.77%        | -32.48%        | 31.05%                  | 41.27%           | 97.95%               |             10.0476  |
| vls_mom_contrarian_v1_frozen | 6m       | 6m                 |                     126 | 2026-02-02     | 2026-07-31   |                   119 |         0.944444 | PASS                  | 2026-02-02     | 2026-07-31   |            119 |         500000 |                640737 |              472570 | -26.25%        | -34.90%        | 37.13%                  | 47.90%           | 95.61%               |             10.0168  |
| vls_mom_contrarian_v1_frozen | 1y       | 1y                 |                     252 | 2025-07-31     | 2026-07-31   |                   243 |         0.964286 | PASS                  | 2025-07-31     | 2026-07-31   |            243 |         500000 |                580082 |              472570 | -18.53%        | -34.90%        | 28.97%                  | 51.03%           | 93.42%               |             10.0082  |
| vls_mom_contrarian_v1_frozen | 3y       | 3y                 |                     756 | 2025-01-03     | 2026-07-31   |                   381 |         0.503968 | INSUFFICIENT_COVERAGE | 2025-01-03     | 2026-07-31   |            381 |         500000 |                500000 |              472570 | -5.49%         | -34.90%        | 27.54%                  | 49.87%           | 88.40%               |              9.55906 |

## 输出文件

- summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_summary.csv`
- nav_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_nav.csv`
- trades_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_trades.csv`
- positions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_positions.csv`
- candidates_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_candidates.csv`
- dynamic_weights_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_dynamic_weights.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_market_environment.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_window_summary.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_adaptive_decisions.csv`
- adaptive_perf_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_adaptive_perf.csv`
- ledger_events_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_ledger_events.csv`
- ledger_prices_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_ledger_prices.csv`
- execution_snapshot_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_ledger_execution_snapshot.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/formal_evidence/vls_oos/factor_diagnostics/alpha_significance/liquidity_null/runs/shuffle_46/trusted_account_backtest_report.md`
