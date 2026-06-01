# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-18`；候选数：Top 5。
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
|      1 | 2026-05-18    | baseline_full_dynamic_factor_industry_cap2 |   603986 | 兆易创新   | 半导体        | 半导体            | dynamic_factor_score |      99.5476 | 20.00%             | 20.00%            | 100.00%                 |         400    |   76.75 |                99.5476 |                   99.8649 |                  94.8066 |         29.94 |        98.59 |  96.47 |             92.9457 |               79.6705 |             99.5349 |              94.1667 |         70.93 |                 0 |             | 可买              |                  1.01016 | normal_liquidity          | index_neutral  | 0.0390335 |             0 |
|      2 | 2026-05-18    | baseline_full_dynamic_factor_industry_cap2 |   300433 | 蓝思科技   | 元器件        | 元器件            | dynamic_factor_score |      98.7083 | 20.00%             | 20.00%            | 100.00%                 |          38.7  |   77.64 |                98.7083 |                   98.7634 |                  92.2206 |         29.43 |        99.64 |  92.02 |             98.469  |               95.814  |             97.3256 |              43.1589 |         70.31 |                 0 |             | 可买              |                  1.01016 | normal_liquidity          | index_neutral  | 0.0544036 |             0 |
|      3 | 2026-05-18    | baseline_full_dynamic_factor_industry_cap2 |   688017 | 绿的谐波   | 机械基件       | 机械基件           | dynamic_factor_score |      98.04   | 20.00%             | 20.00%            | 100.00%                 |         322.88 |   72.89 |                98.04   |                   97.2628 |                  91.0051 |         28.74 |        98.06 |  97.98 |             95.9302 |               94.2829 |             95.5039 |              50.3101 |         57.91 |                 1 |             | 过滤              |                  1.01016 | normal_liquidity          | index_neutral  | 0.0526699 |             0 |
|      4 | 2026-05-18    | baseline_full_dynamic_factor_industry_cap2 |   002484 | 江海股份   | 元器件        | 元器件            | dynamic_factor_score |      97.9713 | 20.00%             | 20.00%            | 100.00%                 |          53.35 |   75.01 |                97.9713 |                   96.6729 |                  91.9578 |         28.44 |        99.64 |  98.02 |             93.2171 |               82.3256 |             94.6318 |              88.5078 |         70.94 |                 0 |             | 可买              |                  1.01016 | normal_liquidity          | index_neutral  | 0.0454428 |             0 |
|      5 | 2026-05-18    | baseline_full_dynamic_factor_industry_cap2 |   300666 | 江丰电子   | 半导体        | 半导体            | dynamic_factor_score |      97.4724 | 20.00%             | 20.00%            | 100.00%                 |         201.26 |   74.48 |                97.4724 |                   97.0377 |                  87.1733 |         28.84 |        97.44 |  91.8  |             87.0349 |               73.1783 |             95.0581 |              60.7752 |         68.98 |                 0 |             | 可买              |                  1.01016 | normal_liquidity          | index_neutral  | 0.0390813 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260518_212644_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260518_212644_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260518_212644_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260518_212644_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
