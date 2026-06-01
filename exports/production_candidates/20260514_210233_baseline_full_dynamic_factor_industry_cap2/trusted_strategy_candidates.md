# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-14`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name   | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:-------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-05-14    | baseline_full_dynamic_factor_industry_cap2 |   688008 | 澜起科技   | 半导体        | 半导体            | dynamic_factor_score |      99.402  | 20.00%             | 20.00%            | 100.00%                 |         265.08 |   72.52 |                99.402  |                   99.8714 |                  93.8612 |         29.95 |        97.6  |  98.88 |             93.7573 |               91.8961 |             99.4959 |              64.6762 |         69.03 |                 0 |             | 可买              |                  1.20527 | high_liquidity            | index_strong   | 0.0581649 |             0 |
|      2 | 2026-05-14    | baseline_full_dynamic_factor_industry_cap2 |   002008 | 大族激光   | 专用机械       | 专用机械           | dynamic_factor_score |      99.0809 | 20.00%             | 20.00%            | 100.00%                 |         149.23 |   72.74 |                99.0809 |                   99.1851 |                  91.9856 |         29.66 |        96.24 |  99.71 |             86.5646 |               82.3963 |             98.9143 |              79.2943 |         58.39 |                 0 |             | 过滤              |                  1.20527 | high_liquidity            | index_strong   | 0.0496328 |             0 |
|      3 | 2026-05-14    | baseline_full_dynamic_factor_industry_cap2 |   300782 | 卓胜微    | 半导体        | 半导体            | dynamic_factor_score |      98.7803 | 20.00%             | 20.00%            | 100.00%                 |         140.88 |   76.37 |                98.7803 |                   98.4306 |                  93.7923 |         29.3  |        99.47 |  94.49 |             96.2195 |               80.6126 |             97.3827 |              87.8247 |         69.89 |                 0 |             | 可买              |                  1.20527 | high_liquidity            | index_strong   | 0.0557605 |             0 |
|      4 | 2026-05-14    | baseline_full_dynamic_factor_industry_cap2 |   002015 | 协鑫能科   | 新型电力       | 新型电力           | dynamic_factor_score |      98.3796 | 20.00%             | 20.00%            | 100.00%                 |          23.5  |   77.28 |                98.3796 |                   98.136  |                  87.5399 |         29.38 |        95.7  |  93.12 |             94.8236 |               68.1272 |             97.7705 |              45.1725 |         60.85 |                 0 |             | 过滤              |                  1.20527 | high_liquidity            | index_strong   | 0.0447511 |             0 |
|      5 | 2026-05-14    | baseline_full_dynamic_factor_industry_cap2 |   300308 | 中际旭创   | 通信设备       | 通信设备           | dynamic_factor_score |      98.3463 | 20.00%             | 20.00%            | 100.00%                 |        1078    |   73.78 |                98.3463 |                   99.039  |                  84.8924 |         30    |        97.63 |  96.28 |             68.6506 |               46.7235 |             99.5541 |              92.2063 |         70.41 |                 0 |             | 可买              |                  1.20527 | high_liquidity            | index_strong   | 0.0293436 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260514_210233_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260514_210233_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260514_210233_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260514_210233_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
