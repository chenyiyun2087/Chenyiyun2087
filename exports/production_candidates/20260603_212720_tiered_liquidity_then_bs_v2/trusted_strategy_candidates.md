# 可信策略生产候选名单

## 口径

- 策略：`流动性分层B点进攻策略`，排序字段：`bs_score_v2`。
- 策略ID：`tiered_liquidity_then_bs_v2`。
- 信号日：`2026-06-03`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                    |   symbol | name   | industry   | industry_key   | sort_col    |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:----------------------------|---------:|:-------|:-----------|:---------------|:------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-06-03    | tiered_liquidity_then_bs_v2 |   300433 | 蓝思科技   | 元器件        | 元器件            | bs_score_v2 |        74.94 | 20.00%             | 20.00%            | 100.00%                 |          44.39 |   81.41 |                99.4976 |                   99.5526 |                  92.8855 |         29.79 |        99.11 |  99.26 |             94.2885 |               85.3437 |             97.9284 |             68.1704  |         74.94 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0439044 |             0 |
|      2 | 2026-06-03    | tiered_liquidity_then_bs_v2 |   002897 | 意华股份   | 元器件        | 元器件            | bs_score_v2 |        74.41 | 20.00%             | 20.00%            | 100.00%                 |          91.2  |   80.59 |                96.6165 |                   97.8928 |                  81.7352 |         27.51 |        99.21 |  98.57 |             95.818  |               76.5537 |             92.3524 |              5.55663 |         74.41 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0571509 |             0 |
|      3 | 2026-06-03    | tiered_liquidity_then_bs_v2 |   603678 | 火炬电子   | 元器件        | 元器件            | bs_score_v2 |        74.34 | 20.00%             | 20.00%            | 100.00%                 |          63.93 |   81.02 |                97.4197 |                   98.7391 |                  87.5151 |         27.17 |        99.83 |  99.71 |             99.4385 |               99.516  |             96.3601 |             20.1936  |         74.34 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0465741 |             0 |
|      4 | 2026-06-03    | tiered_liquidity_then_bs_v2 |   300179 | 四方达    | 矿物制品       | 矿物制品           | bs_score_v2 |        73.38 | 20.00%             | 20.00%            | 100.00%                 |          51.3  |   78.14 |                98.4705 |                   99.0791 |                  90.3943 |         28.62 |        98.97 |  99.57 |             97.3282 |               97.6186 |             94.7531 |             39.1288  |         73.38 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0815662 |             0 |
|      5 | 2026-06-03    | tiered_liquidity_then_bs_v2 |   300394 | 天孚通信   | 通信设备       | 通信设备           | bs_score_v2 |        72.64 | 20.00%             | 20.00%            | 100.00%                 |         497.38 |   76.28 |                99.4047 |                   99.282  |                  94.324  |         29.97 |        98.43 |  98.9  |             91.0745 |               83.4269 |             99.6902 |             86.8151  |         72.64 |                 0 |             | 可买              |                 0.997168 | normal_liquidity          | index_neutral  | 0.0506065 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_212720_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_212720_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_212720_tiered_liquidity_then_bs_v2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260603_212720_tiered_liquidity_then_bs_v2/trusted_strategy_market_environment.csv`
