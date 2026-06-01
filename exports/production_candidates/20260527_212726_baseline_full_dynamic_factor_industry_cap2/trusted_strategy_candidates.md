# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-27`；候选数：Top 5。
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
|      1 | 2026-05-27    | baseline_full_dynamic_factor_industry_cap2 |   600183 | 生益科技   | 元器件        | 元器件            | dynamic_factor_score |      99.4626 | 20.00%             | 20.00%            | 100.00%                 |         131.76 |   77.24 |                99.4626 |                   99.5047 |                  96.1351 |         29.72 |        98.99 |  99.4  |             97.3649 |               91.6295 |             99.2637 |            84.0147   |         70.69 |                 0 |             | 可买              |                  1.05152 | normal_liquidity          | index_neutral  | 0.0447277 |             0 |
|      2 | 2026-05-27    | baseline_full_dynamic_factor_industry_cap2 |   002185 | 华天科技   | 半导体        | 半导体            | dynamic_factor_score |      99.167  | 20.00%             | 20.00%            | 100.00%                 |          20.15 |   73.71 |                99.167  |                   99.18   |                  92.7075 |         29.59 |        99.85 |  98.66 |             99.8644 |               98.2562 |             99.7869 |            35.7489   |         58.83 |                 1 |             | 过滤              |                  1.05152 | normal_liquidity          | index_neutral  | 0.0403614 |             0 |
|      3 | 2026-05-27    | baseline_full_dynamic_factor_industry_cap2 |   603601 | 再升科技   | 玻璃         | 玻璃             | dynamic_factor_score |      98.9793 | 20.00%             | 20.00%            | 100.00%                 |          21.39 |   79.09 |                98.9793 |                   98.9647 |                  91.7635 |         29.44 |        99.05 |  97.58 |             86.4367 |               79.1513 |             95.9891 |            89.5175   |         62.8  |                 0 |             | 过滤              |                  1.05152 | normal_liquidity          | index_neutral  | 0.0454382 |             0 |
|      4 | 2026-05-27    | baseline_full_dynamic_factor_industry_cap2 |   300274 | 阳光电源   | 电气设备       | 电气设备           | dynamic_factor_score |      98.8895 | 20.00%             | 20.00%            | 100.00%                 |         184.09 |   77.12 |                98.8895 |                   98.5536 |                  90.7955 |         29.91 |        98.33 |  95.37 |             89.8469 |               85.3904 |             99.7481 |            51.7535   |         72.52 |                 0 |             | 可买              |                  1.05152 | normal_liquidity          | index_neutral  | 0.0420845 |             0 |
|      5 | 2026-05-27    | baseline_full_dynamic_factor_industry_cap2 |   000636 | 风华高科   | 元器件        | 元器件            | dynamic_factor_score |      98.7551 | 20.00%             | 20.00%            | 100.00%                 |          44.22 |   79.74 |                98.7551 |                   98.9873 |                  87.5351 |         29.17 |        98.53 |  99.71 |             98.8374 |               93.1021 |             98.9537 |             0.658787 |         73.35 |                 0 |             | 可买              |                  1.05152 | normal_liquidity          | index_neutral  | 0.0462963 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260527_212726_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260527_212726_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260527_212726_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260527_212726_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
