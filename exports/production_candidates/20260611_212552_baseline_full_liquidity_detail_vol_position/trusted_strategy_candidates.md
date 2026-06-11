# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-11`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                    |   symbol | name   | industry   | industry_key   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:--------------------------------------------|---------:|:-------|:-----------|:---------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|------------------------:|----------:|--------------:|
|      1 | 2026-06-11    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      95.7056 | 6.34%              | 1.77%             | 70.00%                  |         349.8  |   70.9  |                    nan |                       nan |                  95.7056 |         29.34 |        99.73 | 100    |             95.6412 |               95.6606 |             95.3313 |              88.086  |         60.63 |                 0 |             | 过滤              |                 0.856874 | normal_liquidity          | index_neutral  |                     0.7 | 0.0891882 |     0         |
|      2 | 2026-06-11    | baseline_full_liquidity_detail_vol_position |   002460 | 赣锋锂业   | 小金属        | 小金属            | liquidity_detail_score |      95.4768 | 19.83%             | 5.55%             | 70.00%                  |          69.94 |   40.86 |                    nan |                       nan |                  95.4768 |         29.35 |        49.25 |  57.07 |             94.0527 |               87.6986 |             98.4502 |              96.1062 |         38.87 |                 0 |             | 观察              |                 0.856874 | normal_liquidity          | index_neutral  |                     0.7 | 0.0284996 |    -0.118922  |
|      3 | 2026-06-11    | baseline_full_liquidity_detail_vol_position |   002466 | 天齐锂业   | 小金属        | 小金属            | liquidity_detail_score |      94.6849 | 21.46%             | 6.01%             | 70.00%                  |          61    |   39.01 |                    nan |                       nan |                  94.6849 |         29.33 |        49.25 |  52.83 |             92.5417 |               83.5723 |             98.4308 |              97.6947 |         52.74 |                 0 |             | 观察              |                 0.856874 | normal_liquidity          | index_neutral  |                     0.7 | 0.0263269 |    -0.120784  |
|      4 | 2026-06-11    | baseline_full_liquidity_detail_vol_position |   600869 | 远东股份   | 电气设备       | 电气设备           | liquidity_detail_score |      94.5209 | 11.16%             | 3.12%             | 70.00%                  |          29.5  |   63.06 |                    nan |                       nan |                  94.5209 |         29.09 |        49.25 |  98.18 |             96.6098 |               95.37   |             94.6339 |              79.1166 |         63.26 |                 0 |             | 可买              |                 0.856874 | normal_liquidity          | index_neutral  |                     0.7 | 0.0506528 |    -0.0649762 |
|      5 | 2026-06-11    | baseline_full_liquidity_detail_vol_position |   000657 | 中钨高新   | 小金属        | 小金属            | liquidity_detail_score |      94.2169 | 11.22%             | 3.14%             | 70.00%                  |          77.95 |   77.93 |                    nan |                       nan |                  94.2169 |         29.72 |        99.24 |  97.83 |             94.5564 |               84.9671 |             98.7795 |              81.1701 |         70.89 |                 0 |             | 可买              |                 0.856874 | normal_liquidity          | index_neutral  |                     0.7 | 0.0503715 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260611_212552_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260611_212552_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260611_212552_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260611_212552_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
