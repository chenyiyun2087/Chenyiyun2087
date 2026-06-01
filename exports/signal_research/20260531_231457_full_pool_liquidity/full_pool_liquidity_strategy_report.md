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
| score                          |       462058 |            87 |       1        |        1        |
| score_liq_breakout_adj         |       462058 |            87 |       1        |        1        |
| score_liq_breakout_adj_50p_50d |       462058 |            87 |       1        |        1        |
| score_liq_breakout_adj_40p_30d |       462058 |            87 |       1        |        1        |
| liquidity_detail_score         |       462058 |            87 |       1        |        1        |
| dynamic_factor_score           |       462058 |            87 |       1        |        1        |
| dynamic_ic_factor_score        |       462058 |            87 |       1        |        1        |
| s_liquidity                    |       462058 |            87 |       1        |        1        |
| s_breakout                     |       462058 |            87 |       1        |        1        |
| s_rs                           |       462058 |            87 |       1        |        1        |
| s_relative_amount              |       462002 |            87 |       0.999879 |        1        |
| s_amount_ratio_5_20            |       462002 |            87 |       0.999879 |        1        |
| s_low_impact_cost              |       462058 |            87 |       1        |        1        |
| s_amount_stability             |       461991 |            87 |       0.999855 |        1        |
| bs_score_v2                    |       462058 |            87 |       1        |        1        |
| bs_consensus_score             |       232833 |            45 |       0.503904 |        0.517241 |
| bs_model_rank_score            |       227629 |            45 |       0.492642 |        0.517241 |

## 未形成有效回测的策略

_全部策略均形成有效回测_

## 全样本排名

| strategy                                        | pit_status   |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |   avg_gross_exposure | avg_max_industry_weight   |
|:------------------------------------------------|:-------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|---------------------:|:--------------------------|
| liq20_bs_model_rank_score_b_bonus_0pct          | model_risk   |        35 | 190.81%        | 115.67%             | -25.00%        | 51.43%     | 3.42%        | 1.00%           |       0.457143 |             1        | 28.57%                    |
| liq_top_20_then_model_rank                      | model_risk   |        35 | 190.81%        | 115.67%             | -25.00%        | 51.43%     | 3.42%        | 1.00%           |       0.457143 |             1        | 28.57%                    |
| tiered_liquidity_then_bs_v2                     | trusted      |        77 | 168.11%        | 38.09%              | -72.71%        | 57.14%     | 1.68%        | 0.93%           |       0.935065 |             0.994805 | 33.18%                    |
| liq20_bs_model_rank_score_b_bonus_3pct          | model_risk   |        35 | 164.45%        | 101.41%             | -26.01%        | 57.14%     | 3.10%        | 1.18%           |       1.54286  |             1        | 37.71%                    |
| baseline_full_liquidity_industry_penalty_0p10pt | trusted      |        77 | 149.94%        | 34.96%              | -81.04%        | 45.45%     | 1.47%        | -0.63%          |       0.103896 |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt | trusted      |        77 | 149.94%        | 34.96%              | -81.04%        | 45.45%     | 1.47%        | -0.63%          |       0.103896 |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt | trusted      |        77 | 149.94%        | 34.96%              | -81.04%        | 45.45%     | 1.47%        | -0.63%          |       0.103896 |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt    | trusted      |        77 | 149.94%        | 34.96%              | -81.04%        | 45.45%     | 1.47%        | -0.63%          |       0.103896 |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt    | trusted      |        77 | 149.94%        | 34.96%              | -81.04%        | 45.45%     | 1.47%        | -0.63%          |       0.103896 |             1        | 20.00%                    |
| baseline_full_liquidity_detail                  | trusted      |        77 | 142.21%        | 33.58%              | -74.81%        | 50.65%     | 1.50%        | 0.05%           |       0.350649 |             1        | 43.12%                    |
| liq_top_20_then_liquidity_detail                | trusted      |        77 | 142.21%        | 33.58%              | -74.81%        | 50.65%     | 1.50%        | 0.05%           |       0.350649 |             1        | 43.12%                    |
| liq_top_30_then_liquidity_detail                | trusted      |        77 | 142.21%        | 33.58%              | -74.81%        | 50.65%     | 1.50%        | 0.05%           |       0.350649 |             1        | 43.12%                    |
| baseline_full_liquidity_detail_market_gate      | trusted      |        77 | 134.64%        | 32.20%              | -73.81%        | 50.65%     | 1.43%        | 0.05%           |       0.350649 |             0.958442 | 43.12%                    |
| liq_top_10_then_liquidity_detail                | trusted      |        77 | 133.68%        | 32.02%              | -75.70%        | 50.65%     | 1.46%        | 0.05%           |       0.324675 |             1        | 42.60%                    |
| tiered_liquidity_then_bs_v2_market_gate         | trusted      |        77 | 133.03%        | 31.90%              | -73.42%        | 57.14%     | 1.46%        | 1.27%           |       0.87013  |             0.954286 | 33.74%                    |
| tiered_liquidity_then_liquidity_detail          | trusted      |        77 | 124.32%        | 30.27%              | -76.67%        | 50.65%     | 1.41%        | 0.05%           |       0.337662 |             1        | 42.86%                    |
| baseline_full_score_market_gate                 | trusted      |        77 | 113.27%        | 28.13%              | -61.35%        | 51.95%     | 1.43%        | 0.86%           |       0.285714 |             0.950649 | 27.01%                    |
| baseline_full_score_hist_mdd_position           | trusted      |        77 | 104.14%        | 26.31%              | -64.98%        | 51.95%     | 1.39%        | 0.86%           |       0.285714 |             0.992182 | 27.01%                    |
| baseline_full_score                             | trusted      |        77 | 97.39%         | 24.92%              | -64.97%        | 51.95%     | 1.37%        | 0.88%           |       0.285714 |             0.992208 | 27.01%                    |
| baseline_full_liquidity                         | trusted      |        77 | 95.38%         | 24.51%              | -81.17%        | 50.65%     | 1.16%        | 0.32%           |       0.103896 |             1        | 51.69%                    |

## 测试区间排名

| strategy                                               | pit_status   |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |   avg_gross_exposure | avg_max_industry_weight   |
|:-------------------------------------------------------|:-------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|---------------------:|:--------------------------|
| baseline_full_dynamic_ic_factor                        | trusted      |        16 | 292.92%        | 763.03%             | -0.85%         | 93.75%     | 9.10%        | 8.65%           |         0.25   |                    1 | 62.50%                    |
| liq_top_20_then_dynamic_ic_factor                      | trusted      |        16 | 292.92%        | 763.03%             | -0.85%         | 93.75%     | 9.10%        | 8.65%           |         0.25   |                    1 | 62.50%                    |
| tiered_liquidity_then_dynamic_ic_factor                | trusted      |        16 | 292.92%        | 763.03%             | -0.85%         | 93.75%     | 9.10%        | 8.65%           |         0.25   |                    1 | 62.50%                    |
| baseline_full_liquidity_industry_penalty_0p10pt        | trusted      |        16 | 277.63%        | 710.72%             | 0.00%          | 93.75%     | 8.77%        | 8.34%           |         0.3125 |                    1 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt        | trusted      |        16 | 277.63%        | 710.72%             | 0.00%          | 93.75%     | 8.77%        | 8.34%           |         0.3125 |                    1 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt        | trusted      |        16 | 277.63%        | 710.72%             | 0.00%          | 93.75%     | 8.77%        | 8.34%           |         0.3125 |                    1 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt           | trusted      |        16 | 277.63%        | 710.72%             | 0.00%          | 93.75%     | 8.77%        | 8.34%           |         0.3125 |                    1 | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt           | trusted      |        16 | 277.63%        | 710.72%             | 0.00%          | 93.75%     | 8.77%        | 8.34%           |         0.3125 |                    1 | 20.00%                    |
| baseline_full_liquidity                                | trusted      |        16 | 190.08%        | 435.15%             | -0.85%         | 87.50%     | 7.02%        | 7.75%           |         0.375  |                    1 | 52.50%                    |
| baseline_full_dynamic_factor_industry_cap2_market_gate | trusted      |        16 | 181.51%        | 410.46%             | -4.57%         | 56.25%     | 7.12%        | 2.52%           |         0.375  |                    1 | 40.00%                    |
| baseline_full_dynamic_factor_industry_cap2             | trusted      |        16 | 181.51%        | 410.46%             | -4.57%         | 56.25%     | 7.12%        | 2.52%           |         0.375  |                    1 | 40.00%                    |
| baseline_full_liquidity_industry_cap2                  | trusted      |        16 | 174.08%        | 389.38%             | 0.00%          | 93.75%     | 6.59%        | 7.41%           |         0.5    |                    1 | 40.00%                    |
| baseline_full_dynamic_factor                           | trusted      |        16 | 173.76%        | 388.48%             | -3.13%         | 62.50%     | 6.91%        | 2.53%           |         0.375  |                    1 | 61.25%                    |
| liq_top_10_then_dynamic_factor                         | trusted      |        16 | 173.76%        | 388.48%             | -3.13%         | 62.50%     | 6.91%        | 2.53%           |         0.375  |                    1 | 61.25%                    |
| liq_top_20_then_dynamic_factor                         | trusted      |        16 | 173.76%        | 388.48%             | -3.13%         | 62.50%     | 6.91%        | 2.53%           |         0.375  |                    1 | 61.25%                    |
| liq_top_30_then_dynamic_factor                         | trusted      |        16 | 173.76%        | 388.48%             | -3.13%         | 62.50%     | 6.91%        | 2.53%           |         0.375  |                    1 | 61.25%                    |
| tiered_liquidity_then_dynamic_factor                   | trusted      |        16 | 173.76%        | 388.48%             | -3.13%         | 62.50%     | 6.91%        | 2.53%           |         0.375  |                    1 | 61.25%                    |
| liq_top_30_then_bs_v2                                  | trusted      |        16 | 149.40%        | 321.81%             | -8.42%         | 75.00%     | 6.16%        | 7.20%           |         0.0625 |                    1 | 30.00%                    |
| baseline_full_liquidity_detail                         | trusted      |        16 | 144.22%        | 308.10%             | -13.26%        | 62.50%     | 6.09%        | 4.57%           |         0.6875 |                    1 | 50.00%                    |
| baseline_full_liquidity_detail_market_gate             | trusted      |        16 | 144.22%        | 308.10%             | -13.26%        | 62.50%     | 6.09%        | 4.57%           |         0.6875 |                    1 | 50.00%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                    |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:--------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_0pct      |           0 |        26 |      1.33686   |  0.0365847   |   0.5      |      -0.249959 |
| index_bucket  | index_neutral | liq_top_20_then_model_rank                  |           0 |        26 |      1.33686   |  0.0365847   |   0.5      |      -0.249959 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_3pct      |           0 |        26 |      1.32598   |  0.0357844   |   0.576923 |      -0.260143 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_5pct      |           0 |        26 |      0.486217  |  0.016896    |   0.615385 |      -0.159031 |
| index_bucket  | index_neutral | liq20_bs_model_rank_score_b_bonus_8pct      |           0 |        26 |      0.439784  |  0.0156613   |   0.615385 |      -0.139492 |
| index_bucket  | index_neutral | baseline_full_score_expected_mdd_position   |           0 |        51 |      0.10171   |  0.00271398  |   0.411765 |      -0.229516 |
| index_bucket  | index_neutral | baseline_b_consensus                        |           0 |        26 |      0.0925858 |  0.00592016  |   0.5      |      -0.360186 |
| index_bucket  | index_neutral | baseline_full_score_market_gate             |           0 |        51 |     -0.0418256 |  0.00396683  |   0.45098  |      -0.503394 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_vol_position |           0 |        51 |     -0.0754458 | -0.00144054  |   0.411765 |      -0.194165 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                 |           0 |        51 |     -0.0957361 |  0.00248308  |   0.490196 |      -0.70149  |
| index_bucket  | index_neutral | liq_top_20_then_consensus                   |           0 |        26 |     -0.0957687 |  0.000477459 |   0.615385 |      -0.368957 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2_market_gate     |           0 |        51 |     -0.11918   |  0.00168232  |   0.490196 |      -0.709229 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct_market_gate  |           0 |        51 |     -0.123653  |  0.000798088 |   0.431373 |      -0.659187 |
| index_bucket  | index_neutral | baseline_full_score_hist_mdd_position       |           0 |        51 |     -0.131601  |  0.00221125  |   0.45098  |      -0.548169 |
| index_bucket  | index_neutral | baseline_full_score                         |           0 |        51 |     -0.186285  |  0.00124238  |   0.45098  |      -0.548169 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct_market_gate  |           0 |        51 |     -0.230252  | -0.00162013  |   0.45098  |      -0.679262 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct              |           0 |        51 |     -0.238828  | -0.00182136  |   0.431373 |      -0.685995 |
| index_bucket  | index_neutral | liq30_bs_score_v2_b_bonus_5pct_market_gate  |           0 |        51 |     -0.255475  | -0.00309371  |   0.411765 |      -0.612327 |
| index_bucket  | index_neutral | baseline_b_score_v2                         |           0 |        51 |     -0.304332  | -0.00524744  |   0.431373 |      -0.599298 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_market_gate  |           0 |        51 |     -0.3348    | -0.00526491  |   0.431373 |      -0.694509 |
| index_bucket  | index_neutral | liq30_bs_score_v2_b_bonus_3pct_market_gate  |           0 |        51 |     -0.338284  | -0.00510129  |   0.431373 |      -0.677844 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct              |           0 |        51 |     -0.348639  | -0.00454894  |   0.45098  |      -0.728591 |
| index_bucket  | index_neutral | liq_top_30_then_bs_v2                       |           0 |        51 |     -0.34882   | -0.00447634  |   0.470588 |      -0.758167 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail              |           0 |        51 |     -0.36042   | -0.0057255   |   0.431373 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail            |           0 |        51 |     -0.36042   | -0.0057255   |   0.431373 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail            |           0 |        51 |     -0.36042   | -0.0057255   |   0.431373 |      -0.706275 |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail            |           0 |        51 |     -0.366173  | -0.00592643  |   0.431373 |      -0.708917 |
| index_bucket  | index_neutral | baseline_full_liquidity                     |           0 |        51 |     -0.379812  | -0.00701638  |   0.411765 |      -0.734473 |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail      |           0 |        51 |     -0.391563  | -0.00667408  |   0.431373 |      -0.720578 |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt |           0 |        51 |     -0.423804  | -0.00514092  |   0.431373 |      -0.759626 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260531_231457_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
