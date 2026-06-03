# 可信策略优化矩阵报告

## 口径

- 回测窗口：2023-01-01 至 2026-06-02
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：3

## Calmar 排名前 30

| experiment_group     | experiment_name    | strategy                                   |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:---------------------|:-------------------|:-------------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| market_gate_position | market_gate_pos0.5 | baseline_full_liquidity_detail_market_gate |         475270 | -4.95%         | -1.54%              | -43.42%        |   -0.036 |                  -0.114 |           833 |    79.6676 |      23983.6 | -5.57%                | 18.81%                    |                    4 |
| market_gate_position | market_gate_pos0.5 | tiered_liquidity_then_bs_v2_market_gate    |         421027 | -15.79%        | -5.13%              | -75.58%        |   -0.068 |                  -0.209 |           806 |    80.0749 |      16319.6 | -30.49%               | 22.24%                    |                    3 |
| market_gate_position | market_gate_pos0.7 | baseline_full_liquidity_detail_market_gate |         413652 | -17.27%        | -5.65%              | -54.17%        |   -0.104 |                  -0.319 |           837 |   112.063  |      31138.9 | -8.69%                | 25.35%                    |                    4 |
| market_gate_position | market_gate_pos0.7 | tiered_liquidity_then_bs_v2_market_gate    |         344962 | -31.01%        | -10.76%             | -87.64%        |   -0.123 |                  -0.354 |           804 |   111.335  |      17090.7 | -35.00%               | 33.73%                    |                    4 |
| market_gate_position | market_gate_pos1   | baseline_full_liquidity_detail_market_gate |         283045 | -43.39%        | -16.01%             | -75.27%        |   -0.213 |                  -0.576 |           829 |   160.871  |      36130.3 | -11.81%               | 37.66%                    |                    4 |
| market_gate_position | market_gate_pos1   | tiered_liquidity_then_bs_v2_market_gate    |         231196 | -53.76%        | -21.06%             | -89.70%        |   -0.235 |                  -0.599 |           804 |   160.795  |      24229.3 | -24.22%               | 43.08%                    |                    4 |
| market_gate_position | market_gate_pos0.5 | tiered_liquidity_then_bs_v2                |         289215 | -42.16%        | -15.45%             | -59.80%        |   -0.258 |                  -0.705 |           818 |    79.5329 |      18817.6 | -7.31%                | 21.45%                    |                    3 |
| market_gate_position | market_gate_pos0.7 | tiered_liquidity_then_bs_v2                |         204346 | -59.13%        | -23.99%             | -75.21%        |   -0.319 |                  -0.786 |           821 |   112.248  |      21352.6 | -20.87%               | 25.89%                    |                    3 |
| market_gate_position | market_gate_pos1   | tiered_liquidity_then_bs_v2                |         146850 | -70.63%        | -31.31%             | -94.20%        |   -0.332 |                  -0.75  |           819 |   161.048  |      22951.6 | -55.72%               | 43.85%                    |                    4 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
