# 可信策略优化矩阵报告

## 口径

- 回测窗口：2023-01-01 至 2026-06-02
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：5

## Calmar 排名前 30

| experiment_group   | experiment_name   | strategy                    |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:------------------|:----------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| stop_loss          | stop0             | tiered_liquidity_then_bs_v2 |       146850   | -70.63%        | -31.31%             | -94.20%        |   -0.332 |                  -0.75  |           819 |    161.048 |      22951.6 | -55.72%               | 43.85%                    |                    4 |
| stop_loss          | stop8             | tiered_liquidity_then_bs_v2 |       123306   | -75.34%        | -34.90%             | -90.45%        |   -0.386 |                  -0.833 |          1095 |    205.851 |      27200.7 | -16.03%               | 32.28%                    |                    4 |
| stop_loss          | stop15            | tiered_liquidity_then_bs_v2 |       116944   | -76.61%        | -35.94%             | -91.75%        |   -0.392 |                  -0.835 |           904 |    174.641 |      24207.7 | -21.33%               | 40.06%                    |                    4 |
| stop_loss          | stop12            | tiered_liquidity_then_bs_v2 |        89718   | -82.06%        | -40.94%             | -92.72%        |   -0.442 |                  -0.885 |           943 |    179.497 |      22146.7 | -15.84%               | 34.54%                    |                    4 |
| stop_loss          | stop10            | tiered_liquidity_then_bs_v2 |        50059.6 | -89.99%        | -50.62%             | -93.89%        |   -0.539 |                  -0.958 |          1031 |    196.977 |      23695.1 | -16.22%               | 34.90%                    |                    4 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
