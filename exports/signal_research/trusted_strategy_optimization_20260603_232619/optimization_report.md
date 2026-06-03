# 可信策略优化矩阵报告

## 口径

- 回测窗口：2023-01-01 至 2026-06-02
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：1

## Calmar 排名前 30

| experiment_group   | experiment_name                  | strategy                               |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:---------------------------------|:---------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| adaptive           | attack_defensive_fallback_review | baseline_full_liquidity_detail         |       283045   | -43.39%        | -16.01%             | -75.27%        |   -0.213 |                  -0.576 |           829 |    160.871 |      36130.3 | -11.81%               | 37.66%                    |                    4 |
| adaptive           | attack_defensive_fallback_review | adaptive_style_switch_dynamic_position |       268830   | -46.23%        | -17.32%             | -66.46%        |   -0.261 |                  -0.696 |           819 |    116.18  |      30174.5 | -9.45%                | 27.85%                    |                    4 |
| adaptive           | attack_defensive_fallback_review | tiered_liquidity_then_bs_v2            |       146850   | -70.63%        | -31.31%             | -94.20%        |   -0.332 |                  -0.75  |           819 |    161.048 |      22951.6 | -55.72%               | 43.85%                    |                    4 |
| adaptive           | attack_defensive_fallback_review | baseline_full_score                    |       121280   | -75.74%        | -35.23%             | -91.67%        |   -0.384 |                  -0.826 |           823 |    162.227 |      24423.7 | -46.59%               | 38.26%                    |                    4 |
| adaptive           | attack_defensive_fallback_review | adaptive_style_switch                  |        99602.4 | -80.08%        | -39.02%             | -88.72%        |   -0.44  |                  -0.903 |           815 |    162.185 |      30741   | -22.92%               | 38.61%                    |                    5 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
