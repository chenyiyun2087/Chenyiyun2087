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

| strategy                                    |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:--------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| baseline_full_score                         |         7 | 56.18%         | 397.78%             | -21.91%        | 71.43%     | 7.47%        | 13.80%          |       0.857143 | 31.43%                    |
| baseline_full_liquidity_detail              |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        | 37.14%                    |
| liq_top_10_then_liquidity_detail            |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        | 37.14%                    |
| liq_top_20_then_liquidity_detail            |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        | 37.14%                    |
| liq_top_30_then_liquidity_detail            |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        | 37.14%                    |
| tiered_liquidity_then_liquidity_detail      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        | 37.14%                    |
| tiered_liquidity_then_bs_v2                 |         7 | 51.23%         | 343.29%             | -7.92%         | 71.43%     | 6.48%        | 3.31%           |       0.857143 | 28.57%                    |
| liq_top_20_then_dynamic_factor              |         7 | 42.93%         | 261.78%             | -4.50%         | 57.14%     | 5.71%        | 1.50%           |       0.428571 | 35.00%                    |
| liq_top_10_then_score_industry_penalty_10pt |         7 | 35.67%         | 199.88%             | -8.62%         | 57.14%     | 4.91%        | 0.86%           |       0.571429 | 20.00%                    |
| liq_top_10_then_score_industry_cap2         |         7 | 34.72%         | 192.38%             | -11.17%        | 42.86%     | 4.88%        | -0.98%          |       0.714286 | 28.57%                    |
| liq_top_10_then_score_industry_penalty_5pt  |         7 | 33.20%         | 180.71%             | -9.51%         | 42.86%     | 4.64%        | -0.98%          |       0.571429 | 22.86%                    |
| liq_top_10_then_dynamic_factor              |         7 | 31.71%         | 169.54%             | -4.50%         | 57.14%     | 4.34%        | 1.36%           |       0.428571 | 35.00%                    |
| tiered_liquidity_then_dynamic_factor        |         7 | 31.71%         | 169.54%             | -4.50%         | 57.14%     | 4.34%        | 1.36%           |       0.428571 | 35.00%                    |
| liq20_bs_score_v2_b_bonus_0pct              |         7 | 31.70%         | 169.46%             | -9.82%         | 71.43%     | 4.38%        | 2.75%           |       0.428571 | 31.43%                    |
| liq_top_20_then_bs_v2                       |         7 | 31.70%         | 169.46%             | -9.82%         | 71.43%     | 4.38%        | 2.75%           |       0.428571 | 31.43%                    |
| liq_top_10_then_liq_breakout_adj            |         7 | 29.05%         | 150.45%             | -14.90%        | 42.86%     | 4.29%        | -3.07%          |       0.714286 | 31.43%                    |
| liq_top_10_then_score                       |         7 | 29.05%         | 150.45%             | -14.90%        | 42.86%     | 4.29%        | -3.07%          |       0.714286 | 31.43%                    |
| liq20_bs_score_v2_b_bonus_3pct              |         7 | 28.97%         | 149.92%             | -10.07%        | 71.43%     | 4.01%        | 2.75%           |       1.14286  | 34.29%                    |
| liq20_bs_score_v2_b_bonus_5pct              |         7 | 26.97%         | 136.23%             | -11.69%        | 71.43%     | 3.82%        | 3.11%           |       1.85714  | 31.43%                    |
| baseline_b_consensus                        |         3 | 25.58%         | 577.63%             | -3.91%         | 66.67%     | 8.38%        | 7.76%           |       4.66667  | 71.67%                    |

## 测试区间排名

| strategy                                        |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count | avg_max_industry_weight   |
|:------------------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|:--------------------------|
| baseline_full_liquidity_detail                  |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   | 60.00%                    |
| liq_top_10_then_liquidity_detail                |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   | 60.00%                    |
| liq_top_20_then_liquidity_detail                |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   | 60.00%                    |
| liq_top_30_then_liquidity_detail                |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   | 60.00%                    |
| tiered_liquidity_then_liquidity_detail          |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   | 60.00%                    |
| liq_top_10_then_score_industry_cap2             |         2 | 14.34%         | 441.37%             | 0.00%          | 50.00%     | 7.25%        | 7.25%           |            0.5 | 40.00%                    |
| liq_top_10_then_score_industry_penalty_10pt     |         2 | 13.04%         | 368.73%             | 0.00%          | 100.00%    | 6.47%        | 6.47%           |            0.5 | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt      |         2 | 10.99%         | 271.98%             | 0.00%          | 50.00%     | 5.55%        | 5.55%           |            0.5 | 30.00%                    |
| liq_top_10_then_liq_breakout_adj                |         2 | 9.53%          | 214.93%             | 0.00%          | 50.00%     | 5.16%        | 5.16%           |            0.5 | 50.00%                    |
| liq_top_10_then_score                           |         2 | 9.53%          | 214.93%             | 0.00%          | 50.00%     | 5.16%        | 5.16%           |            0.5 | 50.00%                    |
| tiered_liquidity_then_bs_v2                     |         2 | 8.28%          | 172.54%             | 0.00%          | 50.00%     | 4.83%        | 4.83%           |            1   | 40.00%                    |
| baseline_full_liquidity_industry_penalty_0p10pt |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt    |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   | 20.00%                    |
| baseline_full_liquidity_industry_penalty_2pt    |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   | 20.00%                    |
| tiered_liquidity_then_score_industry_cap2       |         2 | 4.08%          | 65.46%              | 0.00%          | 50.00%     | 2.80%        | 2.80%           |            1   | 40.00%                    |
| baseline_full_liquidity_industry_cap2           |         2 | 2.45%          | 35.64%              | 0.00%          | 50.00%     | 1.23%        | 1.23%           |            0   | 40.00%                    |
| tiered_liquidity_then_liq_breakout_adj          |         2 | 1.55%          | 21.41%              | 0.00%          | 50.00%     | 1.71%        | 1.71%           |            1   | 50.00%                    |
| tiered_liquidity_then_score                     |         2 | 1.55%          | 21.41%              | 0.00%          | 50.00%     | 1.71%        | 1.71%           |            1   | 50.00%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                         |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:-------------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | baseline_full_score                              |           0 |         6 |       0.735384 |    0.103862  |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                      |           0 |         6 |       0.641676 |    0.0887777 |   0.833333 |   -0.000442128 |
| index_bucket  | index_neutral | baseline_full_liquidity_detail                   |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail                 |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail                 |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail                 |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail           |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_20_then_dynamic_factor                   |           0 |         6 |       0.461172 |    0.0702188 |   0.666667 |   -0.0237507   |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_0pct                   |           0 |         6 |       0.460165 |    0.0673875 |   0.833333 |   -0.000123414 |
| index_bucket  | index_neutral | liq_top_20_then_bs_v2                            |           0 |         6 |       0.460165 |    0.0673875 |   0.833333 |   -0.000123414 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct                   |           0 |         6 |       0.432184 |    0.0634372 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | liq20_score_b_bonus_8pct                         |           0 |         6 |       0.430265 |    0.0655878 |   0.833333 |   -0.0755301   |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct                   |           0 |         6 |       0.428504 |    0.0629995 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj                   |           0 |         6 |       0.420213 |    0.0661098 |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | liq_top_10_then_liq_breakout_adj                 |           0 |         6 |       0.360466 |    0.0585813 |   0.5      |   -0.102902    |
| index_bucket  | index_neutral | liq_top_10_then_score                            |           0 |         6 |       0.360466 |    0.0585813 |   0.5      |   -0.102902    |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_cap2              |           0 |         6 |       0.360466 |    0.0585813 |   0.5      |   -0.102902    |
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_cap2        |           0 |         6 |       0.355059 |    0.0568766 |   0.5      |   -0.0792052   |
| index_bucket  | index_neutral | liq_top_10_then_dynamic_factor                   |           0 |         6 |       0.346457 |    0.0542153 |   0.666667 |   -0.0237507   |
| index_bucket  | index_neutral | tiered_liquidity_then_dynamic_factor             |           0 |         6 |       0.346457 |    0.0542153 |   0.666667 |   -0.0237507   |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_10pt      |           0 |         6 |       0.345166 |    0.05581   |   0.5      |   -0.0861748   |
| index_bucket  | index_neutral | liq_top_10_then_score_industry_penalty_5pt       |           0 |         6 |       0.345166 |    0.05581   |   0.5      |   -0.0861748   |
| index_bucket  | index_neutral | tiered_liquidity_then_score_industry_penalty_5pt |           0 |         6 |       0.326754 |    0.0525862 |   0.5      |   -0.0711827   |
| index_bucket  | index_neutral | liq_top_30_then_liq_breakout_adj                 |           0 |         6 |       0.320481 |    0.0521829 |   0.833333 |   -0.152092    |
| index_bucket  | index_neutral | liq_top_30_then_score                            |           0 |         6 |       0.320481 |    0.0521829 |   0.833333 |   -0.152092    |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_8pct                   |           0 |         6 |       0.312034 |    0.0482445 |   0.833333 |   -0.053569    |
| index_bucket  | index_neutral | liq20_score_b_bonus_5pct                         |           0 |         6 |       0.291952 |    0.0486956 |   0.666667 |   -0.0755301   |
| index_bucket  | index_neutral | tiered_liquidity_then_liq_breakout_adj           |           0 |         6 |       0.287914 |    0.0491579 |   0.5      |   -0.124831    |
| index_bucket  | index_neutral | tiered_liquidity_then_score                      |           0 |         6 |       0.287914 |    0.0491579 |   0.5      |   -0.124831    |
| index_bucket  | index_neutral | baseline_b_consensus                             |           0 |         3 |       0.255821 |    0.0837701 |   0.666667 |   -0.0390992   |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_213025_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
