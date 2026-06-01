# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-29`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name     | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:---------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-05-29    | baseline_full_dynamic_factor_industry_cap2 |   600183 | 生益科技 | 元器件     | 元器件         | dynamic_factor_score |      99.3854 | 20.00%             | 20.00%            | 100.00%                 |         140.62 |   75.68 |                99.3854 |                   99.3987 |                  96.0683 |         29.77 |        98.43 |  99.5  |             95.2326 |               95.7946 |             99.4186 |            80.4651   |         71.27 |                 0 |             | 可买            |                  1.06041 | normal_liquidity          | index_neutral  | 0.0437867 |             0 |
|      2 | 2026-05-29    | baseline_full_dynamic_factor_industry_cap2 |   300394 | 天孚通信 | 通信设备   | 通信设备       | dynamic_factor_score |      99.353  | 20.00%             | 20.00%            | 100.00%                 |         455.2  |   79.98 |                99.353  |                   99.1622 |                  90.5327 |         29.97 |        98.64 |  97.71 |             88.876  |               82.1512 |             99.8062 |            55.0388   |         73.23 |                 0 |             | 可买            |                  1.06041 | normal_liquidity          | index_neutral  | 0.0444274 |             0 |
|      3 | 2026-05-29    | baseline_full_dynamic_factor_industry_cap2 |   301511 | 德福科技 | 电气设备   | 电气设备       | dynamic_factor_score |      99.2289 | 20.00%             | 20.00%            | 100.00%                 |         125.62 |   78    |                99.2289 |                   99.4045 |                  93.53   |         29.42 |        98.82 |  99.59 |             91.1047 |               79.593  |             96.7829 |            96.2597   |         71.49 |                 0 |             | 可买            |                  1.06041 | normal_liquidity          | index_neutral  | 0.0709226 |             0 |
|      4 | 2026-05-29    | baseline_full_dynamic_factor_industry_cap2 |   000636 | 风华高科 | 元器件     | 元器件         | dynamic_factor_score |      99.171  | 20.00%             | 20.00%            | 100.00%                 |          53.03 |   79.94 |                99.171  |                   99.4332 |                  88.6416 |         29.37 |        99.86 |  99.88 |             99.0504 |               98.062  |             99.3217 |             0.639535 |         73.08 |                 0 |             | 可买            |                  1.06041 | normal_liquidity          | index_neutral  | 0.0497619 |             0 |
|      5 | 2026-05-29    | baseline_full_dynamic_factor_industry_cap2 |   603256 | 宏和科技 | 玻璃       | 玻璃           | dynamic_factor_score |      98.4553 | 20.00%             | 20.00%            | 100.00%                 |         202.15 |   73.01 |                98.4553 |                   98.7592 |                  86.77   |         29.14 |        99.19 |  99.53 |             83.5465 |               81.8411 |             97.6938 |            42.7713   |         68.56 |                 0 |             | 可买            |                  1.06041 | normal_liquidity          | index_neutral  | 0.0530228 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260531_221517_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260531_221517_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260531_221517_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260531_221517_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
