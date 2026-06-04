# 核心策略风格研究报告

## 窗口收益风险

| strategy                                         | window   | window_start   | window_end   |   periods | total_return   |   avg_cycle_return | max_drawdown   | annualized_volatility   | win_rate   | avg_gross_exposure   |
|:-------------------------------------------------|:---------|:---------------|:-------------|----------:|:---------------|-------------------:|:---------------|:------------------------|:-----------|:---------------------|
| tiered_liquidity_then_bs_v2                      | 3m       | 2026-03-02     | 2026-05-19   |        53 | 257.27%        |        0.0277918   | -66.74%        | 42.41%                  | 60.38%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 6m       | 2025-12-02     | 2026-05-19   |       109 | 4099.41%       |        0.0389988   | -73.39%        | 46.14%                  | 66.97%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 1y       | 2025-06-03     | 2026-05-19   |       233 | 12369.17%      |        0.0248635   | -73.39%        | 44.97%                  | 61.37%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 3y       | 2023-06-02     | 2026-05-19   |       715 | -99.93%        |       -0.00534733  | -100.00%       | 50.05%                  | 42.24%     | 100.00%              |
| baseline_full_liquidity_detail_market_gate       | 3m       | 2026-03-02     | 2026-05-19   |        53 | 846.36%        |        0.0465003   | -32.79%        | 41.11%                  | 62.26%     | 97.74%               |
| baseline_full_liquidity_detail_market_gate       | 6m       | 2025-12-02     | 2026-05-19   |       109 | 8695.47%       |        0.0468779   | -75.99%        | 51.63%                  | 61.47%     | 96.70%               |
| baseline_full_liquidity_detail_market_gate       | 1y       | 2025-06-03     | 2026-05-19   |       233 | 33898.18%      |        0.0293472   | -79.58%        | 46.21%                  | 60.09%     | 97.77%               |
| baseline_full_liquidity_detail_market_gate       | 3y       | 2023-06-02     | 2026-05-19   |       715 | -88.17%        |        0.000585521 | -99.97%        | 43.15%                  | 42.80%     | 97.48%               |
| baseline_full_liquidity                          | 3m       | 2026-03-02     | 2026-05-19   |        53 | 1243.78%       |        0.0524904   | -10.21%        | 34.75%                  | 75.47%     | 100.00%              |
| baseline_full_liquidity                          | 6m       | 2025-12-02     | 2026-05-19   |       109 | 234.47%        |        0.0135603   | -86.07%        | 35.54%                  | 55.05%     | 100.00%              |
| baseline_full_liquidity                          | 1y       | 2025-06-03     | 2026-05-19   |       233 | 528215.10%     |        0.0417796   | -86.07%        | 48.52%                  | 64.81%     | 100.00%              |
| baseline_full_liquidity                          | 3y       | 2023-06-02     | 2026-05-19   |       715 | 53.16%         |        0.00404516  | -99.99%        | 42.75%                  | 46.85%     | 100.00%              |
| baseline_full_liquidity_detail_vol_position      | 3m       | 2026-03-02     | 2026-05-19   |        53 | 876.92%        |        0.0470596   | -31.90%        | 40.74%                  | 64.15%     | 21.23%               |
| baseline_full_liquidity_detail_vol_position      | 6m       | 2025-12-02     | 2026-05-19   |       109 | 9622.42%       |        0.0476193   | -72.07%        | 50.68%                  | 61.47%     | 21.31%               |
| baseline_full_liquidity_detail_vol_position      | 1y       | 2025-06-03     | 2026-05-19   |       233 | 24685.62%      |        0.0276662   | -79.98%        | 44.66%                  | 59.66%     | 26.07%               |
| baseline_full_liquidity_detail_vol_position      | 3y       | 2023-06-02     | 2026-05-19   |       715 | -3.53%         |        0.00304792  | -99.73%        | 40.28%                  | 45.17%     | 30.46%               |
| baseline_full_liquidity_detail_hist_mdd_position | 3m       | 2026-03-02     | 2026-05-19   |        53 | 786.47%        |        0.0453515   | -34.97%        | 41.84%                  | 66.04%     | 82.32%               |
| baseline_full_liquidity_detail_hist_mdd_position | 6m       | 2025-12-02     | 2026-05-19   |       109 | 8807.48%       |        0.0475342   | -78.42%        | 54.30%                  | 62.39%     | 84.39%               |
| baseline_full_liquidity_detail_hist_mdd_position | 1y       | 2025-06-03     | 2026-05-19   |       233 | 31914.62%      |        0.0293727   | -78.42%        | 47.83%                  | 60.52%     | 83.48%               |
| baseline_full_liquidity_detail_hist_mdd_position | 3y       | 2023-06-02     | 2026-05-19   |       715 | -95.58%        |       -0.000469207 | -99.99%        | 44.90%                  | 42.10%     | 78.12%               |
| adaptive_market_style_event                      | 3m       | 2026-03-02     | 2026-06-02   |        53 | 337.02%        |        0.0289935   | -5.19%         | 20.21%                  | 73.58%     | 55.47%               |
| adaptive_market_style_event                      | 6m       | 2025-12-02     | 2026-06-02   |       109 | 319.76%        |        0.0147624   | -61.28%        | 27.89%                  | 62.39%     | 61.74%               |
| adaptive_market_style_event                      | 1y       | 2025-06-03     | 2026-06-02   |       233 | 3695.38%       |        0.0172737   | -61.28%        | 28.24%                  | 64.38%     | 61.72%               |
| adaptive_market_style_event                      | 3y       | 2023-06-02     | 2026-06-02   |       715 | -73.94%        |       -0.000739552 | -99.56%        | 24.10%                  | 46.57%     | 56.06%               |

## 网格搜索 Top10

|   amount_threshold |   vol_threshold |   industry_concentration_threshold |   periods |   total_return |   avg_cycle_return |   max_drawdown |   annualized_volatility |   win_rate |   avg_gross_exposure |
|-------------------:|----------------:|-----------------------------------:|----------:|---------------:|-------------------:|---------------:|------------------------:|-----------:|---------------------:|
|                1.2 |            0.06 |                                0.4 |       813 |        3.70895 |         0.00361536 |      -0.991663 |                0.298958 |   0.462485 |             0.645633 |
|                1.2 |            0.05 |                                0.4 |       813 |        3.69183 |         0.00360999 |      -0.991694 |                0.29888  |   0.462485 |             0.645141 |
|                1.2 |            0.04 |                                0.4 |       813 |        3.66245 |         0.00359189 |      -0.991746 |                0.297955 |   0.462485 |             0.643542 |
|                1   |            0.04 |                                0.4 |       813 |        5.64646 |         0.00418662 |      -0.992842 |                0.312584 |   0.466175 |             0.671218 |
|                1   |            0.05 |                                0.4 |       813 |        5.36082 |         0.00413652 |      -0.99315  |                0.312904 |   0.466175 |             0.672202 |
|                1   |            0.06 |                                0.4 |       813 |        5.36082 |         0.00413652 |      -0.99315  |                0.312904 |   0.466175 |             0.672202 |
|                1.2 |            0.05 |                                0.6 |       813 |        0.43771 |         0.00255442 |      -0.995351 |                0.332213 |   0.453875 |             0.757196 |
|                1.2 |            0.06 |                                0.6 |       813 |        0.39398 |         0.0025145  |      -0.995493 |                0.332055 |   0.453875 |             0.75818  |
|                0.8 |            0.04 |                                0.4 |       813 |        3.82385 |         0.00390882 |      -0.995568 |                0.321981 |   0.460025 |             0.684502 |
|                0.8 |            0.05 |                                0.4 |       813 |        3.61654 |         0.00385872 |      -0.995759 |                0.32229  |   0.460025 |             0.685486 |

## 输出文件

- cycles_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_cycles.csv`
- group_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_group_summary.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_window_summary.csv`
- grid_search_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_grid_search.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_adaptive_decisions.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_market_environment.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_105452_core_strategy_style_research/core_strategy_style_report.md`
