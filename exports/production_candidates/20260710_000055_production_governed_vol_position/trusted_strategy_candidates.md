# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-06`；候选数：Top 5。
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
|      1 | 2026-07-06    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      93.3881 | 11.80%             | 4.11%             | 50.00%                  |         803.6  |   68.2  |                    nan |                       nan |                  93.3881 |         29.77 |        48.64 |  96.4  |             82.0875 |               84.9923 |             99.3222 |              96.3013 |         67.46 |                 0 | SCAN        | 可买              |                 0.964619 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0384411 |    -0.140866  |
|      2 | 2026-07-06    | production_governed_vol_position |   001309 | 德明利    | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      93.0091 | 8.24%              | 2.96%             | 50.00%                  |         935    |   70.92 |                    nan |                       nan |                  93.0091 |         29.88 |        48.64 |  98.39 |             90.2401 |               82.7653 |             99.5933 |              77.6723 |         67.91 |                 0 | CORE        | 可买              |                 0.964619 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0535005 |    -0.0360825 |
|      3 | 2026-07-06    | production_governed_vol_position |   002050 | 三花智控   | 家用电器       | 家用电器           | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      92.8082 | 11.99%             | 4.35%             | 50.00%                  |          46.15 |   46.82 |                    nan |                       nan |                  92.8082 |         29.55 |        48.64 |  63.26 |             91.7506 |               92.4284 |             99.6321 |              62.4903 |         60.07 |                 0 | BASE        | 可买              |                 0.964619 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0363791 |    -0.057971  |
|      4 | 2026-07-06    | production_governed_vol_position |   688372 | 伟测科技   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      92.3593 | 8.89%              | 3.19%             | 50.00%                  |         180.88 |   70.36 |                    nan |                       nan |                  92.3593 |         27.79 |        48.64 |  94.05 |             94.3842 |               93.842  |             92.4477 |              84.8567 |         68.34 |                 0 | CORE        | 可买              |                 0.964619 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.049585  |    -0.0275269 |
|      5 | 2026-07-06    | production_governed_vol_position |   603667 | 五洲新春   | 机械基件       | 机械基件           | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      92.2701 | 9.09%              | 3.32%             | 50.00%                  |          68.54 |   46.01 |                    nan |                       nan |                  92.2701 |         27.97 |        48.64 |  46.07 |             89.0395 |               91.615  |             96.1464 |              90.0465 |         54.81 |                 0 | BASE        | 可买              |                 0.964619 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0476526 |    -0.0999343 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260710_000055_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260710_000055_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260710_000055_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260710_000055_production_governed_vol_position/trusted_strategy_market_environment.csv`
