# 全量池流动性策略研究报告

## 回测口径

- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。
- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 组合：Top 5 等权，`rebalance_step=1`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。
- 成本：`cost_rate` 作为单次买入到卖出的合计交易成本，从组合收益中扣除。
- 全量池有效门槛：单日评分股票数不少于 5000。

## 字段覆盖

| column                         |   valid_rows |   valid_dates |   row_coverage |   date_coverage |
|:-------------------------------|-------------:|--------------:|---------------:|----------------:|
| score                          |       394977 |            74 |       1        |        1        |
| score_liq_breakout_adj         |       394977 |            74 |       1        |        1        |
| score_liq_breakout_adj_50p_50d |       394977 |            74 |       1        |        1        |
| score_liq_breakout_adj_40p_30d |       394977 |            74 |       1        |        1        |
| liquidity_detail_score         |       394977 |            74 |       1        |        1        |
| dynamic_factor_score           |       394977 |            74 |       1        |        1        |
| dynamic_ic_factor_score        |       394977 |            74 |       1        |        1        |
| s_liquidity                    |       394977 |            74 |       1        |        1        |
| s_breakout                     |       394977 |            74 |       1        |        1        |
| s_rs                           |       394977 |            74 |       1        |        1        |
| s_relative_amount              |       394921 |            74 |       0.999858 |        1        |
| s_amount_ratio_5_20            |       394921 |            74 |       0.999858 |        1        |
| s_low_impact_cost              |       394977 |            74 |       1        |        1        |
| s_amount_stability             |       394910 |            74 |       0.99983  |        1        |
| bs_score_v2                    |       394977 |            74 |       1        |        1        |
| bs_consensus_score             |       165752 |            32 |       0.41965  |        0.432432 |
| bs_model_rank_score            |       160548 |            32 |       0.406474 |        0.432432 |

## 未形成有效回测的策略

_全部策略均形成有效回测_

## 全样本排名

| strategy                                         | pit_status   |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |   avg_gross_exposure | avg_max_industry_weight   |
|:-------------------------------------------------|:-------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|---------------------:|:--------------------------|
| tiered_liquidity_then_bs_v2                      | trusted      |        64 | 55.53%         | 18.99%              | -72.71%        | 54.69%     | 1.13%        | 0.82%           |       1.07812  |             0.99375  | 32.73%                    |
| baseline_full_liquidity_detail                   | trusted      |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 |             1        | 41.88%                    |
| liq_top_20_then_liquidity_detail                 | trusted      |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 |             1        | 41.88%                    |
| liq_top_30_then_liquidity_detail                 | trusted      |        64 | 48.94%         | 16.98%              | -74.81%        | 50.00%     | 0.98%        | -0.19%          |       0.265625 |             1        | 41.88%                    |
| baseline_full_liquidity_detail_market_gate       | trusted      |        64 | 44.29%         | 15.53%              | -73.81%        | 50.00%     | 0.90%        | -0.19%          |       0.265625 |             0.95     | 41.88%                    |
| liq_top_10_then_liquidity_detail                 | trusted      |        64 | 43.70%         | 15.35%              | -75.70%        | 50.00%     | 0.93%        | -0.19%          |       0.234375 |             1        | 41.25%                    |
| tiered_liquidity_then_liquidity_detail           | trusted      |        64 | 37.94%         | 13.50%              | -76.67%        | 50.00%     | 0.87%        | -0.19%          |       0.25     |             1        | 41.56%                    |
| tiered_liquidity_then_bs_v2_market_gate          | trusted      |        64 | 35.18%         | 12.60%              | -73.42%        | 54.69%     | 0.87%        | 0.82%           |       1        |             0.945    | 33.41%                    |
| baseline_full_score_market_gate                  | trusted      |        64 | 33.58%         | 12.07%              | -61.35%        | 48.44%     | 0.92%        | -0.52%          |       0.3125   |             0.940625 | 26.88%                    |
| baseline_full_score_hist_mdd_position            | trusted      |        64 | 27.86%         | 10.16%              | -64.98%        | 48.44%     | 0.87%        | -0.71%          |       0.3125   |             0.990594 | 26.88%                    |
| baseline_full_score                              | trusted      |        64 | 23.63%         | 8.71%               | -64.97%        | 48.44%     | 0.85%        | -0.71%          |       0.3125   |             0.990625 | 26.88%                    |
| liq_top_10_then_score_industry_penalty_10pt      | trusted      |        64 | 18.21%         | 6.81%               | -75.46%        | 46.88%     | 0.87%        | -0.65%          |       0.46875  |             0.99375  | 20.16%                    |
| baseline_full_liquidity_detail_vol_position      | trusted      |        64 | 9.95%          | 3.81%               | -21.95%        | 48.44%     | 0.16%        | -0.02%          |       0.265625 |             0.216316 | 41.88%                    |
| baseline_full_liquidity_detail_hist_mdd_position | trusted      |        64 | 5.63%          | 2.18%               | -75.77%        | 51.56%     | 0.37%        | 0.16%           |       0.265625 |             0.814193 | 41.88%                    |
| liq_top_10_then_score_industry_penalty_5pt       | trusted      |        64 | 4.10%          | 1.60%               | -78.20%        | 43.75%     | 0.68%        | -0.92%          |       0.46875  |             0.99375  | 21.09%                    |
| liq20_bs_score_v2_b_bonus_5pct_market_gate       | trusted      |        64 | -3.12%         | -1.24%              | -76.80%        | 46.88%     | 0.29%        | -0.41%          |       1.96875  |             0.946875 | 33.07%                    |
| liq20_bs_score_v2_b_bonus_3pct_market_gate       | trusted      |        64 | -7.52%         | -3.03%              | -77.76%        | 48.44%     | 0.24%        | -0.33%          |       1.25     |             0.941875 | 31.20%                    |
| liq20_bs_score_v2_b_bonus_5pct                   | trusted      |        64 | -9.19%         | -3.72%              | -78.62%        | 46.88%     | 0.22%        | -0.41%          |       1.96875  |             0.996875 | 32.89%                    |
| liq30_bs_score_v2_b_bonus_5pct_market_gate       | trusted      |        64 | -9.87%         | -4.01%              | -71.55%        | 43.75%     | 0.12%        | -1.31%          |       2.14062  |             0.946875 | 30.39%                    |
| baseline_b_score_v2                              | trusted      |        64 | -13.58%        | -5.59%              | -71.47%        | 45.31%     | 0.00%        | -0.43%          |       4.96875  |             0.99375  | 35.55%                    |

## 测试区间排名

| strategy                                         | pit_status   |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |   avg_gross_exposure | avg_max_industry_weight   |
|:-------------------------------------------------|:-------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|---------------------:|:--------------------------|
| baseline_full_liquidity_industry_penalty_0p10pt  | trusted      |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |      0.153846  |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt  | trusted      |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |      0.153846  |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt  | trusted      |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |      0.153846  |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt     | trusted      |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |      0.153846  |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt     | trusted      |        13 | 275.94%        | 1202.73%            | -2.01%         | 92.31%     | 10.92%       | 11.78%          |      0.153846  |             1        | 20.00%                    |
| baseline_full_liquidity_industry_cap2            | trusted      |        13 | 254.06%        | 1059.77%            | -2.31%         | 92.31%     | 10.41%       | 9.09%           |      0.153846  |             1        | 40.00%                    |
| liq_top_10_then_liq_breakout_adj                 | trusted      |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |      0.615385  |             1        | 32.31%                    |
| liq_top_10_then_score                            | trusted      |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |      0.615385  |             1        | 32.31%                    |
| liq_top_10_then_score_industry_cap2              | trusted      |        13 | 240.35%        | 974.28%             | -0.95%         | 76.92%     | 10.23%       | 10.91%          |      0.615385  |             1        | 32.31%                    |
| baseline_full_liquidity                          | trusted      |        13 | 224.83%        | 881.35%             | -3.65%         | 84.62%     | 9.73%        | 9.04%           |      0.153846  |             1        | 46.15%                    |
| baseline_full_liquidity_detail                   | trusted      |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |      0.692308  |             1        | 50.77%                    |
| liq_top_10_then_liquidity_detail                 | trusted      |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |      0.692308  |             1        | 50.77%                    |
| liq_top_20_then_liquidity_detail                 | trusted      |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |      0.692308  |             1        | 50.77%                    |
| liq_top_30_then_liquidity_detail                 | trusted      |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |      0.692308  |             1        | 50.77%                    |
| tiered_liquidity_then_liquidity_detail           | trusted      |        13 | 222.15%        | 865.71%             | -1.65%         | 84.62%     | 9.66%        | 10.95%          |      0.692308  |             1        | 50.77%                    |
| liq_top_10_then_score_industry_penalty_10pt      | trusted      |        13 | 202.52%        | 754.91%             | -0.95%         | 84.62%     | 9.06%        | 9.17%           |      0.461538  |             1        | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt       | trusted      |        13 | 202.52%        | 754.91%             | -0.95%         | 84.62%     | 9.06%        | 9.17%           |      0.461538  |             1        | 20.00%                    |
| baseline_full_liquidity_detail_market_gate       | trusted      |        13 | 200.07%        | 741.56%             | -1.65%         | 84.62%     | 9.07%        | 7.66%           |      0.692308  |             0.938462 | 50.77%                    |
| tiered_liquidity_then_score_industry_penalty_5pt | trusted      |        13 | 158.43%        | 529.94%             | -6.72%         | 84.62%     | 7.77%        | 9.17%           |      0.769231  |             1        | 20.00%                    |
| baseline_full_dynamic_factor_industry_cap2       | trusted      |        13 | 155.76%        | 517.41%             | -3.75%         | 76.92%     | 7.86%        | 6.13%           |      0.0769231 |             1        | 30.77%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                         |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:-------------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | baseline_full_score_market_gate                  |           0 |        50 |      0.0240211 |  0.00533221  |       0.46 |      -0.503394 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct_market_gate       |           0 |        50 |     -0.0514575 |  0.00233629  |       0.44 |      -0.659187 |
| index_bucket  | index_neutral | baseline_full_score_hist_mdd_position            |           0 |        50 |     -0.0719235 |  0.00354152  |       0.46 |      -0.548169 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                      |           0 |        50 |     -0.0757063 |  0.00296615  |       0.5  |      -0.70149  |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_vol_position      |           0 |        50 |     -0.0857757 | -0.00169533  |       0.4  |      -0.194165 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2_market_gate          |           0 |        50 |     -0.0996693 |  0.00214938  |       0.5  |      -0.709229 |
| index_bucket  | index_neutral | baseline_full_score                              |           0 |        50 |     -0.130365  |  0.00255328  |       0.46 |      -0.548169 |
| index_bucket  | index_neutral | liq30_bs_score_v2_b_bonus_5pct_market_gate       |           0 |        50 |     -0.159634  | -0.000874638 |       0.42 |      -0.612327 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct_market_gate       |           0 |        50 |     -0.166839  | -0.000130297 |       0.46 |      -0.679262 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct                   |           0 |        50 |     -0.176122  | -0.000335545 |       0.44 |      -0.685995 |
| index_bucket  | index_neutral | liq30_bs_score_v2_b_bonus_3pct_market_gate       |           0 |        50 |     -0.249155  | -0.00282923  |       0.44 |      -0.677844 |
| index_bucket  | index_neutral | baseline_b_score_v2                              |           0 |        50 |     -0.251553  | -0.00394205  |       0.44 |      -0.599298 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct                   |           0 |        50 |     -0.294978  | -0.00311768  |       0.46 |      -0.728591 |
| index_bucket  | index_neutral | liq_top_30_then_bs_v2                            |           0 |        50 |     -0.312071  | -0.00349747  |       0.48 |      -0.758167 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_market_gate       |           0 |        50 |     -0.362026  | -0.00622373  |       0.42 |      -0.694509 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail                   |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail                 |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail                 |           0 |        50 |     -0.386598  | -0.00669353  |       0.42 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail                 |           0 |        50 |     -0.392115  | -0.00689848  |       0.42 |      -0.708917 |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail           |           0 |        50 |     -0.416466  | -0.00766109  |       0.42 |      -0.720578 |
| index_bucket  | index_neutral | baseline_full_liquidity                          |           0 |        50 |     -0.431457  | -0.00897346  |       0.4  |      -0.734473 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt      |           0 |        50 |     -0.436594  | -0.00569775  |       0.42 |      -0.759626 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_0pct                   |           0 |        50 |     -0.457476  | -0.00769188  |       0.42 |      -0.783241 |
| index_bucket  | index_neutral | liq_top_20_then_bs_v2                            |           0 |        50 |     -0.457476  | -0.00769188  |       0.42 |      -0.783241 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_hist_mdd_position |           0 |        50 |     -0.462728  | -0.00984714  |       0.44 |      -0.718268 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_5pt       |           0 |        50 |     -0.494653  | -0.00778821  |       0.4  |      -0.784397 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_8pct                   |           0 |        50 |     -0.518603  | -0.0111491   |       0.36 |      -0.770258 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_cap2            |           0 |        50 |     -0.536574  | -0.0131046   |       0.36 |      -0.741174 |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj_50p_50d           |           0 |        50 |     -0.555397  | -0.0116159   |       0.42 |      -0.748827 |
| index_bucket  | index_neutral | baseline_full_liquidity_industry_penalty_0p10pt  |           0 |        50 |     -0.588732  | -0.0158482   |       0.26 |      -0.769635 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_031026_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
