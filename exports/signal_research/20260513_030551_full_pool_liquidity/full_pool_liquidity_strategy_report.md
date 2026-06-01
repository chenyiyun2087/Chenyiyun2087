# 全量池流动性策略研究报告

## 回测口径

- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。
- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 组合：Top 5 等权，`rebalance_step=10`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。
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
| baseline_full_dynamic_factor_industry_cap2       | trusted      |         7 | 67.66%         | 542.62%             | -4.55%         | 57.14%     | 8.28%        | 3.01%           |       0.428571 |             1        | 28.57%                    |
| baseline_full_dynamic_factor                     | trusted      |         7 | 65.21%         | 509.48%             | -4.55%         | 57.14%     | 8.07%        | 1.50%           |       0.428571 |             1        | 31.43%                    |
| baseline_full_score                              | trusted      |         7 | 56.18%         | 397.78%             | -21.91%        | 71.43%     | 7.47%        | 13.80%          |       0.857143 |             1        | 31.43%                    |
| baseline_full_score_hist_mdd_position            | trusted      |         7 | 56.18%         | 397.78%             | -21.91%        | 71.43%     | 7.47%        | 13.80%          |       0.857143 |             1        | 31.43%                    |
| baseline_full_score_market_gate                  | trusted      |         7 | 56.18%         | 397.78%             | -21.91%        | 71.43%     | 7.47%        | 13.80%          |       0.857143 |             1        | 31.43%                    |
| baseline_full_liquidity_detail                   | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| baseline_full_liquidity_detail_market_gate       | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| liq_top_10_then_liquidity_detail                 | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| liq_top_20_then_liquidity_detail                 | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| liq_top_30_then_liquidity_detail                 | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| tiered_liquidity_then_liquidity_detail           | trusted      |         7 | 53.83%         | 371.38%             | -4.13%         | 85.71%     | 6.55%        | 5.14%           |       0        |             1        | 37.14%                    |
| tiered_liquidity_then_bs_v2                      | trusted      |         7 | 51.23%         | 343.29%             | -7.92%         | 71.43%     | 6.48%        | 3.31%           |       0.857143 |             1        | 28.57%                    |
| tiered_liquidity_then_bs_v2_market_gate          | trusted      |         7 | 51.23%         | 343.29%             | -7.92%         | 71.43%     | 6.48%        | 3.31%           |       0.857143 |             1        | 28.57%                    |
| tiered_liquidity_then_dynamic_ic_factor          | trusted      |         7 | 46.54%         | 295.73%             | -8.94%         | 57.14%     | 6.17%        | 0.94%           |       0.285714 |             1        | 25.71%                    |
| liq_top_30_then_dynamic_factor                   | trusted      |         7 | 46.51%         | 295.50%             | -4.55%         | 57.14%     | 6.10%        | 1.50%           |       0.428571 |             1        | 34.29%                    |
| liq_top_20_then_dynamic_ic_factor                | trusted      |         7 | 40.21%         | 237.63%             | -17.12%        | 57.14%     | 5.80%        | 0.94%           |       0.285714 |             1        | 22.86%                    |
| baseline_full_liquidity_detail_hist_mdd_position | trusted      |         7 | 38.20%         | 220.46%             | -4.30%         | 71.43%     | 4.91%        | 4.83%           |       0        |             0.781534 | 37.14%                    |
| liq_top_20_then_dynamic_factor                   | trusted      |         7 | 37.93%         | 218.28%             | -4.55%         | 57.14%     | 5.19%        | 1.50%           |       0.428571 |             1        | 28.57%                    |
| liq_top_10_then_score_industry_penalty_10pt      | trusted      |         7 | 35.67%         | 199.88%             | -8.62%         | 57.14%     | 4.91%        | 0.86%           |       0.571429 |             1        | 20.00%                    |
| liq_top_10_then_score_industry_cap2              | trusted      |         7 | 34.72%         | 192.38%             | -11.17%        | 42.86%     | 4.88%        | -0.98%          |       0.714286 |             1        | 28.57%                    |

## 测试区间排名

| strategy                                         | pit_status   |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |   avg_gross_exposure | avg_max_industry_weight   |
|:-------------------------------------------------|:-------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|---------------------:|:--------------------------|
| baseline_full_liquidity_detail                   | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| baseline_full_liquidity_detail_market_gate       | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| liq_top_10_then_liquidity_detail                 | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| liq_top_20_then_liquidity_detail                 | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| liq_top_30_then_liquidity_detail                 | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| tiered_liquidity_then_liquidity_detail           | trusted      |         2 | 21.10%         | 1015.43%            | 0.00%          | 100.00%    | 10.16%       | 10.16%          |            0   |             1        | 60.00%                    |
| tiered_liquidity_then_dynamic_ic_factor          | trusted      |         2 | 18.27%         | 728.76%             | 0.00%          | 50.00%     | 9.78%        | 9.78%           |            0   |             1        | 40.00%                    |
| baseline_full_liquidity_detail_hist_mdd_position | trusted      |         2 | 14.80%         | 469.49%             | 0.00%          | 100.00%    | 7.17%        | 7.17%           |            0   |             0.668421 | 60.00%                    |
| liq_top_10_then_score_industry_cap2              | trusted      |         2 | 14.34%         | 441.37%             | 0.00%          | 50.00%     | 7.25%        | 7.25%           |            0.5 |             1        | 40.00%                    |
| liq_top_10_then_score_industry_penalty_10pt      | trusted      |         2 | 13.04%         | 368.73%             | 0.00%          | 100.00%    | 6.47%        | 6.47%           |            0.5 |             1        | 20.00%                    |
| liq_top_10_then_score_industry_penalty_5pt       | trusted      |         2 | 10.99%         | 271.98%             | 0.00%          | 50.00%     | 5.55%        | 5.55%           |            0.5 |             1        | 30.00%                    |
| liq_top_10_then_liq_breakout_adj                 | trusted      |         2 | 9.53%          | 214.93%             | 0.00%          | 50.00%     | 5.16%        | 5.16%           |            0.5 |             1        | 50.00%                    |
| liq_top_10_then_score                            | trusted      |         2 | 9.53%          | 214.93%             | 0.00%          | 50.00%     | 5.16%        | 5.16%           |            0.5 |             1        | 50.00%                    |
| tiered_liquidity_then_bs_v2                      | trusted      |         2 | 8.28%          | 172.54%             | 0.00%          | 50.00%     | 4.83%        | 4.83%           |            1   |             1        | 40.00%                    |
| tiered_liquidity_then_bs_v2_market_gate          | trusted      |         2 | 8.28%          | 172.54%             | 0.00%          | 50.00%     | 4.83%        | 4.83%           |            1   |             1        | 40.00%                    |
| liq_top_20_then_dynamic_ic_factor                | trusted      |         2 | 7.65%          | 153.07%             | 0.00%          | 50.00%     | 5.51%        | 5.51%           |            0   |             1        | 30.00%                    |
| baseline_full_liquidity_industry_penalty_0p10pt  | trusted      |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p25pt  | trusted      |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_0p50pt  | trusted      |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   |             1        | 20.00%                    |
| baseline_full_liquidity_industry_penalty_1pt     | trusted      |         2 | 7.47%          | 148.00%             | 0.00%          | 100.00%    | 3.74%        | 3.74%           |            0   |             1        | 20.00%                    |

## 市场环境归因

| bucket_type   | bucket        | strategy                                   |   cost_rate |   periods |   total_return |   avg_return |   win_rate |   max_drawdown |
|:--------------|:--------------|:-------------------------------------------|------------:|----------:|---------------:|-------------:|-----------:|---------------:|
| index_bucket  | index_neutral | baseline_full_score                        |           0 |         6 |       0.735384 |    0.103862  |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | baseline_full_score_hist_mdd_position      |           0 |         6 |       0.735384 |    0.103862  |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | baseline_full_score_market_gate            |           0 |         6 |       0.735384 |    0.103862  |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | baseline_full_dynamic_factor_industry_cap2 |           0 |         6 |       0.686667 |    0.097648  |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | baseline_full_dynamic_factor               |           0 |         6 |       0.662036 |    0.0951409 |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2                |           0 |         6 |       0.641676 |    0.0887777 |   0.833333 |   -0.000442128 |
| index_bucket  | index_neutral | tiered_liquidity_then_bs_v2_market_gate    |           0 |         6 |       0.641676 |    0.0887777 |   0.833333 |   -0.000442128 |
| index_bucket  | index_neutral | liq_top_20_then_dynamic_ic_factor          |           0 |         6 |       0.624455 |    0.0905211 |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | tiered_liquidity_then_dynamic_ic_factor    |           0 |         6 |       0.54517  |    0.0805788 |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | baseline_full_dynamic_ic_factor            |           0 |         6 |       0.512544 |    0.0764874 |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | liq_top_30_then_dynamic_factor             |           0 |         6 |       0.473911 |    0.072214  |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | baseline_full_liquidity_detail             |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | baseline_full_liquidity_detail_market_gate |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_10_then_liquidity_detail           |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_20_then_liquidity_detail           |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq_top_30_then_liquidity_detail           |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | tiered_liquidity_then_liquidity_detail     |           0 |         6 |       0.46306  |    0.0678665 |   0.833333 |   -0.0413359   |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_0pct             |           0 |         6 |       0.460165 |    0.0673875 |   0.833333 |   -0.000123414 |
| index_bucket  | index_neutral | liq_top_20_then_bs_v2                      |           0 |         6 |       0.460165 |    0.0673875 |   0.833333 |   -0.000123414 |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct             |           0 |         6 |       0.432184 |    0.0634372 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_5pct_market_gate |           0 |         6 |       0.432184 |    0.0634372 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | liq20_score_b_bonus_8pct                   |           0 |         6 |       0.430265 |    0.0655878 |   0.833333 |   -0.0755301   |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct             |           0 |         6 |       0.428504 |    0.0629995 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | liq20_bs_score_v2_b_bonus_3pct_market_gate |           0 |         6 |       0.428504 |    0.0629995 |   0.833333 |   -0.00394391  |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj             |           0 |         6 |       0.420213 |    0.0661098 |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj_40p_30d     |           0 |         6 |       0.420213 |    0.0661098 |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | liq30_score_b_bonus_3pct_market_gate       |           0 |         6 |       0.398594 |    0.0624905 |   0.833333 |   -0.152092    |
| index_bucket  | index_neutral | liq_top_20_then_dynamic_factor             |           0 |         6 |       0.387604 |    0.0615159 |   0.666667 |   -0.0397871   |
| index_bucket  | index_neutral | baseline_full_liq_breakout_adj_50p_50d     |           0 |         6 |       0.365529 |    0.0589795 |   0.833333 |   -0.132245    |
| index_bucket  | index_neutral | liq_top_10_then_liq_breakout_adj           |           0 |         6 |       0.360466 |    0.0585813 |   0.5      |   -0.102902    |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_market_environment.csv`
- Environment Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_environment_summary.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260513_030551_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
