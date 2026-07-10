# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`锁定`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-08`；候选数：Top 5。
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
|      1 | 2026-07-08    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      94.5999 | 15.53%             | 4.35%             | 50.00%                  |         802.32 |   61.29 |                    nan |                       nan |                  94.5999 |         29.79 |        49.5  |  96.92 |             90.0077 |               82.7847 |             99.4384 |              95.4493 |         65.38 |                 0 | SCAN        | 可买              |                 0.803794 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0363799 |    -0.142234  |
|      2 | 2026-07-08    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      94.2216 | 3.85%              | 1.19%             | 50.00%                  |         295    |   59.46 |                    nan |                       nan |                  94.2216 |         29.3  |        49.5  |  17.14 |             90.3176 |               87.2773 |             96.921  |              94.6166 |         43.38 |                 0 | SCAN        | 可买              |                 0.803794 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.133413  |    -0.106061  |
|      3 | 2026-07-08    | production_governed_vol_position |   688432 | 有研硅    | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.4484 | 5.93%              | 1.57%             | 50.00%                  |          44.81 |   69.86 |                    nan |                       nan |                  93.4484 |         28.32 |        49.5  |  99.92 |             96.8435 |               94.5004 |             95.5655 |              78.0984 |         67.79 |                 0 | SCAN        | 可买              |                 0.803794 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.10051   |    -0.0288253 |
|      4 | 2026-07-08    | production_governed_vol_position |   603893 | 瑞芯微    | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.1604 | 14.98%             | 4.58%             | 50.00%                  |         202    |   72.24 |                    nan |                       nan |                  93.1604 |         28.56 |        99.71 |  94    |             99.6321 |               95.7785 |             99.361  |              58.8304 |         76.14 |                 0 | CORE        | 可买              |                 0.803794 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.03454   |     0         |
|      5 | 2026-07-08    | production_governed_vol_position |   002384 | 东山精密   | 元器件        | 元器件            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.0901 | 9.71%              | 2.89%             | 50.00%                  |         237.56 |   47.64 |                    nan |                       nan |                  93.0901 |         29.98 |        49.5  |  84.88 |             88.0132 |               75.3679 |             99.7483 |              92.4671 |         61.38 |                 0 | BASE        | 可买              |                 0.803794 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0547075 |    -0.138651  |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_235033_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_235033_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_235033_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260709_235033_production_governed_vol_position/trusted_strategy_market_environment.csv`
