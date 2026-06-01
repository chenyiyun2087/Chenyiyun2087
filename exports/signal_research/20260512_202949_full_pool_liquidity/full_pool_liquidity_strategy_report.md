# 全量池流动性策略研究报告

## 回测口径

- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。
- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 组合：Top 5 等权，`rebalance_step=1`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。
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

| strategy                                        |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| baseline_full_liquidity                         |        34 | 154.54%        | 99.86%              | -26.75%        | 52.94%     | 3.05%        | 0.89%           |      0.0294118 | 52.35%                    |
| baseline_full_liquidity_industry_penalty_0p10pt |        34 | 103.63%        | 69.39%              | -39.71%        | 41.18%     | 2.39%        | -1.29%          |      0.0588235 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt |        34 | 103.63%        | 69.39%              | -39.71%        | 41.18%     | 2.39%        | -1.29%          |      0.0588235 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt |        34 | 103.63%        | 69.39%              | -39.71%        | 41.18%     | 2.39%        | -1.29%          |      0.0588235 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt    |        34 | 103.63%        | 69.39%              | -39.71%        | 41.18%     | 2.39%        | -1.29%          |      0.0588235 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt    |        34 | 103.63%        | 69.39%              | -39.71%        | 41.18%     | 2.39%        | -1.29%          |      0.0588235 | 20.00%                    |
| baseline_full_liquidity_industry_cap2           |        34 | 100.58%        | 67.52%              | -35.08%        | 50.00%     | 2.35%        | 0.23%           |      0.0588235 | 40.00%                    |
| baseline_full_liquidity_detail                  |        34 | 61.20%         | 42.46%              | -48.53%        | 52.94%     | 1.75%        | 1.51%           |      0.352941  | 37.65%                    |
| liq_top_20_then_liquidity_detail                |        34 | 61.20%         | 42.46%              | -48.53%        | 52.94%     | 1.75%        | 1.51%           |      0.352941  | 37.65%                    |
| liq_top_30_then_liquidity_detail                |        34 | 61.20%         | 42.46%              | -48.53%        | 52.94%     | 1.75%        | 1.51%           |      0.352941  | 37.65%                    |
| liq_top_10_then_liquidity_detail                |        34 | 59.75%         | 41.51%              | -48.99%        | 52.94%     | 1.72%        | 1.51%           |      0.323529  | 37.06%                    |
| tiered_liquidity_then_liquidity_detail          |        34 | 53.35%         | 37.29%              | -51.03%        | 52.94%     | 1.60%        | 1.51%           |      0.352941  | 37.65%                    |
| baseline_full_score                             |        34 | 30.03%         | 21.49%              | -59.02%        | 44.12%     | 1.14%        | -1.09%          |      0.352941  | 25.88%                    |
| liq_top_10_then_score_industry_penalty_10pt     |        34 | 17.39%         | 12.62%              | -63.14%        | 44.12%     | 0.86%        | -0.84%          |      0.647059  | 20.15%                    |
| liq_top_10_then_score_industry_penalty_5pt      |        34 | 3.38%          | 2.49%               | -67.26%        | 38.24%     | 0.50%        | -0.96%          |      0.647059  | 21.91%                    |
| liq_top_10_then_score_industry_cap2             |        34 | 3.26%          | 2.41%               | -70.88%        | 38.24%     | 0.64%        | -0.96%          |      0.735294  | 33.68%                    |
| liq_top_10_then_liq_breakout_adj                |        34 | -7.31%         | -5.47%              | -73.86%        | 38.24%     | 0.35%        | -1.50%          |      0.735294  | 34.85%                    |
| liq_top_10_then_score                           |        34 | -7.31%         | -5.47%              | -73.86%        | 38.24%     | 0.35%        | -1.50%          |      0.735294  | 34.85%                    |
| tiered_liquidity_then_bs_v2                     |        34 | -9.29%         | -6.97%              | -66.95%        | 47.06%     | 0.09%        | -0.07%          |      1.20588   | 32.65%                    |
| baseline_full_liq_breakout_adj                  |        34 | -13.96%        | -10.54%             | -64.85%        | 44.12%     | -0.11%       | -1.09%          |      0.382353  | 27.65%                    |

## 测试区间排名

| strategy                                        |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| liq_top_10_then_liq_breakout_adj                |         7 | 120.81%        | 1631.74%            | -0.10%         | 71.43%     | 12.38%       | 15.47%          |       0.571429 | 37.14%                    |
| liq_top_10_then_score                           |         7 | 120.81%        | 1631.74%            | -0.10%         | 71.43%     | 12.38%       | 15.47%          |       0.571429 | 37.14%                    |
| liq_top_10_then_score_industry_cap2             |         7 | 120.81%        | 1631.74%            | -0.10%         | 71.43%     | 12.38%       | 15.47%          |       0.571429 | 37.14%                    |
| tiered_liquidity_then_liq_breakout_adj          |         7 | 100.30%        | 1119.16%            | -0.10%         | 71.43%     | 10.93%       | 14.83%          |       0.714286 | 37.14%                    |
| tiered_liquidity_then_score                     |         7 | 100.30%        | 1119.16%            | -0.10%         | 71.43%     | 10.93%       | 14.83%          |       0.714286 | 37.14%                    |
| tiered_liquidity_then_score_industry_cap2       |         7 | 100.30%        | 1119.16%            | -0.10%         | 71.43%     | 10.93%       | 14.83%          |       0.714286 | 37.14%                    |
| baseline_full_liquidity_industry_cap2           |         7 | 99.72%         | 1106.58%            | -2.31%         | 85.71%     | 10.72%       | 13.77%          |       0.142857 | 40.00%                    |
| baseline_full_liquidity_industry_penalty_0p10pt |         7 | 94.10%         | 988.62%             | -2.01%         | 85.71%     | 10.24%       | 9.69%           |       0.142857 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt |         7 | 94.10%         | 988.62%             | -2.01%         | 85.71%     | 10.24%       | 9.69%           |       0.142857 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt |         7 | 94.10%         | 988.62%             | -2.01%         | 85.71%     | 10.24%       | 9.69%           |       0.142857 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt    |         7 | 94.10%         | 988.62%             | -2.01%         | 85.71%     | 10.24%       | 9.69%           |       0.142857 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt    |         7 | 94.10%         | 988.62%             | -2.01%         | 85.71%     | 10.24%       | 9.69%           |       0.142857 | 20.00%                    |
| tiered_liquidity_then_bs_v2                     |         7 | 89.08%         | 890.61%             | -0.10%         | 71.43%     | 9.82%        | 9.73%           |       0.857143 | 31.43%                    |
| baseline_full_liquidity                         |         7 | 87.84%         | 867.50%             | -3.65%         | 71.43%     | 9.83%        | 13.77%          |       0.142857 | 48.57%                    |
| liq_top_10_then_score_industry_penalty_10pt     |         7 | 79.89%         | 727.94%             | 0.00%          | 85.71%     | 8.90%        | 9.17%           |       0.428571 | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt      |         7 | 79.89%         | 727.94%             | 0.00%          | 85.71%     | 8.90%        | 9.17%           |       0.428571 | 20.00%                    |
| baseline_full_liquidity_detail                  |         7 | 64.52%         | 500.29%             | -0.43%         | 71.43%     | 7.56%        | 7.13%           |       0.285714 | 51.43%                    |
| liq_top_10_then_liquidity_detail                |         7 | 64.52%         | 500.29%             | -0.43%         | 71.43%     | 7.56%        | 7.13%           |       0.285714 | 51.43%                    |
| liq_top_20_then_liquidity_detail                |         7 | 64.52%         | 500.29%             | -0.43%         | 71.43%     | 7.56%        | 7.13%           |       0.285714 | 51.43%                    |
| liq_top_30_then_liquidity_detail                |         7 | 64.52%         | 500.29%             | -0.43%         | 71.43%     | 7.56%        | 7.13%           |       0.285714 | 51.43%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                         |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:-------------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | baseline_full_liquidity                          |           0 |        25 |      0.581784  |   0.0211382  |       0.52 |      -0.267523 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_cap2            |           0 |        25 |      0.284955  |   0.0126769  |       0.44 |      -0.273657 |
| index_bucket  | index_neutral | baseline_full_score                              |           0 |        25 |      0.245657  |   0.0125996  |       0.4  |      -0.449907 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p10pt  |           0 |        25 |     -0.0134099 |   0.00172668 |       0.24 |      -0.386079 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p25pt  |           0 |        25 |     -0.0134099 |   0.00172668 |       0.24 |      -0.386079 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p50pt  |           0 |        25 |     -0.0134099 |   0.00172668 |       0.24 |      -0.386079 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_1pt     |           0 |        25 |     -0.0134099 |   0.00172668 |       0.24 |      -0.386079 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_2pt     |           0 |        25 |     -0.0134099 |   0.00172668 |       0.24 |      -0.386079 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt      |           0 |        25 |     -0.135964  |  -0.00128903 |       0.36 |      -0.61735  |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj                   |           0 |        25 |     -0.155565  |  -0.00413779 |       0.4  |      -0.508701 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail                   |           0 |        25 |     -0.201877  |  -0.00572694 |       0.36 |      -0.485271 |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail                 |           0 |        25 |     -0.201877  |  -0.00572694 |       0.36 |      -0.485271 |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail                 |           0 |        25 |     -0.201877  |  -0.00572694 |       0.36 |      -0.485271 |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail                 |           0 |        25 |     -0.209055  |  -0.00613684 |       0.36 |      -0.4899   |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_5pt       |           0 |        25 |     -0.225004  |  -0.00546996 |       0.32 |      -0.656782 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                      |           0 |        25 |     -0.227593  |  -0.00598876 |       0.4  |      -0.638538 |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail           |           0 |        25 |     -0.24074   |  -0.00766205 |       0.36 |      -0.510335 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct                   |           0 |        25 |     -0.243743  |  -0.00714039 |       0.4  |      -0.621596 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_cap2              |           0 |        25 |     -0.244121  |  -0.00486307 |       0.28 |      -0.699933 |
| index_bucket  | index_neutral | liq_top_30_then_liq_breakout_adj                 |           0 |        25 |     -0.252636  |  -0.00911724 |       0.4  |      -0.575792 |
| index_bucket  | index_neutral | liq_top_30_then_score                            |           0 |        25 |     -0.252636  |  -0.00911724 |       0.4  |      -0.575792 |
| index_bucket  | index_neutral | liq_top_10_then_liq_breakout_adj                 |           0 |        25 |     -0.291697  |  -0.00720315 |       0.28 |      -0.71882  |
| index_bucket  | index_neutral | liq_top_10_then_score                            |           0 |        25 |     -0.291697  |  -0.00720315 |       0.28 |      -0.71882  |
| index_bucket  | index_neutral | baseline_full_dynamic_factor                     |           0 |        25 |     -0.299739  |  -0.00976948 |       0.36 |      -0.576279 |
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_penalty_5pt |           0 |        25 |     -0.31958   |  -0.0107399  |       0.32 |      -0.668998 |
| index_bucket  | index_neutral | baseline_full_dynamic_factor_industry_cap2       |           0 |        25 |     -0.346167  |  -0.0128914  |       0.36 |      -0.571884 |
| index_bucket  | index_neutral | baseline_b_score_v2                              |           0 |        25 |     -0.351223  |  -0.0148659  |       0.44 |      -0.57231  |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct                   |           0 |        25 |     -0.395325  |  -0.0157603  |       0.36 |      -0.659187 |
| index_bucket  | index_neutral | liq20_score_b_bonus_5pct                         |           0 |        25 |     -0.422671  |  -0.0191438  |       0.36 |      -0.61079  |
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_cap2        |           0 |        25 |     -0.424998  |  -0.0170641  |       0.28 |      -0.691511 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_202949_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
