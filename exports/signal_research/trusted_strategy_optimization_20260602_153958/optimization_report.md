# 可信策略优化矩阵报告

## 口径

- 回测窗口：2025-06-02 至 2026-05-29
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：2

## Calmar 排名前 30

| experiment_group   | experiment_name   | strategy                                  |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:------------------|:------------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:--------------------------|---------------------:|
| smoke              | industry_cap2     | tiered_liquidity_then_bs_v2_industry_cap2 |    1.6363e+06  | 227.26%        | 249.06%             | -23.44%        |   10.627 |                   9.697 |           237 |    45.714  |      32112.3 | 34.14%                    |                    3 |
| smoke              | current_baseline  | tiered_liquidity_then_bs_v2               |    1.54472e+06 | 208.94%        | 228.49%             | -23.28%        |    9.815 |                   8.976 |           237 |    45.6537 |      30152.1 | 35.11%                    |                    4 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_experiments.json`：实验参数与回测子目录。
