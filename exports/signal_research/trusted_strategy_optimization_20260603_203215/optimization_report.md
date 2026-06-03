# 可信策略优化矩阵报告

## 口径

- 回测窗口：2023-01-01 至 2026-06-02
- 初始资金：500,000.00
- TopN：5；最多持仓：5
- 信号：T 日评分；执行：T+1 开盘。
- 可信口径：不使用 `bs_model_*`，动态权重只使用已完成持有期样本。
- 实验数：4

## Calmar 排名前 30

| experiment_group   | experiment_name          | strategy                                   |   final_equity | total_return   | annualized_return   | max_drawdown   |   calmar |   return_drawdown_ratio |   trade_count |   turnover |   total_cost | max_single_day_loss   | avg_top_industry_weight   |   max_industry_names |
|:-------------------|:-------------------------|:-------------------------------------------|---------------:|:---------------|:--------------------|:---------------|---------:|------------------------:|--------------:|-----------:|-------------:|:----------------------|:--------------------------|---------------------:|
| hold_cost          | hold12_cost0.00075_slip0 | baseline_full_liquidity_detail             |       489717   | -2.06%         | -0.64%              | -65.17%        |   -0.01  |                  -0.032 |           690 |    135.242 |      41999.9 | -22.57%               | 37.84%                    |                    4 |
| hold_cost          | hold15_cost0.00075_slip0 | baseline_full_liquidity_detail             |       462566   | -7.49%         | -2.36%              | -74.27%        |   -0.032 |                  -0.101 |           547 |    107.173 |      31624.5 | -31.96%               | 43.16%                    |                    4 |
| hold_cost          | hold8_cost0.00075_slip0  | baseline_full_liquidity_detail             |       356524   | -28.70%        | -9.85%              | -71.32%        |   -0.138 |                  -0.402 |          1031 |    199.921 |      46744.4 | -14.72%               | 40.26%                    |                    4 |
| hold_cost          | hold12_cost0.00075_slip0 | tiered_liquidity_then_bs_v2                |       293764   | -41.25%        | -15.04%             | -76.70%        |   -0.196 |                  -0.538 |           688 |    134.937 |      32055.4 | -13.81%               | 38.66%                    |                    4 |
| hold_cost          | hold10_cost0.00075_slip0 | baseline_full_liquidity_detail             |       283045   | -43.39%        | -16.01%             | -75.27%        |   -0.213 |                  -0.576 |           829 |    160.871 |      36130.3 | -11.81%               | 37.66%                    |                    4 |
| hold_cost          | hold8_cost0.00075_slip0  | tiered_liquidity_then_bs_v2                |       215555   | -56.89%        | -22.74%             | -91.05%        |   -0.25  |                  -0.625 |          1023 |    199.673 |      34817.8 | -29.85%               | 41.02%                    |                    4 |
| hold_cost          | hold15_cost0.00075_slip0 | baseline_full_score                        |       216655   | -56.67%        | -22.62%             | -76.65%        |   -0.295 |                  -0.739 |           546 |    107.674 |      28776.2 | -40.76%               | 31.89%                    |                    4 |
| hold_cost          | hold10_cost0.00075_slip0 | tiered_liquidity_then_bs_v2                |       146850   | -70.63%        | -31.31%             | -94.20%        |   -0.332 |                  -0.75  |           819 |    161.048 |      22951.6 | -55.72%               | 43.85%                    |                    4 |
| hold_cost          | hold15_cost0.00075_slip0 | tiered_liquidity_then_bs_v2                |       163732   | -67.25%        | -28.98%             | -83.42%        |   -0.347 |                  -0.806 |           549 |    107.651 |      20135.9 | -13.55%               | 38.52%                    |                    4 |
| hold_cost          | hold12_cost0.00075_slip0 | baseline_full_score                        |       169603   | -66.08%        | -28.21%             | -77.03%        |   -0.366 |                  -0.858 |           685 |    136.844 |      28799.3 | -37.94%               | 35.23%                    |                    3 |
| hold_cost          | hold10_cost0.00075_slip0 | baseline_full_score                        |       121280   | -75.74%        | -35.23%             | -91.67%        |   -0.384 |                  -0.826 |           823 |    162.227 |      24423.7 | -46.59%               | 38.26%                    |                    4 |
| hold_cost          | hold12_cost0.00075_slip0 | baseline_full_dynamic_factor_industry_cap2 |        73817.3 | -85.24%        | -44.37%             | -95.50%        |   -0.465 |                  -0.893 |           678 |    134.149 |      18372.5 | -49.25%               | 36.81%                    |                    4 |
| hold_cost          | hold8_cost0.00075_slip0  | baseline_full_dynamic_factor_industry_cap2 |        62150.3 | -87.57%        | -47.23%             | -94.36%        |   -0.501 |                  -0.928 |          1008 |    196.526 |      26098   | -29.84%               | 35.19%                    |                    4 |
| hold_cost          | hold15_cost0.00075_slip0 | baseline_full_dynamic_factor_industry_cap2 |        68856   | -86.23%        | -45.55%             | -90.89%        |   -0.501 |                  -0.949 |           542 |    107.328 |      17308.8 | -39.63%               | 38.82%                    |                    3 |
| hold_cost          | hold10_cost0.00075_slip0 | baseline_full_dynamic_factor_industry_cap2 |        30490.3 | -93.90%        | -57.58%             | -96.23%        |   -0.598 |                  -0.976 |           793 |    159.693 |      20579.6 | -31.38%               | 37.52%                    |                    4 |
| hold_cost          | hold8_cost0.00075_slip0  | baseline_full_score                        |        20849.9 | -95.83%        | -62.24%             | -96.38%        |   -0.646 |                  -0.994 |          1018 |    201.525 |      32862   | -31.61%               | 39.81%                    |                    3 |

## 输出文件

- `optimization_summary.csv`：完整结果。
- `optimization_ranked_by_calmar.csv`：按 Calmar 排序。
- `optimization_industry_exposure.csv`：每日行业暴露。
- `optimization_experiments.json`：实验参数与回测子目录。
