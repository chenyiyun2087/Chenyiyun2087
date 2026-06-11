# 可信策略生产候选名单

## 口径

- 策略：`动态因子均衡策略（单行业最多2只）`，排序字段：`dynamic_factor_score`。
- 策略ID：`baseline_full_dynamic_factor_industry_cap2`。
- 风险档位：`balanced`；均衡档：流动性质量防守策略+市场门禁，基准80%仓位，弱市场由门禁降至约50%。

- 信号日：`2026-06-04`；候选数：Top 5。
- 执行层：目标资金比例 `80%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name   | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:-------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-06-04    | baseline_full_dynamic_factor_industry_cap2 |   300408 | 三环集团   | 元器件        | 元器件            | dynamic_factor_score |      99.2259 | 20.00%             | 20.00%            | 100.00%                 |         139.03 |   72.55 |                99.2259 |                   99.1774 |                  93.6419 |         29.73 |        98.06 |  99.13 |             93.1655 |               94.3078 |             98.5673 |            64.3756   |         70.16 |                 0 |             | 可买              |                 0.884327 | normal_liquidity          | index_neutral  | 0.0487598 |             0 |
|      2 | 2026-06-04    | baseline_full_dynamic_factor_industry_cap2 |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | dynamic_factor_score |      99.1005 | 20.00%             | 20.00%            | 100.00%                 |          64    |   77.01 |                99.1005 |                   99.3531 |                  91.5936 |         29.46 |        98.72 |  99.28 |             96.4763 |               96.5537 |             97.5992 |            38.9545   |         61.89 |                 0 |             | 过滤              |                 0.884327 | normal_liquidity          | index_neutral  | 0.0624822 |             0 |
|      3 | 2026-06-04    | baseline_full_dynamic_factor_industry_cap2 |   000636 | 风华高科   | 元器件        | 元器件            | dynamic_factor_score |      99.0686 | 20.00%             | 20.00%            | 100.00%                 |          63.5  |   72.79 |                99.0686 |                   99.2836 |                  88.3165 |         29.65 |        98.99 |  99.94 |             95.334  |               98.8383 |             98.8964 |             0.561471 |         71.05 |                 0 |             | 可买              |                 0.884327 | normal_liquidity          | index_neutral  | 0.0477462 |             0 |
|      4 | 2026-06-04    | baseline_full_dynamic_factor_industry_cap2 |   600487 | 亨通光电   | 通信设备       | 通信设备           | dynamic_factor_score |      98.9159 | 20.00%             | 20.00%            | 100.00%                 |          97.87 |   78.55 |                98.9159 |                   98.6167 |                  88.7573 |         29.9  |        99.82 |  95.99 |             96.3795 |               91.5198 |             99.8064 |             9.15779  |         71.78 |                 0 |             | 可买              |                 0.884327 | normal_liquidity          | index_neutral  | 0.0514984 |             0 |
|      5 | 2026-06-04    | baseline_full_dynamic_factor_industry_cap2 |   688146 | 中船特气   | 半导体        | 半导体            | dynamic_factor_score |      98.8034 | 20.00%             | 20.00%            | 100.00%                 |         219.19 |   69.96 |                98.8034 |                   99.247  |                  92.4352 |         29.05 |        99.82 | 100    |             91.1713 |               89.6612 |             94.2691 |            78.7803   |         60.72 |                 0 |             | 过滤              |                 0.884327 | normal_liquidity          | index_neutral  | 0.0971493 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_210228_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_210228_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_210228_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_210228_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
