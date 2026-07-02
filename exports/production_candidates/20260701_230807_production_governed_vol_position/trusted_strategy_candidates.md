# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-01`；候选数：Top 5。
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
|      1 | 2026-07-01    | production_governed_vol_position |   000021 | 深科技    | 元器件        | 元器件            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      98.3258 | 11.51%             | 3.18%             | 50.00%                  |          62.26 |   68.4  |                    nan |                       nan |                  98.3258 |         29.74 |        47.52 |  98.37 |             96.6279 |               96.686  |             99.3217 |              99.4574 |         54.39 |                 0 | SCAN        | 过滤              |                  1.15462 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0497914 |    -0.0312743 |
|      2 | 2026-07-01    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      94.8286 | 4.01%              | 1.23%             | 50.00%                  |         330    |   47.15 |                    nan |                       nan |                  94.8286 |         29.12 |        47.52 |  66.36 |             91.1434 |               90.9302 |             96.7442 |              96.2209 |         52.38 |                 0 | BASE        | 可买              |                  1.15462 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.128436  |    -0.0770991 |
|      3 | 2026-07-01    | production_governed_vol_position |   300223 | 北京君正   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      94.5596 | 8.51%              | 2.51%             | 50.00%                  |         259.05 |   65.63 |                    nan |                       nan |                  94.5596 |         29.77 |        96.28 |  99.17 |             88.2946 |               93.8372 |             98.7209 |              83.2364 |         69.42 |                 1 | TRADE       | 可买              |                  1.15462 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0628896 |     0         |
|      4 | 2026-07-01    | production_governed_vol_position |   688019 | 安集科技   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      94.2662 | 4.51%              | 1.47%             | 50.00%                  |         322.6  |   47.17 |                    nan |                       nan |                  94.2662 |         28.72 |        47.52 |  49.15 |             95.0388 |               93.0426 |             94.3217 |              88.6047 |         39.46 |                 0 | BASE        | 过滤              |                  1.15462 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.10774   |    -0.123434  |
|      5 | 2026-07-01    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      94.1303 | 21.46%             | 5.93%             | 50.00%                  |         935.36 |   66.48 |                    nan |                       nan |                  94.1303 |         29.73 |        97.83 |  97.58 |             85.6589 |               86.2597 |             99.0116 |              95.6783 |         66.38 |                 0 | SCAN        | 可买              |                  1.15462 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0266679 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_230807_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_230807_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_230807_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_230807_production_governed_vol_position/trusted_strategy_market_environment.csv`
