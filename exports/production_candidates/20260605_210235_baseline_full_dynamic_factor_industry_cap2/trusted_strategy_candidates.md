# 可信策略生产候选名单

## 口径

- 策略：`动态因子均衡策略（单行业最多2只）`，排序字段：`dynamic_factor_score`。
- 策略ID：`baseline_full_dynamic_factor_industry_cap2`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-05`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
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
|      1 | 2026-06-05    | baseline_full_dynamic_factor_industry_cap2 |   000725 | 京东方Ａ   | 元器件        | 元器件            | dynamic_factor_score |      99.3956 | 20.00%             | 20.00%            | 100.00%                 |           6.43 |   82.35 |                99.3956 |                   99.4525 |                  89.1835 |         29.9  |        99.54 |  98.97 |             98.3743 |               95.4519 |             99.8258 |             3.503    |         76.12 |                 0 |             | 可买              |                 0.985827 | normal_liquidity          | index_neutral  | 0.049355  |             0 |
|      2 | 2026-06-05    | baseline_full_dynamic_factor_industry_cap2 |   688146 | 中船特气   | 半导体        | 半导体            | dynamic_factor_score |      98.8885 | 20.00%             | 20.00%            | 100.00%                 |         231.11 |   74.25 |                98.8885 |                   99.4289 |                  94.2215 |         29.12 |        99.63 | 100    |             95.0261 |               93.1875 |             97.3099 |            78.1498   |         69.88 |                 0 |             | 可买              |                 0.985827 | normal_liquidity          | index_neutral  | 0.0968314 |             0 |
|      3 | 2026-06-05    | baseline_full_dynamic_factor_industry_cap2 |   605358 | 立昂微    | 半导体        | 半导体            | dynamic_factor_score |      98.7015 | 20.00%             | 20.00%            | 100.00%                 |          66.77 |   76.23 |                98.7015 |                   98.8182 |                  92.3043 |         29.31 |        99.5  |  97.37 |             95.2584 |               90.6135 |             98.4324 |            58.1575   |         69.49 |                 0 |             | 可买              |                 0.985827 | normal_liquidity          | index_neutral  | 0.0500954 |             0 |
|      4 | 2026-06-05    | baseline_full_dynamic_factor_industry_cap2 |   600869 | 远东股份   | 电气设备       | 电气设备           | dynamic_factor_score |      98.0948 | 20.00%             | 20.00%            | 100.00%                 |          28.51 |   74.43 |                98.0948 |                   98.7731 |                  88.9063 |         28.88 |        99.05 |  98.76 |             95.1616 |               93.3811 |             96.5357 |            28.7981   |         68.44 |                 0 |             | 可买              |                 0.985827 | normal_liquidity          | index_neutral  | 0.0525985 |             0 |
|      5 | 2026-06-05    | baseline_full_dynamic_factor_industry_cap2 |   603773 | 沃格光电   | 元器件        | 元器件            | dynamic_factor_score |      97.9744 | 20.00%             | 20.00%            | 100.00%                 |         132.4  |   77.43 |                97.9744 |                   98.9573 |                  85.8689 |         28.92 |        99.15 |  99.61 |             94.0004 |               92.8585 |             96.9034 |             0.445133 |         71.28 |                 0 |             | 可买              |                 0.985827 | normal_liquidity          | index_neutral  | 0.0680093 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_210235_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_210235_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_210235_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_210235_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
