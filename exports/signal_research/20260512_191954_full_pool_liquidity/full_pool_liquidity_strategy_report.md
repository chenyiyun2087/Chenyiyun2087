# 全量池流动性策略研究报告

## 回测口径

- 信号来源：`score_rank_daily`，仅使用信号日当日已经存在的评分字段。
- 买入/卖出：信号日 T 生成，T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 组合：Top 5 等权，`rebalance_step=10`。为 1 时是每日滚动事件研究，为持仓天数时更接近非重叠调仓。
- 成本：`cost_rate` 作为单次买入到卖出的合计交易成本，从组合收益中扣除。
- 全量池有效门槛：单日评分股票数不少于 5000。

## 全样本排名

| strategy                       |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:-------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| liq_top_10_then_score          |         4 | 21.38%         | 239.03%             | -2.04%         | 50.00%     | 5.42%        | 0.41%           |           0.75 |
| baseline_full_liquidity        |         4 | 7.07%          | 53.77%              | -2.76%         | 50.00%     | 1.97%        | -0.55%          |           0    |
| liq_top_30_then_score          |         4 | 2.95%          | 20.10%              | -5.55%         | 25.00%     | 0.87%        | -1.50%          |           0.5  |
| liq20_score_b_bonus_5pct       |         4 | -7.05%         | -36.92%             | -10.91%        | 25.00%     | -1.63%       | -2.53%          |           1    |
| baseline_full_score            |         4 | -10.82%        | -51.38%             | -18.18%        | 25.00%     | -2.49%       | -4.21%          |           0.5  |
| baseline_b_score_v2            |         4 | -13.08%        | -58.64%             | -9.43%         | 0.00%      | -3.43%       | -3.21%          |           5    |
| liq20_score_b_bonus_3pct       |         4 | -13.44%        | -59.72%             | -10.91%        | 25.00%     | -3.30%       | -5.55%          |           0.75 |
| liq20_bs_score_v2_b_bonus_3pct |         4 | -15.59%        | -65.62%             | -16.41%        | 25.00%     | -4.03%       | -3.58%          |           1.25 |
| liq20_score_b_bonus_0pct       |         4 | -16.36%        | -67.55%             | -10.91%        | 25.00%     | -4.21%       | -5.55%          |           0.5  |
| liq_top_20_then_score          |         4 | -16.36%        | -67.55%             | -10.91%        | 25.00%     | -4.21%       | -5.55%          |           0.5  |
| liq20_score_b_bonus_8pct       |         4 | -16.38%        | -67.59%             | -12.27%        | 25.00%     | -4.30%       | -4.73%          |           1.75 |
| liq20_bs_score_v2_b_bonus_0pct |         4 | -17.06%        | -69.22%             | -17.86%        | 25.00%     | -4.42%       | -3.58%          |           1    |
| liq_top_20_then_bs_v2          |         4 | -17.06%        | -69.22%             | -17.86%        | 25.00%     | -4.42%       | -3.58%          |           1    |
| liq20_bs_score_v2_b_bonus_5pct |         4 | -24.04%        | -82.32%             | -21.60%        | 0.00%      | -6.61%       | -6.41%          |           2.25 |
| liq20_bs_score_v2_b_bonus_8pct |         4 | -27.79%        | -87.14%             | -25.47%        | 0.00%      | -7.75%       | -7.80%          |           2.75 |

## 测试区间排名

| strategy                       |   periods | total_return   | annualized_return   | max_drawdown   | win_rate   | avg_return   | median_return   |   avg_bs_count |
|:-------------------------------|----------:|:---------------|:--------------------|:---------------|:-----------|:-------------|:----------------|---------------:|
| baseline_full_liquidity        |         1 | 13.77%         | 2479.97%            | 0.00%          | 100.00%    | 13.77%       | 13.77%          |              0 |
| baseline_full_score            |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| liq_top_30_then_score          |         1 | 10.06%         | 1018.66%            | 0.00%          | 100.00%    | 10.06%       | 10.06%          |              0 |
| liq20_score_b_bonus_3pct       |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 |
| liq20_score_b_bonus_5pct       |         1 | 7.57%          | 529.37%             | 0.00%          | 100.00%    | 7.57%        | 7.57%           |              1 |
| liq20_score_b_bonus_0pct       |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 |
| liq_top_20_then_score          |         1 | 3.94%          | 164.86%             | 0.00%          | 100.00%    | 3.94%        | 3.94%           |              0 |
| liq20_bs_score_v2_b_bonus_0pct |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq20_bs_score_v2_b_bonus_3pct |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq_top_20_then_bs_v2          |         1 | 1.53%          | 46.54%              | 0.00%          | 100.00%    | 1.53%        | 1.53%           |              1 |
| liq20_score_b_bonus_8pct       |         1 | 1.31%          | 38.70%              | 0.00%          | 100.00%    | 1.31%        | 1.31%           |              2 |
| liq_top_10_then_score          |         1 | -0.10%         | -2.46%              | 0.00%          | 0.00%      | -0.10%       | -0.10%          |              1 |
| baseline_b_score_v2            |         1 | -1.29%         | -27.97%             | 0.00%          | 0.00%      | -1.29%       | -1.29%          |              5 |
| liq20_bs_score_v2_b_bonus_5pct |         1 | -6.21%         | -80.15%             | 0.00%          | 0.00%      | -6.21%       | -6.21%          |              4 |
| liq20_bs_score_v2_b_bonus_8pct |         1 | -6.21%         | -80.15%             | 0.00%          | 0.00%      | -6.21%       | -6.21%          |              4 |

## 输出文件

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_191954_full_pool_liquidity/full_pool_liquidity_strategy_summary.csv`
- Cycles CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_191954_full_pool_liquidity/full_pool_liquidity_strategy_cycles.csv`
- Trades CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_191954_full_pool_liquidity/full_pool_liquidity_strategy_trades.csv`
- Monthly CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_191954_full_pool_liquidity/full_pool_liquidity_strategy_monthly.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260512_191954_full_pool_liquidity/full_pool_liquidity_strategy_report.json`
