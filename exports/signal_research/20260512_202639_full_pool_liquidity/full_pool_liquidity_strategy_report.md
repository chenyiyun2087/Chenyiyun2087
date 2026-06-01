# 全量池流动性策略研究报告

## 回测口径

- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。
- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 组合：Top 5 等权，`rebalance_step=10`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。
- 成本：`cost_rate` 作为单次买入到卖出的合计交易成本，从组合收益中扣除。
- 全量池有效门槛：单日评分股票数不少于 5000。

## 字段覆盖

| column                 |   valid_rows |   valid_dates |   row_coverage |   date_coverage |
|:-----------------------|-------------:|--------------:|---------------:|----------------:|
| score                  |       239826 |            44 |      1         |       1         |
| score_liq_breakout_adj |       239826 |            44 |      1         |       1         |
| liquidity_detail_score |       239826 |            44 |      1         |       1         |
| dynamic_factor_score   |       239826 |            44 |      1         |       1         |
| s_liquidity            |       239826 |            44 |      1         |       1         |
| s_breakout             |       239826 |            44 |      1         |       1         |
| s_relative_amount      |       239826 |            44 |      1         |       1         |
| s_amount_ratio_5_20    |       239826 |            44 |      1         |       1         |
| s_low_impact_cost      |       239826 |            44 |      1         |       1         |
| s_amount_stability     |       239826 |            44 |      1         |       1         |
| bs_score_v2            |       239826 |            44 |      1         |       1         |
| bs_consensus_score     |        10601 |             2 |      0.0442029 |       0.0454545 |
| bs_model_rank_score    |         5397 |             2 |      0.0225038 |       0.0454545 |

## 未形成有效回测的策略

`baseline_b_consensus`, `liq_top_20_then_model_rank`, `liq_top_20_then_consensus`, `liq20_bs_model_rank_score_b_bonus_0pct`, `liq20_bs_model_rank_score_b_bonus_3pct`, `liq20_bs_model_rank_score_b_bonus_5pct`, `liq20_bs_model_rank_score_b_bonus_8pct`

## 全样本排名

| strategy                                         |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:-------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| tiered_liquidity_then_score_industry_penalty_5pt |         4 | 23.61%         | 280.10%             | -7.31%         | 75.00%     | 6.10%        | 3.17%           |           1.25 | 21.25%                    |
| liq_top_10_then_liq_breakout_adj                 |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 | 36.25%                    |
| liq_top_10_then_score                            |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 | 36.25%                    |
| liq_top_10_then_score_industry_cap2              |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 | 36.25%                    |
| liq_top_10_then_score_industry_penalty_10pt      |         4 | 19.59%         | 208.66%             | -8.04%         | 50.00%     | 5.15%        | 2.51%           |           0.75 | 21.25%                    |
| liq_top_10_then_score_industry_penalty_5pt       |         4 | 19.59%         | 208.66%             | -8.04%         | 50.00%     | 5.15%        | 2.51%           |           0.75 | 21.25%                    |
| tiered_liquidity_then_dynamic_factor             |         4 | 18.38%         | 189.43%             | -5.60%         | 75.00%     | 4.58%        | 4.34%           |           0.75 | 36.25%                    |
| liq_top_10_then_dynamic_factor                   |         4 | 16.84%         | 166.55%             | -5.60%         | 75.00%     | 4.25%        | 3.67%           |           0.5  | 36.25%                    |
| tiered_liquidity_then_liq_breakout_adj           |         4 | 11.97%         | 103.84%             | -2.04%         | 50.00%     | 3.00%        | 1.08%           |           1.25 | 42.50%                    |
| tiered_liquidity_then_score                      |         4 | 11.97%         | 103.84%             | -2.04%         | 50.00%     | 3.00%        | 1.08%           |           1.25 | 42.50%                    |
| tiered_liquidity_then_score_industry_cap2        |         4 | 11.97%         | 103.84%             | -2.04%         | 50.00%     | 3.00%        | 1.08%           |           1.25 | 42.50%                    |
| liq_top_30_then_dynamic_factor                   |         4 | 11.63%         | 99.97%              | -8.97%         | 50.00%     | 3.12%        | 1.42%           |           0.75 | 31.25%                    |
| baseline_full_liquidity                          |         4 | 7.07%          | 53.77%              | -2.76%         | 50.00%     | 1.97%        | -0.55%          |           0    | 45.00%                    |
| tiered_liquidity_then_bs_v2                      |         4 | 6.13%          | 45.46%              | -8.72%         | 50.00%     | 1.77%        | 1.91%           |           1.25 | 42.50%                    |
| liq_top_20_then_dynamic_factor                   |         4 | 5.29%          | 38.40%              | -14.14%        | 50.00%     | 1.75%        | 0.41%           |           0.5  | 41.25%                    |
| liq_top_30_then_liq_breakout_adj                 |         4 | 2.95%          | 20.10%              | -5.55%         | 25.00%     | 0.87%        | -1.50%          |           0.5  | 30.00%                    |
| liq_top_30_then_score                            |         4 | 2.95%          | 20.10%              | -5.55%         | 25.00%     | 0.87%        | -1.50%          |           0.5  | 30.00%                    |
| baseline_full_dynamic_factor                     |         4 | 2.35%          | 15.75%              | -16.54%        | 50.00%     | 1.12%        | 0.41%           |           0.75 | 31.25%                    |
| baseline_full_liquidity_industry_cap2            |         4 | 0.31%          | 1.94%               | -7.39%         | 25.00%     | 0.36%        | -3.76%          |           0    | 40.00%                    |
| baseline_full_liquidity_industry_penalty_1pt     |         4 | -1.11%         | -6.78%              | -4.10%         | 50.00%     | -0.10%       | -1.26%          |           0    | 20.00%                    |

## 测试区间排名

| strategy                                         |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:-------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| baseline_full_dynamic_factor                     |         1 | 15.23%         | 3460.96%            | 0.00%          | 100.00%    | 15.23%       | 15.23%          |              1 | 60.00%                    |
| liq_top_10_then_dynamic_factor                   |         1 | 15.23%         | 3460.96%            | 0.00%          | 100.00%    | 15.23%       | 15.23%          |              1 | 60.00%                    |
| liq_top_20_then_dynamic_factor                   |         1 | 15.23%         | 3460.96%            | 0.00%          | 100.00%    | 15.23%       | 15.23%          |              1 | 60.00%                    |
| liq_top_30_then_dynamic_factor                   |         1 | 15.23%         | 3460.96%            | 0.00%          | 100.00%    | 15.23%       | 15.23%          |              1 | 60.00%                    |
| tiered_liquidity_then_dynamic_factor             |         1 | 15.23%         | 3460.96%            | 0.00%          | 100.00%    | 15.23%       | 15.23%          |              1 | 60.00%                    |
| baseline_full_liquidity                          |         1 | 13.77%         | 2479.97%            | 0.00%          | 100.00%    | 13.77%       | 13.77%          |              0 | 40.00%                    |
| baseline_full_liquidity_industry_cap2            |         1 | 13.77%         | 2479.97%            | 0.00%          | 100.00%    | 13.77%       | 13.77%          |              0 | 40.00%                    |
| baseline_full_liq_breakout_adj                   |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 | 20.00%                    |
| baseline_full_score                              |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 | 20.00%                    |
| liq_top_30_then_liq_breakout_adj                 |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 | 20.00%                    |
| liq_top_30_then_score                            |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt     |         1 | 8.89%          | 756.11%             | 0.00%          | 100.00%    | 8.89%        | 8.89%           |              0 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt     |         1 | 8.89%          | 756.11%             | 0.00%          | 100.00%    | 8.89%        | 8.89%           |              0 | 20.00%                    |
| liq20_score_b_bonus_3pct                         |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 | 20.00%                    |
| liq20_score_b_bonus_5pct                         |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 | 20.00%                    |
| liq_top_10_then_score_industry_penalty_10pt      |         1 | 5.81%          | 315.23%             | 0.00%          | 100.00%    | 5.81%        | 5.81%           |              1 | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt       |         1 | 5.81%          | 315.23%             | 0.00%          | 100.00%    | 5.81%        | 5.81%           |              1 | 20.00%                    |
| tiered_liquidity_then_score_industry_penalty_5pt |         1 | 5.81%          | 315.23%             | 0.00%          | 100.00%    | 5.81%        | 5.81%           |              1 | 20.00%                    |
| baseline_full_dynamic_factor_industry_cap2       |         1 | 4.03%          | 170.64%             | 0.00%          | 100.00%    | 4.03%        | 4.03%           |              1 | 40.00%                    |
| liq20_score_b_bonus_0pct                         |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 | 20.00%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                         |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:-------------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_penalty_5pt |           0 |         3 |     0.229631   |   0.0795809  |   0.666667 |     -0.073056  |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt      |           0 |         3 |     0.205466   |   0.0713683  |   0.666667 |     -0.073056  |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_5pt       |           0 |         3 |     0.205466   |   0.0713683  |   0.666667 |     -0.073056  |
| index_bucket  | index_neutral | liq_top_10_then_liq_breakout_adj                 |           0 |         3 |     0.202733   |   0.0692051  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | liq_top_10_then_score                            |           0 |         3 |     0.202733   |   0.0692051  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_cap2              |           0 |         3 |     0.202733   |   0.0692051  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | baseline_full_dynamic_factor                     |           0 |         3 |     0.157679   |   0.0535219  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | liq_top_10_then_dynamic_factor                   |           0 |         3 |     0.157679   |   0.0535219  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | liq_top_20_then_dynamic_factor                   |           0 |         3 |     0.157679   |   0.0535219  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | liq_top_30_then_dynamic_factor                   |           0 |         3 |     0.157679   |   0.0535219  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | tiered_liquidity_then_dynamic_factor             |           0 |         3 |     0.157679   |   0.0535219  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | baseline_full_liquidity                          |           0 |         3 |     0.101025   |   0.0354188  |   0.666667 |      0         |
| index_bucket  | index_neutral | tiered_liquidity_then_liq_breakout_adj           |           0 |         3 |     0.0950202  |   0.0325155  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | tiered_liquidity_then_score                      |           0 |         3 |     0.0950202  |   0.0325155  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_cap2        |           0 |         3 |     0.0950202  |   0.0325155  |   0.333333 |     -0.0214045 |
| index_bucket  | index_neutral | liq_top_30_then_liq_breakout_adj                 |           0 |         3 |     0.0676746  |   0.0234942  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | liq_top_30_then_score                            |           0 |         3 |     0.0676746  |   0.0234942  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | baseline_full_dynamic_factor_industry_cap2       |           0 |         3 |     0.0451453  |   0.0161846  |   0.666667 |     -0.0559833 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_cap2            |           0 |         3 |     0.0314823  |   0.0140154  |   0.333333 |     -0.0476125 |
| index_bucket  | index_neutral | liq20_score_b_bonus_5pct                         |           0 |         3 |     0.0219294  |   0.00836654 |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                      |           0 |         3 |     0.0213413  |   0.0106089  |   0.333333 |     -0.0880927 |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj                   |           0 |         3 |     0.00876125 |   0.00547705 |   0.333333 |     -0.0744866 |
| index_bucket  | index_neutral | baseline_full_score                              |           0 |         3 |     0.00876125 |   0.00547705 |   0.333333 |     -0.0744866 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_1pt     |           0 |         3 |    -0.0265118  |  -0.00662049 |   0.333333 |     -0.0409934 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_2pt     |           0 |         3 |    -0.0265118  |  -0.00662049 |   0.333333 |     -0.0409934 |
| index_bucket  | index_neutral | liq20_score_b_bonus_3pct                         |           0 |         3 |    -0.0482795  |  -0.0138427  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | liq20_score_b_bonus_0pct                         |           0 |         3 |    -0.0804118  |  -0.0259491  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | liq_top_20_then_liq_breakout_adj                 |           0 |         3 |    -0.0804118  |  -0.0259491  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | liq_top_20_then_score                            |           0 |         3 |    -0.0804118  |  -0.0259491  |   0.333333 |     -0.0204351 |
| index_bucket  | index_neutral | liq20_score_b_bonus_8pct                         |           0 |         3 |    -0.0805671  |  -0.0271506  |   0.333333 |     -0.0353872 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202639_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
