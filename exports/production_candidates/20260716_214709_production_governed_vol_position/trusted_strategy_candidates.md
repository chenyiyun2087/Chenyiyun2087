# 可信策略生产候选名单

## 口径

- 策略：`生产治理波动仓位策略`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_weak`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`defensive_low_liquidity_weak_index`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-16`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | candidate_pool    | candidate_pool_role   | market_regime   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy                           |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:------------------|:----------------------|:----------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:--------------------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-07-16    | production_governed_vol_position |   002156 | 通富微电   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      94.8671 | 6.05%              | 3.07%             | 50.00%                  |          75.95 |   45.95 |                    nan |                       nan |                  94.8671 |         29.88 |        48.23 |  93.9  |             88.2307 |               93.1669 |             99.2838 |              85.1336 |         60.62 |                 1 | WATCH       | 可买              |                 0.762725 | low_liquidity             | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0515672 |    -0.0350654 |
|      2 | 2026-07-16    | production_governed_vol_position |   002384 | 东山精密   | 元器件        | 元器件            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.7502 | 4.68%              | 2.67%             | 50.00%                  |         268.8  |   45.96 |                    nan |                       nan |                  93.7502 |         29.97 |        48.23 |  85.39 |             87.0693 |               89.0631 |             99.7096 |              80.6039 |         54.96 |                 1 | WATCH       | 观察              |                 0.762725 | low_liquidity             | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0593016 |    -0.0253807 |
|      3 | 2026-07-16    | production_governed_vol_position |   000938 | 紫光股份   | IT设备       | IT设备           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      92.0907 | 5.71%              | 2.87%             | 50.00%                  |          38.59 |   67.06 |                    nan |                       nan |                  92.0907 |         29.77 |        48.23 |  99.25 |             94.1154 |               97.7352 |             99.3419 |              40.1278 |         67.28 |                 0 | SCAN        | 可买              |                 0.762725 | low_liquidity             | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0550469 |     0         |
|      4 | 2026-07-16    | production_governed_vol_position |   300759 | 康龙化成   | 化学制药       | 化学制药           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.5912 | 7.84%              | 3.99%             | 50.00%                  |          38.6  |   69.63 |                    nan |                       nan |                  91.5912 |         28.66 |        48.23 |  99.9  |             96.2253 |               97.6771 |             97.9481 |              47.8901 |         69.85 |                 0 | SCAN        | 可买              |                 0.762725 | low_liquidity             | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0395938 |    -0.0421836 |
|      5 | 2026-07-16    | production_governed_vol_position |   600900 | 长江电力   | 水力发电       | 水力发电           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      90.7798 | 25.71%             | 15.17%            | 50.00%                  |          28.24 |   62.81 |                    nan |                       nan |                  90.7798 |         29.12 |        48.23 |  85.35 |             79.849  |               86.0434 |             99.5161 |              81.4944 |         61.29 |                 0 | SCAN        | 可买              |                 0.762725 | low_liquidity             | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0104256 |    -0.0153417 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260716_214709_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260716_214709_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260716_214709_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260716_214709_production_governed_vol_position/trusted_strategy_market_environment.csv`
