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
| s_liquidity            |       239826 |            44 |      1         |       1         |
| s_breakout             |       239826 |            44 |      1         |       1         |
| bs_score_v2            |       239826 |            44 |      1         |       1         |
| bs_consensus_score     |        10601 |             2 |      0.0442029 |       0.0454545 |
| bs_model_rank_score    |         5397 |             2 |      0.0225038 |       0.0454545 |

## 未形成有效回测的策略

`baseline_b_consensus`, `liq_top_20_then_model_rank`, `liq_top_20_then_consensus`, `liq20_bs_model_rank_score_b_bonus_0pct`, `liq20_bs_model_rank_score_b_bonus_3pct`, `liq20_bs_model_rank_score_b_bonus_5pct`, `liq20_bs_model_rank_score_b_bonus_8pct`

## 全样本排名

| strategy                               |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:---------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| baseline_full_liquidity                |        34 | 154.54%        | 99.86%              | -26.75%        | 52.94%     | 3.05%        | 0.89%           |      0.0294118 |
| baseline_full_score                    |        34 | 30.03%         | 21.49%              | -59.02%        | 44.12%     | 1.14%        | -1.09%          |      0.352941  |
| liq_top_10_then_liq_breakout_adj       |        34 | -7.31%         | -5.47%              | -73.86%        | 38.24%     | 0.35%        | -1.50%          |      0.735294  |
| liq_top_10_then_score                  |        34 | -7.31%         | -5.47%              | -73.86%        | 38.24%     | 0.35%        | -1.50%          |      0.735294  |
| tiered_liquidity_then_bs_v2            |        34 | -9.29%         | -6.97%              | -66.95%        | 47.06%     | 0.09%        | -0.07%          |      1.20588   |
| baseline_full_liq_breakout_adj         |        34 | -13.96%        | -10.54%             | -64.85%        | 44.12%     | -0.11%       | -1.09%          |      0.382353  |
| liq_top_30_then_liq_breakout_adj       |        34 | -28.01%        | -21.62%             | -66.85%        | 44.12%     | -0.72%       | -1.84%          |      0.470588  |
| liq_top_30_then_score                  |        34 | -28.01%        | -21.62%             | -66.85%        | 44.12%     | -0.72%       | -1.84%          |      0.470588  |
| liq20_bs_score_v2_b_bonus_3pct         |        34 | -30.43%        | -23.58%             | -73.76%        | 44.12%     | -0.68%       | -0.59%          |      1.38235   |
| tiered_liquidity_then_liq_breakout_adj |        34 | -40.77%        | -32.18%             | -75.93%        | 38.24%     | -1.04%       | -1.76%          |      1.20588   |
| tiered_liquidity_then_score            |        34 | -40.77%        | -32.18%             | -75.93%        | 38.24%     | -1.04%       | -1.76%          |      1.20588   |
| baseline_b_score_v2                    |        34 | -45.57%        | -36.29%             | -69.55%        | 44.12%     | -1.54%       | -1.58%          |      5         |
| liq20_bs_score_v2_b_bonus_5pct         |        34 | -48.42%        | -38.78%             | -76.80%        | 41.18%     | -1.54%       | -2.29%          |      2.02941   |
| liq20_bs_score_v2_b_bonus_0pct         |        34 | -51.68%        | -41.67%             | -77.45%        | 38.24%     | -1.76%       | -2.19%          |      0.676471  |
| liq_top_20_then_bs_v2                  |        34 | -51.68%        | -41.67%             | -77.45%        | 38.24%     | -1.76%       | -2.19%          |      0.676471  |
| liq20_bs_score_v2_b_bonus_8pct         |        34 | -54.37%        | -44.09%             | -80.22%        | 38.24%     | -1.88%       | -4.92%          |      2.79412   |
| liq20_score_b_bonus_5pct               |        34 | -56.90%        | -46.41%             | -75.52%        | 38.24%     | -2.12%       | -2.29%          |      1.67647   |
| liq20_score_b_bonus_8pct               |        34 | -61.54%        | -50.75%             | -75.96%        | 38.24%     | -2.44%       | -2.44%          |      2.26471   |
| liq20_score_b_bonus_3pct               |        34 | -69.50%        | -58.53%             | -78.64%        | 35.29%     | -3.11%       | -3.24%          |      1.11765   |
| liq20_score_b_bonus_0pct               |        34 | -69.78%        | -58.80%             | -77.40%        | 41.18%     | -3.15%       | -3.85%          |      0.5       |

## 测试区间排名

| strategy                               |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:---------------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| liq_top_10_then_liq_breakout_adj       |         7 | 120.81%        | 1631.74%            | -0.10%         | 71.43%     | 12.38%       | 15.47%          |       0.571429 |
| liq_top_10_then_score                  |         7 | 120.81%        | 1631.74%            | -0.10%         | 71.43%     | 12.38%       | 15.47%          |       0.571429 |
| tiered_liquidity_then_liq_breakout_adj |         7 | 100.30%        | 1119.16%            | -0.10%         | 71.43%     | 10.93%       | 14.83%          |       0.714286 |
| tiered_liquidity_then_score            |         7 | 100.30%        | 1119.16%            | -0.10%         | 71.43%     | 10.93%       | 14.83%          |       0.714286 |
| tiered_liquidity_then_bs_v2            |         7 | 89.08%         | 890.61%             | -0.10%         | 71.43%     | 9.82%        | 9.73%           |       0.857143 |
| baseline_full_liquidity                |         7 | 87.84%         | 867.50%             | -3.65%         | 71.43%     | 9.83%        | 13.77%          |       0.142857 |
| baseline_full_liq_breakout_adj         |         7 | 61.05%         | 455.95%             | 0.00%          | 85.71%     | 7.33%        | 6.95%           |       0.142857 |
| liq20_bs_score_v2_b_bonus_3pct         |         7 | 54.23%         | 375.79%             | 0.00%          | 85.71%     | 6.59%        | 8.30%           |       0.857143 |
| baseline_full_score                    |         7 | 48.19%         | 312.02%             | 0.00%          | 85.71%     | 5.92%        | 4.62%           |       0.142857 |
| liq20_score_b_bonus_5pct               |         7 | 46.96%         | 299.90%             | 0.00%          | 85.71%     | 5.93%        | 7.57%           |       1.28571  |
| liq20_bs_score_v2_b_bonus_0pct         |         7 | 44.02%         | 271.77%             | 0.00%          | 85.71%     | 5.49%        | 7.11%           |       0.571429 |
| liq_top_20_then_bs_v2                  |         7 | 44.02%         | 271.77%             | 0.00%          | 85.71%     | 5.49%        | 7.11%           |       0.571429 |
| liq_top_30_then_liq_breakout_adj       |         7 | 42.94%         | 261.85%             | 0.00%          | 85.71%     | 5.36%        | 8.12%           |       0.285714 |
| liq_top_30_then_score                  |         7 | 42.94%         | 261.85%             | 0.00%          | 85.71%     | 5.36%        | 8.12%           |       0.285714 |
| baseline_b_score_v2                    |         7 | 38.57%         | 223.59%             | -1.29%         | 71.43%     | 4.88%        | 2.63%           |       5        |
| liq20_score_b_bonus_8pct               |         7 | 38.04%         | 219.14%             | 0.00%          | 85.71%     | 5.07%        | 3.68%           |       1.85714  |
| liq20_score_b_bonus_0pct               |         7 | 36.31%         | 205.00%             | 0.00%          | 85.71%     | 4.72%        | 3.94%           |       0.285714 |
| liq_top_20_then_liq_breakout_adj       |         7 | 36.31%         | 205.00%             | 0.00%          | 85.71%     | 4.72%        | 3.94%           |       0.285714 |
| liq_top_20_then_score                  |         7 | 36.31%         | 205.00%             | 0.00%          | 85.71%     | 4.72%        | 3.94%           |       0.285714 |
| liq20_score_b_bonus_3pct               |         7 | 35.57%         | 199.08%             | 0.00%          | 85.71%     | 4.58%        | 5.29%           |       0.571429 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- Coverage CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_coverage.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_195337_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
