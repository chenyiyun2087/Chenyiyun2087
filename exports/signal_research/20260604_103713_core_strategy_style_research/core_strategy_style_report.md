# 核心策略风格研究报告

## 窗口收益风险

| strategy                                         | window   | window_start   | window_end   |   periods | total_return   |   avg_cycle_return | max_drawdown   | annualized_volatility   | win_rate   | avg_gross_exposure   |
|:-------------------------------------------------|:---------|:---------------|:-------------|----------:|:---------------|-------------------:|:---------------|:------------------------|:-----------|:---------------------|
| tiered_liquidity_then_bs_v2                      | 3m       | 2026-03-02     | 2026-05-19   |        53 | 257.27%        |          0.0277918 | -66.74%        | 42.41%                  | 60.38%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 6m       | 2026-03-02     | 2026-05-19   |        53 | 257.27%        |          0.0277918 | -66.74%        | 42.41%                  | 60.38%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 1y       | 2026-03-02     | 2026-05-19   |        53 | 257.27%        |          0.0277918 | -66.74%        | 42.41%                  | 60.38%     | 100.00%              |
| tiered_liquidity_then_bs_v2                      | 3y       | 2026-03-02     | 2026-05-19   |        53 | 257.27%        |          0.0277918 | -66.74%        | 42.41%                  | 60.38%     | 100.00%              |
| baseline_full_liquidity_detail_market_gate       | 3m       | 2026-03-02     | 2026-05-19   |        53 | 846.36%        |          0.0465003 | -32.79%        | 41.11%                  | 62.26%     | 97.74%               |
| baseline_full_liquidity_detail_market_gate       | 6m       | 2026-03-02     | 2026-05-19   |        53 | 846.36%        |          0.0465003 | -32.79%        | 41.11%                  | 62.26%     | 97.74%               |
| baseline_full_liquidity_detail_market_gate       | 1y       | 2026-03-02     | 2026-05-19   |        53 | 846.36%        |          0.0465003 | -32.79%        | 41.11%                  | 62.26%     | 97.74%               |
| baseline_full_liquidity_detail_market_gate       | 3y       | 2026-03-02     | 2026-05-19   |        53 | 846.36%        |          0.0465003 | -32.79%        | 41.11%                  | 62.26%     | 97.74%               |
| baseline_full_liquidity                          | 3m       | 2026-03-02     | 2026-05-19   |        53 | 1243.78%       |          0.0524904 | -10.21%        | 34.75%                  | 75.47%     | 100.00%              |
| baseline_full_liquidity                          | 6m       | 2026-03-02     | 2026-05-19   |        53 | 1243.78%       |          0.0524904 | -10.21%        | 34.75%                  | 75.47%     | 100.00%              |
| baseline_full_liquidity                          | 1y       | 2026-03-02     | 2026-05-19   |        53 | 1243.78%       |          0.0524904 | -10.21%        | 34.75%                  | 75.47%     | 100.00%              |
| baseline_full_liquidity                          | 3y       | 2026-03-02     | 2026-05-19   |        53 | 1243.78%       |          0.0524904 | -10.21%        | 34.75%                  | 75.47%     | 100.00%              |
| baseline_full_liquidity_detail_vol_position      | 3m       | 2026-03-02     | 2026-05-19   |        53 | 876.92%        |          0.0470596 | -31.90%        | 40.74%                  | 64.15%     | 21.23%               |
| baseline_full_liquidity_detail_vol_position      | 6m       | 2026-03-02     | 2026-05-19   |        53 | 876.92%        |          0.0470596 | -31.90%        | 40.74%                  | 64.15%     | 21.23%               |
| baseline_full_liquidity_detail_vol_position      | 1y       | 2026-03-02     | 2026-05-19   |        53 | 876.92%        |          0.0470596 | -31.90%        | 40.74%                  | 64.15%     | 21.23%               |
| baseline_full_liquidity_detail_vol_position      | 3y       | 2026-03-02     | 2026-05-19   |        53 | 876.92%        |          0.0470596 | -31.90%        | 40.74%                  | 64.15%     | 21.23%               |
| baseline_full_liquidity_detail_hist_mdd_position | 3m       | 2026-03-02     | 2026-05-19   |        53 | 786.47%        |          0.0453515 | -34.97%        | 41.84%                  | 66.04%     | 82.32%               |
| baseline_full_liquidity_detail_hist_mdd_position | 6m       | 2026-03-02     | 2026-05-19   |        53 | 786.47%        |          0.0453515 | -34.97%        | 41.84%                  | 66.04%     | 82.32%               |
| baseline_full_liquidity_detail_hist_mdd_position | 1y       | 2026-03-02     | 2026-05-19   |        53 | 786.47%        |          0.0453515 | -34.97%        | 41.84%                  | 66.04%     | 82.32%               |
| baseline_full_liquidity_detail_hist_mdd_position | 3y       | 2026-03-02     | 2026-05-19   |        53 | 786.47%        |          0.0453515 | -34.97%        | 41.84%                  | 66.04%     | 82.32%               |
| adaptive_market_style_event                      | 3m       | 2026-03-02     | 2026-06-02   |        53 | 337.02%        |          0.0289935 | -5.19%         | 20.21%                  | 73.58%     | 55.47%               |
| adaptive_market_style_event                      | 6m       | 2026-03-02     | 2026-06-02   |        53 | 337.02%        |          0.0289935 | -5.19%         | 20.21%                  | 73.58%     | 55.47%               |
| adaptive_market_style_event                      | 1y       | 2026-03-02     | 2026-06-02   |        53 | 337.02%        |          0.0289935 | -5.19%         | 20.21%                  | 73.58%     | 55.47%               |
| adaptive_market_style_event                      | 3y       | 2026-03-02     | 2026-06-02   |        53 | 337.02%        |          0.0289935 | -5.19%         | 20.21%                  | 73.58%     | 55.47%               |

## 网格搜索 Top10

|   amount_threshold |   vol_threshold |   industry_concentration_threshold |   periods |   total_return |   avg_cycle_return |   max_drawdown |   annualized_volatility |   win_rate |   avg_gross_exposure |
|-------------------:|----------------:|-----------------------------------:|----------:|---------------:|-------------------:|---------------:|------------------------:|-----------:|---------------------:|
|                0.8 |            0.04 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                0.8 |            0.05 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                0.8 |            0.06 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                1   |            0.04 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                1   |            0.05 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                1   |            0.06 |                                0.4 |        53 |        6.25535 |          0.0392709 |     -0.0384842 |                0.247183 |   0.735849 |             0.735849 |
|                1.2 |            0.04 |                                0.4 |        53 |        3.85985 |          0.0314666 |     -0.0746821 |                0.248933 |   0.679245 |             0.673585 |
|                1.2 |            0.05 |                                0.4 |        53 |        3.85985 |          0.0314666 |     -0.0746821 |                0.248933 |   0.679245 |             0.673585 |
|                1.2 |            0.06 |                                0.4 |        53 |        3.85985 |          0.0314666 |     -0.0746821 |                0.248933 |   0.679245 |             0.673585 |
|                0.8 |            0.04 |                                0.6 |        53 |        6.25777 |          0.0395895 |     -0.182302  |                0.276858 |   0.698113 |             0.820755 |

## 输出文件

- cycles_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_cycles.csv`
- group_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_group_summary.csv`
- window_summary_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_window_summary.csv`
- grid_search_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_grid_search.csv`
- adaptive_decisions_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_adaptive_decisions.csv`
- market_environment_csv: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_market_environment.csv`
- json: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_report.json`
- markdown: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_103713_core_strategy_style_research/core_strategy_style_report.md`
