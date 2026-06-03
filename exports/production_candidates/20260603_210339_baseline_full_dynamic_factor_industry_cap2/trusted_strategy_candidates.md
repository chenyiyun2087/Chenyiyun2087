# 可信策略生产候选名单

## 口径

- 策略：`动态因子均衡策略（单行业最多2只）`，排序字段：`dynamic_factor_score`。
- 策略ID：`baseline_full_dynamic_factor_industry_cap2`。
- 信号日：`2026-06-03`；候选数：Top 5。
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
|      1 | 2026-06-03    | baseline_full_dynamic_factor_industry_cap2 |   300433 | 蓝思科技   | 元器件        | 元器件            | dynamic_factor_score |      99.4976 | 20.00%             | 20.00%            | 100.00%                 |          44.39 |   81.41 |                99.4976 |                   99.5526 |                  92.8855 |         29.79 |        99.11 |  99.26 |             94.2885 |               85.3437 |             97.9284 |              68.1704 |         74.94 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0439044 |             0 |
|      2 | 2026-06-03    | baseline_full_dynamic_factor_industry_cap2 |   300308 | 中际旭创   | 通信设备       | 通信设备           | dynamic_factor_score |      99.4384 | 20.00%             | 20.00%            | 100.00%                 |        1275    |   74.19 |                99.4384 |                   99.1692 |                  95.9129 |         30    |        99.54 |  98.3  |             92.3911 |               86.9894 |             99.8064 |              94.153  |         69.77 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0381538 |             0 |
|      3 | 2026-06-03    | baseline_full_dynamic_factor_industry_cap2 |   300394 | 天孚通信   | 通信设备       | 通信设备           | dynamic_factor_score |      99.4047 | 20.00%             | 20.00%            | 100.00%                 |         497.38 |   76.28 |                99.4047 |                   99.282  |                  94.324  |         29.97 |        98.43 |  98.9  |             91.0745 |               83.4269 |             99.6902 |              86.8151 |         72.64 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0506065 |             0 |
|      4 | 2026-06-03    | baseline_full_dynamic_factor_industry_cap2 |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | dynamic_factor_score |      98.9687 | 20.00%             | 20.00%            | 100.00%                 |          60.47 |   77    |                98.9687 |                   99.1559 |                  91.6664 |         29.41 |        98.66 |  99.11 |             96.8054 |               96.4376 |             98.2962 |              38.819  |         61.8  |                 0 |             | 过滤              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0620757 |             0 |
|      5 | 2026-06-03    | baseline_full_dynamic_factor_industry_cap2 |   601208 | 东材科技   | 化工原料       | 化工原料           | dynamic_factor_score |      98.7653 | 20.00%             | 20.00%            | 100.00%                 |          56.49 |   76.69 |                98.7653 |                   98.6856 |                  93.6609 |         29.44 |        98.59 |  97.21 |             95.6631 |               84.0658 |             98.4124 |              79.0319 |         62.45 |                 0 |             | 过滤              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0577612 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_210339_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_210339_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_210339_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_210339_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
