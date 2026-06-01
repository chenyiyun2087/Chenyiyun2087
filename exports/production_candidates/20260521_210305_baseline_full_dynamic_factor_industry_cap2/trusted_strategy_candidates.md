# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-21`；候选数：Top 5。
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
|      1 | 2026-05-21    | baseline_full_dynamic_factor_industry_cap2 |   002371 | 北方华创   | 半导体        | 半导体            | dynamic_factor_score |      99.3602 | 20.00%             | 20.00%            | 100.00%                 |         662.99 |   78.91 |                99.3602 |                   99.4224 |                  90.4549 |         29.84 |        98.2  |  97.13 |             91.6134 |               88.7081 |             99.3221 |              41.41   |         73.42 |                 0 |             | 可买              |                  1.17596 | normal_liquidity          | index_neutral  | 0.0351948 |             0 |
|      2 | 2026-05-21    | baseline_full_dynamic_factor_industry_cap2 |   002407 | 多氟多    | 化工原料       | 化工原料           | dynamic_factor_score |      99.0772 | 20.00%             | 20.00%            | 100.00%                 |          40.81 |   76.71 |                99.0772 |                   99.1134 |                  89.9942 |         29.7  |        98.39 |  96.01 |             92.0782 |               87.4298 |             99.3027 |              39.6862 |         70.83 |                 0 |             | 可买              |                  1.17596 | normal_liquidity          | index_neutral  | 0.0499454 |             0 |
|      3 | 2026-05-21    | baseline_full_dynamic_factor_industry_cap2 |   300408 | 三环集团   | 元器件        | 元器件            | dynamic_factor_score |      98.4797 | 20.00%             | 20.00%            | 100.00%                 |          98.47 |   72.11 |                98.4797 |                   98.3703 |                  90.021  |         29.15 |        98.31 |  98.66 |             85.4348 |               79.0432 |             98.2181 |              74.7821 |         68.66 |                 0 |             | 可买              |                  1.17596 | normal_liquidity          | index_neutral  | 0.0334832 |             0 |
|      4 | 2026-05-21    | baseline_full_dynamic_factor_industry_cap2 |   688017 | 绿的谐波   | 机械基件       | 机械基件           | dynamic_factor_score |      98.3857 | 20.00%             | 20.00%            | 100.00%                 |         335    |   72.5  |                98.3857 |                   98.2236 |                  90.4575 |         28.99 |        98.3  |  98.88 |             93.9764 |               94.7124 |             97.6758 |              41.5069 |         57.72 |                 1 |             | 过滤              |                  1.17596 | normal_liquidity          | index_neutral  | 0.0525882 |             0 |
|      5 | 2026-05-21    | baseline_full_dynamic_factor_industry_cap2 |   300323 | 华灿光电   | 半导体        | 半导体            | dynamic_factor_score |      98.1069 | 20.00%             | 20.00%            | 100.00%                 |          18.73 |   76.31 |                98.1069 |                   97.5763 |                  90.4349 |         28.36 |        99.57 |  99.4  |             98.3537 |               95.3903 |             97.5015 |              40.1704 |         70.27 |                 0 |             | 可买              |                  1.17596 | normal_liquidity          | index_neutral  | 0.0458259 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260521_210305_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260521_210305_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260521_210305_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260521_210305_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
