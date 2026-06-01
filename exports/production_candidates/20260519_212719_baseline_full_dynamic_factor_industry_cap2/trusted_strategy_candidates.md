# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-19`；候选数：Top 5。
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
|      1 | 2026-05-19    | baseline_full_dynamic_factor_industry_cap2 |   000988 | 华工科技   | 专用机械       | 专用机械           | dynamic_factor_score |      99.2868 | 20.00%             | 20.00%            | 100.00%                 |         158.4  |   75.24 |                99.2868 |                   99.7607 |                  91.473  |         29.92 |        98.45 |  95.27 |             92.0481 |               71.4895 |             99.36   |             75.4267  |         68.98 |                 0 |             | 可买              |                 0.999211 | normal_liquidity          | index_neutral  | 0.0411385 |             0 |
|      2 | 2026-05-19    | baseline_full_dynamic_factor_industry_cap2 |   688012 | 中微公司   | 半导体        | 半导体            | dynamic_factor_score |      98.9005 | 20.00%             | 20.00%            | 100.00%                 |         479.51 |   72.44 |                98.9005 |                   99.5041 |                  91.6551 |         29.81 |        97.71 |  97.27 |             92.6881 |               90.6129 |             98.7587 |             49.6509  |         69.03 |                 0 |             | 可买              |                 0.999211 | normal_liquidity          | index_neutral  | 0.0425222 |             0 |
|      3 | 2026-05-19    | baseline_full_dynamic_factor_industry_cap2 |   601991 | 大唐发电   | 火力发电       | 火力发电           | dynamic_factor_score |      98.5844 | 20.00%             | 20.00%            | 100.00%                 |           8.38 |   74.4  |                98.5844 |                   98.3424 |                  88.7576 |         29.33 |        97.48 |  99.86 |             98.6618 |               99.7479 |             98.1575 |              2.32739 |         71.78 |                 0 |             | 可买              |                 0.999211 | normal_liquidity          | index_neutral  | 0.0506998 |             0 |
|      4 | 2026-05-19    | baseline_full_dynamic_factor_industry_cap2 |   300408 | 三环集团   | 元器件        | 元器件            | dynamic_factor_score |      98.3251 | 20.00%             | 20.00%            | 100.00%                 |          96.54 |   75.44 |                98.3251 |                   97.8656 |                  89.5979 |         29.03 |        98.24 |  96.86 |             91.1559 |               68.3476 |             96.2568 |             79.6936  |         70.46 |                 0 |             | 可买              |                 0.999211 | normal_liquidity          | index_neutral  | 0.0349172 |             0 |
|      5 | 2026-05-19    | baseline_full_dynamic_factor_industry_cap2 |   300302 | 同有科技   | IT设备       | IT设备           | dynamic_factor_score |      98.2973 | 20.00%             | 20.00%            | 100.00%                 |          59.4  |   76.36 |                98.2973 |                   97.2586 |                  89.5938 |         28.72 |        99.41 | 100    |             98.3902 |               96.0434 |             97.6532 |             25.6788  |         69.88 |                 0 |             | 可买              |                 0.999211 | normal_liquidity          | index_neutral  | 0.0697544 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260519_212719_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260519_212719_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260519_212719_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260519_212719_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
