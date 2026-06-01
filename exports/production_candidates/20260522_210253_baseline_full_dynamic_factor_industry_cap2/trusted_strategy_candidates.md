# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-22`；候选数：Top 5。
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
|      1 | 2026-05-22    | baseline_full_dynamic_factor_industry_cap2 |   600584 | 长电科技   | 半导体        | 半导体            | dynamic_factor_score |      99.4135 | 20.00%             | 20.00%            | 100.00%                 |         72.88  |   75.97 |                99.4135 |                   99.4878 |                  97.5204 |         29.87 |        98.3  |  98.84 |             95.9318 |               96.5517 |             99.0508 |              91.6699 |         60.46 |                 1 |             | 过滤              |                 0.969937 | normal_liquidity          | index_neutral  | 0.0419418 |             0 |
|      2 | 2026-05-22    | baseline_full_dynamic_factor_industry_cap2 |   300408 | 三环集团   | 元器件        | 元器件            | dynamic_factor_score |      99.0718 | 20.00%             | 20.00%            | 100.00%                 |        115     |   76.07 |                99.0718 |                   99.0095 |                  93.3772 |         29.25 |        99.7  |  99.24 |             98.1596 |               90.0426 |             97.8884 |              65.556  |         69.41 |                 0 |             | 可买              |                 0.969937 | normal_liquidity          | index_neutral  | 0.0465869 |             0 |
|      3 | 2026-05-22    | baseline_full_dynamic_factor_industry_cap2 |   300136 | 信维通信   | 元器件        | 元器件            | dynamic_factor_score |      99.0434 | 20.00%             | 20.00%            | 100.00%                 |        118.925 |   75.02 |                99.0434 |                   99.1578 |                  90.6912 |         29.89 |        96.96 |  98.16 |             78.5161 |               71.4839 |             98.1984 |              96.8229 |         70.19 |                 0 |             | 可买              |                 0.969937 | normal_liquidity          | index_neutral  | 0.0606577 |             0 |
|      4 | 2026-05-22    | baseline_full_dynamic_factor_industry_cap2 |   603986 | 兆易创新   | 半导体        | 半导体            | dynamic_factor_score |      99.0274 | 20.00%             | 20.00%            | 100.00%                 |        468.74  |   72.9  |                99.0274 |                   99.2139 |                  96.379  |         29.97 |        97.15 |  98.06 |             92.038  |               90.6044 |             99.7675 |              94.5564 |         69.15 |                 0 |             | 可买              |                 0.969937 | normal_liquidity          | index_neutral  | 0.0457619 |             0 |
|      5 | 2026-05-22    | baseline_full_dynamic_factor_industry_cap2 |   300209 | 有棵树    | 互联网        | 互联网            | dynamic_factor_score |      98.5395 | 20.00%             | 20.00%            | 100.00%                 |         30.9   |   80.8  |                98.5395 |                   98.2893 |                  87.3057 |         28.79 |        99.36 |  99.94 |             86.8074 |               70.6315 |             93.5684 |              69.2755 |         72.46 |                 0 |             | 可买              |                 0.969937 | normal_liquidity          | index_neutral  | 0.0677813 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260522_210253_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260522_210253_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260522_210253_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260522_210253_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
