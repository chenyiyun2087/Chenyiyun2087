# 可信策略生产候选名单

## 口径

- 策略：`动态因子均衡策略（单行业最多2只）`，排序字段：`dynamic_factor_score`。
- 策略ID：`baseline_full_dynamic_factor_industry_cap2`。
- 信号日：`2026-06-02`；候选数：Top 5。
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
|      1 | 2026-06-02    | baseline_full_dynamic_factor_industry_cap2 |   002436 | 兴森科技   | 元器件        | 元器件            | dynamic_factor_score |      99.2514 | 20.00%             | 20.00%            | 100.00%                 |        42.22   |   76.32 |                99.2514 |                   99.2129 |                  91.2835 |         29.67 |        99.5  |  98.49 |             93.7997 |               89.9244 |             97.9655 |            47.8008   |         70.94 |                 0 |             | 可买              |                 0.888244 | normal_liquidity          | index_neutral  | 0.0453666 |             0 |
|      2 | 2026-06-02    | baseline_full_dynamic_factor_industry_cap2 |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | dynamic_factor_score |      99.1785 | 20.00%             | 20.00%            | 100.00%                 |        58.3368 |   78.29 |                99.1785 |                   99.3564 |                  91.4407 |         29.32 |        99.32 |  99.07 |             97.3455 |               95.8148 |             96.5317 |            40.2635   |         62.62 |                 0 |             | 过滤              |                 0.888244 | normal_liquidity          | index_neutral  | 0.0621978 |             0 |
|      3 | 2026-06-02    | baseline_full_dynamic_factor_industry_cap2 |   300502 | 新易盛    | 通信设备       | 通信设备           | dynamic_factor_score |      99.1233 | 20.00%             | 20.00%            | 100.00%                 |       747      |   76.04 |                99.1233 |                   98.9222 |                  90.0409 |         29.99 |        98.72 |  97.62 |             84.5379 |               69.4245 |             99.845  |            77.5625   |         71.65 |                 0 |             | 可买              |                 0.888244 | normal_liquidity          | index_neutral  | 0.0462253 |             0 |
|      4 | 2026-06-02    | baseline_full_dynamic_factor_industry_cap2 |   000636 | 风华高科   | 元器件        | 元器件            | dynamic_factor_score |      99.0904 | 20.00%             | 20.00%            | 100.00%                 |        59.91   |   74.34 |                99.0904 |                   99.307  |                  88.562  |         29.55 |        98.99 |  99.92 |             97.5005 |               99.0699 |             98.2755 |             0.600659 |         71.35 |                 0 |             | 可买              |                 0.888244 | normal_liquidity          | index_neutral  | 0.0494569 |             0 |
|      5 | 2026-06-02    | baseline_full_dynamic_factor_industry_cap2 |   601991 | 大唐发电   | 火力发电       | 火力发电           | dynamic_factor_score |      98.9123 | 20.00%             | 20.00%            | 100.00%                 |         9.18   |   73.38 |                98.9123 |                   99.1164 |                  85.4267 |         29.73 |        99.42 |  99.86 |             89.4207 |               87.0955 |             97.4617 |             2.1895   |         71.56 |                 0 |             | 可买              |                 0.888244 | normal_liquidity          | index_neutral  | 0.0660447 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260602_210232_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260602_210232_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260602_210232_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260602_210232_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
