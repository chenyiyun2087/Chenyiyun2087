# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-07`；候选数：Top 5。
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
|      1 | 2026-07-07    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.3831 | 16.21%             | 4.26%             | 50.00%                  |         806.39 |   63.05 |                    nan |                       nan |                  93.3831 |         29.78 |        49.49 |  96.9  |             83.811  |               83.0364 |             99.206  |              95.7785 |         63.82 |                 0 | SCAN        | 可买              |                 0.808144 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0371288 |    -0.137883  |
|      2 | 2026-07-07    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.0396 | 4.22%              | 1.19%             | 50.00%                  |         276    |   53.67 |                    nan |                       nan |                  93.0396 |         29.29 |        49.49 |  12.01 |             83.4237 |               88.323  |             96.9791 |              95.062  |         41.81 |                 0 | BASE        | 可买              |                 0.808144 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.13324   |    -0.228119  |
|      3 | 2026-07-07    | production_governed_vol_position |   000938 | 紫光股份   | IT设备       | IT设备           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      92.6828 | 13.17%             | 3.35%             | 50.00%                  |          31.48 |   67.74 |                    nan |                       nan |                  92.6828 |         29.4  |        49.49 |  92.02 |             96.2432 |               97.2308 |             99.2641 |              47.5988 |         67.48 |                 0 | SCAN        | 可买              |                 0.808144 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0472329 |    -0.0549385 |
|      4 | 2026-07-07    | production_governed_vol_position |   688766 | 普冉股份   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      92.6401 | 7.83%              | 1.94%             | 50.00%                  |         710    |   63.09 |                    nan |                       nan |                  92.6401 |         29.49 |        49.49 |  99.73 |             86.9094 |               85.3408 |             97.6375 |              84.9148 |         63.81 |                 0 | SCAN        | 可买              |                 0.808144 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0814542 |    -0.183908  |
|      5 | 2026-07-07    | production_governed_vol_position |   688017 | 绿的谐波   | 机械基件       | 机械基件           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      92.4121 | 8.58%              | 2.19%             | 50.00%                  |         465.07 |   60.47 |                    nan |                       nan |                  92.4121 |         29.64 |        49.49 |  83.02 |             87.3935 |               86.0186 |             98.2765 |              77.6917 |         61.58 |                 0 | SCAN        | 可买              |                 0.808144 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0720586 |    -0.0469877 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_234006_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_234006_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_234006_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_234006_production_governed_vol_position/trusted_strategy_market_environment.csv`
