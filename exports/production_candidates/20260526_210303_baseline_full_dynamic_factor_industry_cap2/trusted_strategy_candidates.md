# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-26`；候选数：Top 5。
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
|      1 | 2026-05-26    | baseline_full_dynamic_factor_industry_cap2 |   600584 | 长电科技   | 半导体        | 半导体            | dynamic_factor_score |      99.7795 | 20.00%             | 20.00%            | 100.00%                 |          88.19 |   78.11 |                99.7795 |                   99.7975 |                  98.2481 |         29.9  |        99.64 |  99.77 |             96.9585 |               96.8229 |             99.6319 |             95.215   |         53.66 |                 1 |             | 过滤              |                  1.06628 | normal_liquidity          | index_neutral  | 0.0458065 |             0 |
|      2 | 2026-05-26    | baseline_full_dynamic_factor_industry_cap2 |   002156 | 通富微电   | 半导体        | 半导体            | dynamic_factor_score |      99.3773 | 20.00%             | 20.00%            | 100.00%                 |          75.39 |   79.99 |                99.3773 |                   99.322  |                  88.7575 |         29.86 |        99.64 |  98.39 |             97.191  |               93.7427 |             99.5544 |              5.1143  |         72.36 |                 0 |             | 可买              |                  1.06628 | normal_liquidity          | index_neutral  | 0.0446108 |             0 |
|      3 | 2026-05-26    | baseline_full_dynamic_factor_industry_cap2 |   301526 | 国际复材   | 玻璃         | 玻璃             | dynamic_factor_score |      98.6978 | 20.00%             | 20.00%            | 100.00%                 |          22.8  |   75.58 |                98.6978 |                   98.7598 |                  90.8435 |         29.37 |        98.93 |  97.85 |             86.6912 |               75.8233 |             95.7381 |             86.11    |         70.67 |                 0 |             | 可买              |                  1.06628 | normal_liquidity          | index_neutral  | 0.0695316 |             0 |
|      4 | 2026-05-26    | baseline_full_dynamic_factor_industry_cap2 |   000725 | 京东方Ａ   | 元器件        | 元器件            | dynamic_factor_score |      98.6758 | 20.00%             | 20.00%            | 100.00%                 |           5.77 |   76.53 |                98.6758 |                   98.5615 |                  89.9645 |         29.73 |        99.64 |  94.96 |             99.7675 |               99.7675 |             99.5157 |              4.78497 |         71.8  |                 0 |             | 可买              |                  1.06628 | normal_liquidity          | index_neutral  | 0.0381613 |             0 |
|      5 | 2026-05-26    | baseline_full_dynamic_factor_industry_cap2 |   002916 | 深南电路   | 元器件        | 元器件            | dynamic_factor_score |      98.5351 | 20.00%             | 20.00%            | 100.00%                 |         419.5  |   79.56 |                98.5351 |                   98.5481 |                  96.0559 |         29.44 |        98.02 |  95.52 |             95.9706 |               89.5002 |             98.4696 |             94.1302  |         73.16 |                 0 |             | 可买              |                  1.06628 | normal_liquidity          | index_neutral  | 0.03707   |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260526_210303_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260526_210303_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260526_210303_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260526_210303_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
