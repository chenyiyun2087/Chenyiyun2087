# 可信策略优化矩阵报告

## 口径

- 回测窗口：2026-03-02 至 2026-05-29
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：2

## Calmar 排名前 30

| experiment_group   | experiment_name   | strategy                                  |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:------------------|:------------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| smoke              | current_baseline  | tiered_liquidity_then_bs_v2               |         589195 | 17.84%         | 101.60%             | -15.92%        |    6.38  |                   1.12  |            55 |    10.1087 |      4083.03 | -7.20%                | 31.73%                    |                    3 |
| smoke              | industry_cap2     | tiered_liquidity_then_bs_v2_industry_cap2 |         586850 | 17.37%         | 98.19%              | -16.34%        |    6.009 |                   1.063 |            55 |    10.0826 |      4050.8  | -6.99%                | 28.93%                    |                    2 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
