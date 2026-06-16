# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-16`；候选数：Top 5。
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
|      1 | 2026-06-16    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      95.8009 | 7.53%              | 1.73%             | 70.00%                  |         323    |   58.81 |                    nan |                       nan |                  95.8009 |         29.44 |        48.21 | 100    |             94.5187 |               95.4484 |             95.7389 |              89.6572 |         60.65 |                 0 |             | 可买              |                  1.02387 | normal_liquidity          | index_neutral  |                     0.7 | 0.0913132 |    -0.0766152 |
|      2 | 2026-06-16    | baseline_full_liquidity_detail_vol_position |   301217 | 铜冠铜箔   | 元器件        | 元器件            | liquidity_detail_score |      95.4353 | 9.83%              | 2.26%             | 70.00%                  |         178.17 |   76.93 |                    nan |                       nan |                  95.4353 |         29.42 |        98.47 |  99.85 |             98.1019 |               92.7174 |             96.7267 |              81.7161 |         68.8  |                 0 |             | 可买              |                  1.02387 | normal_liquidity          | index_neutral  |                     0.7 | 0.0699154 |     0         |
|      3 | 2026-06-16    | baseline_full_liquidity_detail_vol_position |   000657 | 中钨高新   | 小金属        | 小金属            | liquidity_detail_score |      94.8173 | 13.94%             | 3.21%             | 70.00%                  |          92.5  |   72.03 |                    nan |                       nan |                  94.8173 |         29.77 |        99.45 |  99.42 |             90.4707 |               91.6134 |             99.1478 |              84.1565 |         68.41 |                 0 |             | 可买              |                  1.02387 | normal_liquidity          | index_neutral  |                     0.7 | 0.049314  |     0         |
|      4 | 2026-06-16    | baseline_full_liquidity_detail_vol_position |   600378 | 昊华科技   | 化工原料       | 化工原料           | liquidity_detail_score |      94.33   | 12.37%             | 2.84%             | 70.00%                  |          58    |   60.33 |                    nan |                       nan |                  94.33   |         29.09 |        48.21 |  99.24 |             96.7461 |               97.8695 |             97.3271 |              69.1458 |         62.31 |                 0 |             | 可买              |                  1.02387 | normal_liquidity          | index_neutral  |                     0.7 | 0.0556042 |    -0.0921897 |
|      5 | 2026-06-16    | baseline_full_liquidity_detail_vol_position |   002466 | 天齐锂业   | 小金属        | 小金属            | liquidity_detail_score |      94.1513 | 26.33%             | 6.05%             | 70.00%                  |          63.99 |   42.81 |                    nan |                       nan |                  94.1513 |         29.37 |        48.21 |  74.88 |             84.1178 |               90.5481 |             98.0631 |              98.7604 |         56.29 |                 1 |             | 可买              |                  1.02387 | normal_liquidity          | index_neutral  |                     0.7 | 0.0261161 |    -0.0611796 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260616_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260616_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260616_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260616_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
