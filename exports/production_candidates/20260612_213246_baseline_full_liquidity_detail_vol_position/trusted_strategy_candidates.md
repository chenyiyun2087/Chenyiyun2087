# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-12`；候选数：Top 5。
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
|      1 | 2026-06-12    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      96.1857 | 7.70%              | 1.61%             | 70.00%                  |         302    |   62.76 |                    nan |                       nan |                  96.1857 |         29.38 |        49.22 |  99.9  |             96.1263 |               96.3006 |             95.8551 |              89.6378 |         54.79 |                 0 |             | 可买              |                  1.07918 | normal_liquidity          | index_neutral  |                     0.7 | 0.0984689 |    -0.13665   |
|      2 | 2026-06-12    | baseline_full_liquidity_detail_vol_position |   600869 | 远东股份   | 电气设备       | 电气设备           | liquidity_detail_score |      95.415  | 14.12%             | 2.94%             | 70.00%                  |          32.43 |   64.13 |                    nan |                       nan |                  95.415  |         29.2  |        49.22 |  99.05 |             96.533  |               95.952  |             97.7145 |              81.2512 |         46.43 |                 0 |             | 过滤              |                  1.07918 | normal_liquidity          | index_neutral  |                     0.7 | 0.0536935 |     0         |
|      3 | 2026-06-12    | baseline_full_liquidity_detail_vol_position |   000657 | 中钨高新   | 小金属        | 小金属            | liquidity_detail_score |      95.1685 | 15.66%             | 3.26%             | 70.00%                  |          79    |   69.98 |                    nan |                       nan |                  95.1685 |         29.74 |        49.22 |  98.59 |             93.8795 |               89.4829 |             98.8379 |              84.9119 |         59.4  |                 0 |             | 可买              |                  1.07918 | normal_liquidity          | index_neutral  |                     0.7 | 0.0484287 |     0         |
|      4 | 2026-06-12    | baseline_full_liquidity_detail_vol_position |   002466 | 天齐锂业   | 小金属        | 小金属            | liquidity_detail_score |      94.6587 | 28.12%             | 5.86%             | 70.00%                  |          62.5  |   42.09 |                    nan |                       nan |                  94.6587 |         29.33 |        49.22 |  61.53 |             88.4176 |               87.8559 |             98.7991 |              98.7023 |         45.84 |                 0 |             | 观察              |                  1.07918 | normal_liquidity          | index_neutral  |                     0.7 | 0.0269725 |    -0.099164  |
|      5 | 2026-06-12    | baseline_full_liquidity_detail_vol_position |   688498 | 源杰科技   | 半导体        | 半导体            | liquidity_detail_score |      94.606  | 4.40%              | 0.92%             | 70.00%                  |        1400.01 |   55.99 |                    nan |                       nan |                  94.606  |         29.78 |        49.22 |  59.09 |             93.5503 |               89.909  |             99.2253 |              78.191  |         52.02 |                 0 |             | 可买              |                  1.07918 | normal_liquidity          | index_neutral  |                     0.7 | 0.172496  |    -0.0508407 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260612_213246_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260612_213246_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260612_213246_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260612_213246_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
