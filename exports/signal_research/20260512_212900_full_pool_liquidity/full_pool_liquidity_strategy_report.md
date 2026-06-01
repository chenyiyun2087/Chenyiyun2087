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
| score                  |       394977 |            74 |       1        |        1        |
| score_liq_breakout_adj |       394977 |            74 |       1        |        1        |
| liquidity_detail_score |       394977 |            74 |       1        |        1        |
| dynamic_factor_score   |       394977 |            74 |       1        |        1        |
| s_liquidity            |       394977 |            74 |       1        |        1        |
| s_breakout             |       394977 |            74 |       1        |        1        |
| s_relative_amount      |       394921 |            74 |       0.999858 |        1        |
| s_amount_ratio_5_20    |       394921 |            74 |       0.999858 |        1        |
| s_low_impact_cost      |       394977 |            74 |       1        |        1        |
| s_amount_stability     |       394910 |            74 |       0.99983  |        1        |
| bs_score_v2            |       394977 |            74 |       1        |        1        |
| bs_consensus_score     |       165752 |            32 |       0.41965  |        0.432432 |
| bs_model_rank_score    |       160548 |            32 |       0.406474 |        0.432432 |

## 未形成有效回测的策略

_全部策略均形成有效回测_

## 全样本排名

| strategy                                        |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| liq20_bs_model_rank_score_b_bonus_0pct          |        29 | 196.49%        | 157.13%             | -25.00%        | 48.28%     | 4.16%        | -0.04%          |       0.37931  | 29.66%                    |
| liq_top_20_then_model_rank                      |        29 | 196.49%        | 157.13%             | -25.00%        | 48.28%     | 4.16%        | -0.04%          |       0.37931  | 29.66%                    |
| liq20_bs_model_rank_score_b_bonus_3pct          |        29 | 169.61%        | 136.75%             | -26.01%        | 55.17%     | 3.77%        | 1.31%           |       1.68966  | 40.69%                    |
| liq20_bs_model_rank_score_b_bonus_5pct          |        29 | 64.25%         | 53.91%              | -15.90%        | 62.07%     | 1.88%        | 0.83%           |       2.93103  | 51.03%                    |
| liq20_bs_model_rank_score_b_bonus_8pct          |        29 | 63.11%         | 52.98%              | -13.95%        | 62.07%     | 1.86%        | 0.89%           |       4.17241  | 55.86%                    |
| tiered_liquidity_then_bs_v2                     |        64 | 55.53%         | 18.99%              | -72.71%        | 54.69%     | 1.13%        | 0.82%           |       1.07812  | 32.73%                    |
| baseline_b_consensus                            |        29 | 53.07%         | 44.76%              | -26.50%        | 58.62%     | 1.68%        | 1.35%           |       4.93103  | 51.90%                    |
| baseline_full_liquidity_detail                  |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 | 41.88%                    |
| liq_top_20_then_liquidity_detail                |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 | 41.88%                    |
| liq_top_30_then_liquidity_detail                |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 | 41.88%                    |
| liq_top_10_then_liquidity_detail                |        64 | 43.70%         | 15.35%              | -75.70%        | 50.00%     | 0.93%        | -0.19%          |       0.234375 | 41.25%                    |
| tiered_liquidity_then_liquidity_detail          |        64 | 37.94%         | 13.50%              | -76.67%        | 50.00%     | 0.87%        | -0.19%          |       0.25     | 41.56%                    |
| baseline_full_score                             |        64 | 23.63%         | 8.71%               | -64.97%        | 48.44%     | 0.85%        | -0.71%          |       0.3125   | 26.88%                    |
| liq_top_10_then_score_industry_penalty_10pt     |        64 | 18.21%         | 6.81%               | -75.46%        | 46.88%     | 0.87%        | -0.65%          |       0.46875  | 20.16%                    |
| liq_top_10_then_score_industry_penalty_5pt      |        64 | 4.10%          | 1.60%               | -78.20%        | 43.75%     | 0.68%        | -0.92%          |       0.46875  | 21.09%                    |
| liq20_bs_score_v2_b_bonus_5pct                  |        64 | -9.19%         | -3.72%              | -78.62%        | 46.88%     | 0.22%        | -0.41%          |       1.96875  | 32.89%                    |
| baseline_b_score_v2                             |        64 | -13.58%        | -5.59%              | -71.47%        | 45.31%     | 0.00%        | -0.43%          |       4.96875  | 35.55%                    |
| liq20_bs_score_v2_b_bonus_3pct                  |        64 | -14.34%        | -5.91%              | -81.18%        | 48.44%     | 0.16%        | -0.39%          |       1.25     | 31.02%                    |
| liq_top_20_then_consensus                       |        29 | -17.72%        | -15.59%             | -38.59%        | 55.17%     | -0.28%       | 0.91%           |       0.344828 | 32.93%                    |
| baseline_full_liquidity_industry_penalty_0p10pt |        64 | -19.45%        | -8.17%              | -81.04%        | 34.38%     | -0.10%       | -2.15%          |       0.078125 | 20.00%                    |

## 测试区间排名

| strategy                                         |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:-------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| baseline_full_liquidity_industry_penalty_0p10pt  |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |       0.153846 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt  |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |       0.153846 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt  |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |       0.153846 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt     |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |       0.153846 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt     |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |       0.153846 | 20.00%                    |
| baseline_full_liquidity_industry_cap2            |        13 | 254.06%        | 1059.77%            | -2.31%         | 92.31%     | 10.41%       | 9.09%           |       0.153846 | 40.00%                    |
| liq_top_10_then_liq_breakout_adj                 |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |       0.615385 | 32.31%                    |
| liq_top_10_then_score                            |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |       0.615385 | 32.31%                    |
| liq_top_10_then_score_industry_cap2              |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |       0.615385 | 32.31%                    |
| baseline_full_liquidity                          |        13 | 224.83%        | 881.35%             | -3.65%         | 84.62%     | 9.73%        | 9.04%           |       0.153846 | 46.15%                    |
| baseline_full_liquidity_detail                   |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |       0.692308 | 50.77%                    |
| liq_top_10_then_liquidity_detail                 |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |       0.692308 | 50.77%                    |
| liq_top_20_then_liquidity_detail                 |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |       0.692308 | 50.77%                    |
| liq_top_30_then_liquidity_detail                 |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |       0.692308 | 50.77%                    |
| tiered_liquidity_then_liquidity_detail           |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |       0.692308 | 50.77%                    |
| liq_top_10_then_score_industry_penalty_10pt      |        13 | 202.52%        | 754.91%             | -0.95%         | 84.62%     | 9.06%        | 9.17%           |       0.461538 | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt       |        13 | 202.52%        | 754.91%             | -0.95%         | 84.62%     | 9.06%        | 9.17%           |       0.461538 | 20.00%                    |
| liq_top_10_then_dynamic_factor                   |        13 | 166.06%        | 566.52%             | -3.84%         | 69.23%     | 8.18%        | 6.50%           |       0.538462 | 46.15%                    |
| tiered_liquidity_then_score_industry_penalty_5pt |        13 | 158.43%        | 529.94%             | -6.72%         | 84.62%     | 7.77%        | 9.17%           |       0.769231 | 20.00%                    |
| baseline_full_dynamic_factor                     |        13 | 157.87%        | 527.32%             | -3.84%         | 76.92%     | 7.94%        | 6.13%           |       0.230769 | 43.08%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                        |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:------------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_0pct          |           0 |        25 |      1.31362   |  0.0376464   |       0.48 |      -0.249959 |
| index_bucket  | index_neutral | liq_top_20_then_model_rank                      |           0 |        25 |      1.31362   |  0.0376464   |       0.48 |      -0.249959 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_3pct          |           0 |        25 |      1.30285   |  0.0368141   |       0.56 |      -0.260143 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_5pct          |           0 |        25 |      0.471439  |  0.0171701   |       0.6  |      -0.159031 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_8pct          |           0 |        25 |      0.425468  |  0.015886    |       0.6  |      -0.139492 |
| index_bucket  | index_neutral | baseline_b_consensus                            |           0 |        25 |      0.255206  |  0.0113392   |       0.52 |      -0.264956 |
| index_bucket  | index_neutral | liq_top_20_then_consensus                       |           0 |        25 |     -0.0418259 |  0.00274846  |       0.64 |      -0.368957 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                     |           0 |        50 |     -0.0757063 |  0.00296615  |       0.5  |      -0.70149  |
| index_bucket  | index_neutral | baseline_full_score                             |           0 |        50 |     -0.130365  |  0.00255328  |       0.46 |      -0.548169 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct                  |           0 |        50 |     -0.176122  | -0.000335545 |       0.44 |      -0.685995 |
| index_bucket  | index_neutral | baseline_b_score_v2                             |           0 |        50 |     -0.251553  | -0.00394205  |       0.44 |      -0.599298 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct                  |           0 |        50 |     -0.294978  | -0.00311768  |       0.46 |      -0.728591 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail                  |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail                |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail                |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail                |           0 |        50 |     -0.392115  | -0.00689848  |       0.42 |      -0.708917 |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail          |           0 |        50 |     -0.416466  | -0.00766109  |       0.42 |      -0.720578 |
| index_bucket  | index_neutral | baseline_full_liquidity                         |           0 |        50 |     -0.431457  | -0.00897346  |       0.4  |      -0.734473 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt     |           0 |        50 |     -0.436594  | -0.00569775  |       0.42 |      -0.759626 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_0pct                  |           0 |        50 |     -0.457476  | -0.00769188  |       0.42 |      -0.783241 |
| index_bucket  | index_neutral | liq_top_20_then_bs_v2                           |           0 |        50 |     -0.457476  | -0.00769188  |       0.42 |      -0.783241 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_5pt      |           0 |        50 |     -0.494653  | -0.00778821  |       0.4  |      -0.784397 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_8pct                  |           0 |        50 |     -0.518603  | -0.0111491   |       0.36 |      -0.770258 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_cap2           |           0 |        50 |     -0.536574  | -0.0131046   |       0.36 |      -0.741174 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p10pt |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p25pt |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p50pt |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_1pt    |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_2pt    |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |
| index_bucket  | index_neutral | liq_top_20_then_dynamic_factor                  |           0 |        50 |     -0.63977   | -0.015946    |       0.32 |      -0.780313 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_212900_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
