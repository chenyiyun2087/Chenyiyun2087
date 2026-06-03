# 可信策略优化矩阵报告

## 口径

- 回测窗口：2023-01-01 至 2026-06-02
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：1

## Calmar 排名前 30

| experiment_group   | experiment_name          | strategy                                         |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:-------------------------|:-------------------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| industry           | tiered_industry_controls | tiered_liquidity_then_bs_v2                      |         146850 | -70.63%        | -31.31%             | -94.20%        |   -0.332 |                  -0.75  |           819 |    161.048 |      22951.6 | -55.72%               | 43.85%                    |                    4 |
| industry           | tiered_industry_controls | tiered_liquidity_then_bs_v2_industry_cap2        |         132626 | -73.47%        | -33.42%             | -93.84%        |   -0.356 |                  -0.783 |           819 |    161.09  |      21491.1 | -51.35%               | 43.25%                    |                    4 |
| industry           | tiered_industry_controls | tiered_liquidity_then_bs_v2_industry_cap1        |         100684 | -79.86%        | -38.82%             | -88.50%        |   -0.439 |                  -0.902 |           816 |    160.021 |      23593.3 | -20.26%               | 30.95%                    |                    2 |
| industry           | tiered_industry_controls | tiered_liquidity_then_bs_v2_industry_penalty_5pt |          99912 | -80.02%        | -38.96%             | -88.50%        |   -0.44  |                  -0.904 |           816 |    160.022 |      23588.6 | -20.26%               | 30.97%                    |                    2 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
