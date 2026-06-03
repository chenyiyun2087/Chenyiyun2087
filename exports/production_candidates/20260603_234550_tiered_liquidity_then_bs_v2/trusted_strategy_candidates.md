# 可信策略生产候选名单

## 口径

- 策略：`流动性分层B点进攻策略`，排序字段：`bs_score_v2`。
- 策略ID：`tiered_liquidity_then_bs_v2`。
- 信号日：`2026-06-02`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                    |   symbol | name     | industry   | industry_key   | sort_col    |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:----------------------------|---------:|:---------|:-----------|:---------------|:------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-06-02    | tiered_liquidity_then_bs_v2 |   002484 | 江海股份 | 元器件     | 元器件         | bs_score_v2 |        74.53 | 20.00%             | 20.00%            | 100.00%                 |          83.46 |   80.87 |                98.7465 |                   99.245  |                  86.9445 |         29.07 |        98.51 |  99.84 |             95.3691 |               94.0903 |             96.4154 |             5.3478   |         74.53 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | 0.0504648 |             0 |
|      2 | 2026-06-02    | tiered_liquidity_then_bs_v2 |   300502 | 新易盛   | 通信设备   | 通信设备       | bs_score_v2 |        71.65 | 20.00%             | 20.00%            | 100.00%                 |         747    |   76.04 |                99.1233 |                   98.9222 |                  90.0409 |         29.99 |        98.72 |  97.62 |             84.5379 |               69.4245 |             99.845  |            77.5625   |         71.65 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | 0.0462253 |             0 |
|      3 | 2026-06-02    | tiered_liquidity_then_bs_v2 |   601991 | 大唐发电 | 火力发电   | 火力发电       | bs_score_v2 |        71.56 | 20.00%             | 20.00%            | 100.00%                 |           9.18 |   73.38 |                98.9123 |                   99.1164 |                  85.4267 |         29.73 |        99.42 |  99.86 |             89.4207 |               87.0955 |             97.4617 |             2.1895   |         71.56 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | 0.0660447 |             0 |
|      4 | 2026-06-02    | tiered_liquidity_then_bs_v2 |   603773 | 沃格光电 | 元器件     | 元器件         | bs_score_v2 |        71.4  | 20.00%             | 20.00%            | 100.00%                 |         122    |   80.04 |                98.5233 |                   99.0788 |                  86.0047 |         28.69 |        99.84 |  99.48 |             96.5898 |               92.8502 |             96.3573 |             0.523154 |         71.4  |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | 0.0678939 |             0 |
|      5 | 2026-06-02    | tiered_liquidity_then_bs_v2 |   000636 | 风华高科 | 元器件     | 元器件         | bs_score_v2 |        71.35 | 20.00%             | 20.00%            | 100.00%                 |          59.91 |   74.34 |                99.0904 |                   99.307  |                  88.562  |         29.55 |        98.99 |  99.92 |             97.5005 |               99.0699 |             98.2755 |             0.600659 |         71.35 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | 0.0494569 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_234550_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_234550_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_234550_tiered_liquidity_then_bs_v2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_234550_tiered_liquidity_then_bs_v2/trusted_strategy_market_environment.csv`
