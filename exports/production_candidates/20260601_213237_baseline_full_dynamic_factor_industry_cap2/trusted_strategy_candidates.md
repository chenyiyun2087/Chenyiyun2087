# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-06-01`；候选数：Top 5。
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
|      1 | 2026-06-01    | baseline_full_dynamic_factor_industry_cap2 |   300433 | 蓝思科技   | 元器件        | 元器件            | dynamic_factor_score |      99.4007 | 20.00%             | 20.00%            | 100.00%                 |          41.83 |   78.31 |                99.4007 |                   99.3773 |                  91.2278 |         29.72 |        98.16 |  99.07 |             86.3425 |               83.2236 |             99.012  |            69.9729   |         73.98 |                 0 |             | 可买              |                 0.915505 | normal_liquidity          | index_neutral  | 0.0419895 |             0 |
|      2 | 2026-06-01    | baseline_full_dynamic_factor_industry_cap2 |   000636 | 风华高科   | 元器件        | 元器件            | dynamic_factor_score |      99.3505 | 20.00%             | 20.00%            | 100.00%                 |          55.05 |   79    |                99.3505 |                   99.5879 |                  88.8865 |         29.47 |        98.97 |  99.9  |             99.1864 |               99.5351 |             98.4115 |             0.639287 |         73.17 |                 0 |             | 可买              |                 0.915505 | normal_liquidity          | index_neutral  | 0.0497442 |             0 |
|      3 | 2026-06-01    | baseline_full_dynamic_factor_industry_cap2 |   600863 | 内蒙华电   | 火力发电       | 火力发电           | dynamic_factor_score |      98.2143 | 20.00%             | 20.00%            | 100.00%                 |           7.92 |   78.33 |                98.2143 |                   99.0335 |                  87.8424 |         27.72 |        99.8  |  99.36 |             99.5738 |               99.5738 |             95.8349 |            16.5633   |         73.18 |                 0 |             | 可买              |                 0.915505 | normal_liquidity          | index_neutral  | 0.0446335 |             0 |
|      4 | 2026-06-01    | baseline_full_dynamic_factor_industry_cap2 |   600011 | 华能国际   | 火力发电       | 火力发电           | dynamic_factor_score |      98.0804 | 20.00%             | 20.00%            | 100.00%                 |           9.76 |   76.99 |                98.0804 |                   98.5224 |                  91.0011 |         27.76 |        99.8  |  97.29 |             99.7094 |               97.9272 |             97.2491 |            47.6947   |         65.56 |                 0 |             | 过滤              |                 0.915505 | normal_liquidity          | index_neutral  | 0.0358532 |             0 |
|      5 | 2026-06-01    | baseline_full_dynamic_factor_industry_cap2 |   002028 | 思源电气   | 电气设备       | 电气设备           | dynamic_factor_score |      95.6862 | 20.00%             | 20.00%            | 100.00%                 |         216    |   64.63 |                95.6862 |                   94.144  |                  88.1219 |         28.72 |        98.92 |  84.58 |             94.8857 |               81.79   |             97.3072 |            39.8683   |         67.49 |                 0 |             | 可买              |                 0.915505 | normal_liquidity          | index_neutral  | 0.0361952 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260601_213237_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260601_213237_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260601_213237_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260601_213237_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
