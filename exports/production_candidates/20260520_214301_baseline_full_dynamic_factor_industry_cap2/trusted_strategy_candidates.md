# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-20`；候选数：Top 5。
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
|      1 | 2026-05-20    | baseline_full_dynamic_factor_industry_cap2 |   603986 | 兆易创新   | 半导体        | 半导体            | dynamic_factor_score |      99.3559 | 20.00%             | 20.00%            | 100.00%                 |         445.02 |   73.97 |                99.3559 |                   99.7679 |                  95.2483 |         29.95 |        99.49 |  97.81 |             90.2907 |               87.4806 |             99.4767 |              92.1318 |         62.21 |                 0 |             | 过滤              |                  1.01328 | normal_liquidity          | index_neutral  | 0.0425712 |             0 |
|      2 | 2026-05-20    | baseline_full_dynamic_factor_industry_cap2 |   600584 | 长电科技   | 半导体        | 半导体            | dynamic_factor_score |      99.2083 | 20.00%             | 20.00%            | 100.00%                 |          66.22 |   73.91 |                99.2083 |                   99.5771 |                  97.2256 |         29.84 |        99.49 |  97.33 |             96.7054 |               95.7171 |             99.0698 |              88.7984 |         59.02 |                 1 |             | 过滤              |                  1.01328 | normal_liquidity          | index_neutral  | 0.0398101 |             0 |
|      3 | 2026-05-20    | baseline_full_dynamic_factor_industry_cap2 |   000988 | 华工科技   | 专用机械       | 专用机械           | dynamic_factor_score |      98.8999 | 20.00%             | 20.00%            | 100.00%                 |         163.42 |   73.53 |                98.8999 |                   99.6529 |                  92.4001 |         29.92 |        96.65 |  95.25 |             91.124  |               79.3992 |             99.4186 |              74.593  |         67.85 |                 0 |             | 可买              |                  1.01328 | normal_liquidity          | index_neutral  | 0.0409804 |             0 |
|      4 | 2026-05-20    | baseline_full_dynamic_factor_industry_cap2 |   002281 | 光迅科技   | 通信设备       | 通信设备           | dynamic_factor_score |      98.5112 | 20.00%             | 20.00%            | 100.00%                 |         236.4  |   67.87 |                98.5112 |                   99.4837 |                  94.3265 |         29.92 |        96.72 |  99.73 |             86.3953 |               83.7791 |             99.186  |              97.093  |         66.81 |                 0 |             | 可买              |                  1.01328 | normal_liquidity          | index_neutral  | 0.0442514 |             0 |
|      5 | 2026-05-20    | baseline_full_dynamic_factor_industry_cap2 |   300274 | 阳光电源   | 电气设备       | 电气设备           | dynamic_factor_score |      98.1534 | 20.00%             | 20.00%            | 100.00%                 |         167.2  |   69.29 |                98.1534 |                   99.4546 |                  92.2484 |         29.9  |        97.93 |  90.16 |             91.2403 |               91.0465 |             99.2248 |              55.9302 |         67.59 |                 0 |             | 可买              |                  1.01328 | normal_liquidity          | index_neutral  | 0.0403301 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260520_214301_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260520_214301_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260520_214301_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260520_214301_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
