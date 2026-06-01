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

| strategy                               |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:---------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| liq_top_10_then_liq_breakout_adj       |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 |
| liq_top_10_then_score                  |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 |
| tiered_liquidity_then_liq_breakout_adj |         4 | 11.97%         | 103.84%             | -2.04%         | 50.00%     | 3.00%        | 1.08%           |           1.25 |
| tiered_liquidity_then_score            |         4 | 11.97%         | 103.84%             | -2.04%         | 50.00%     | 3.00%        | 1.08%           |           1.25 |
| baseline_full_liquidity                |         4 | 7.07%          | 53.77%              | -2.76%         | 50.00%     | 1.97%        | -0.55%          |           0    |
| tiered_liquidity_then_bs_v2            |         4 | 6.13%          | 45.46%              | -8.72%         | 50.00%     | 1.77%        | 1.91%           |           1.25 |
| liq_top_30_then_liq_breakout_adj       |         4 | 2.95%          | 20.10%              | -5.55%         | 25.00%     | 0.87%        | -1.50%          |           0.5  |
| liq_top_30_then_score                  |         4 | 2.95%          | 20.10%              | -5.55%         | 25.00%     | 0.87%        | -1.50%          |           0.5  |
| baseline_full_liquidity_detail         |         4 | -6.63%         | -35.08%             | -0.92%         | 25.00%     | -1.20%       | -0.67%          |           0    |
| liq_top_10_then_liquidity_detail       |         4 | -6.63%         | -35.08%             | -0.92%         | 25.00%     | -1.20%       | -0.67%          |           0    |
| liq_top_20_then_liquidity_detail       |         4 | -6.63%         | -35.08%             | -0.92%         | 25.00%     | -1.20%       | -0.67%          |           0    |
| liq_top_30_then_liquidity_detail       |         4 | -6.63%         | -35.08%             | -0.92%         | 25.00%     | -1.20%       | -0.67%          |           0    |
| tiered_liquidity_then_liquidity_detail |         4 | -6.63%         | -35.08%             | -0.92%         | 25.00%     | -1.20%       | -0.67%          |           0    |
| liq20_score_b_bonus_5pct               |         4 | -7.05%         | -36.92%             | -10.91%        | 25.00%     | -1.63%       | -2.53%          |           1    |
| baseline_full_liq_breakout_adj         |         4 | -10.82%        | -51.38%             | -18.18%        | 25.00%     | -2.49%       | -4.21%          |           0.5  |
| baseline_full_score                    |         4 | -10.82%        | -51.38%             | -18.18%        | 25.00%     | -2.49%       | -4.21%          |           0.5  |
| baseline_b_score_v2                    |         4 | -13.08%        | -58.64%             | -9.43%         | 0.00%      | -3.43%       | -3.21%          |           5    |
| liq20_score_b_bonus_3pct               |         4 | -13.44%        | -59.72%             | -10.91%        | 25.00%     | -3.30%       | -5.55%          |           0.75 |
| liq20_bs_score_v2_b_bonus_3pct         |         4 | -15.59%        | -65.62%             | -16.41%        | 25.00%     | -4.03%       | -3.58%          |           1.25 |
| liq20_score_b_bonus_0pct               |         4 | -16.36%        | -67.55%             | -10.91%        | 25.00%     | -4.21%       | -5.55%          |           0.5  |

## 测试区间排名

| strategy                               |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:---------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| baseline_full_liquidity                |         1 | 13.77%         | 2479.97%            | 0.00%          | 100.00%    | 13.77%       | 13.77%          |              0 |
| baseline_full_liq_breakout_adj         |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| baseline_full_score                    |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| liq_top_30_then_liq_breakout_adj       |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| liq_top_30_then_score                  |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| liq20_score_b_bonus_3pct               |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 |
| liq20_score_b_bonus_5pct               |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 |
| liq20_score_b_bonus_0pct               |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 |
| liq_top_20_then_liq_breakout_adj       |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 |
| liq_top_20_then_score                  |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 |
| liq20_bs_score_v2_b_bonus_0pct         |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq20_bs_score_v2_b_bonus_3pct         |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq_top_20_then_bs_v2                  |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq20_score_b_bonus_8pct               |         1 | 1.31%          | 38.70%              | 0.00%          | 100.00%    | 1.31%        | 1.31%           |              2 |
| liq_top_10_then_liq_breakout_adj       |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| liq_top_10_then_score                  |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| tiered_liquidity_then_bs_v2            |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| tiered_liquidity_then_liq_breakout_adj |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| tiered_liquidity_then_score            |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| baseline_full_liquidity_detail         |         1 | -0.43%         | -10.29%             | 0.00%          | 0.00%      | -0.43%       | -0.43%          |              0 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195723_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
