# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-03`；候选数：Top 5。
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
|      1 | 2026-07-03    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      95.2252 | 4.17%              | 1.19%             | 50.00%                  |        267.5   |   46.19 |                    nan |                       nan |                  95.2252 |         29.2  |        48.91 |   4.34 |             88.282  |               95.5065 |             97.8501 |              96.32   |         43.8  |                 0 | BASE        | 可买              |                 0.993569 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.133418  |     -0.251891 |
|      2 | 2026-07-03    | production_governed_vol_position |   000021 | 深科技    | 元器件        | 元器件            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      95.1729 | 11.64%             | 2.75%             | 50.00%                  |         55.91  |   62.21 |                    nan |                       nan |                  95.1729 |         29.76 |        48.91 |  97.42 |             83.7885 |               95.2547 |             98.5861 |              96.5911 |         65.47 |                 0 | SCAN        | 可买              |                 0.993569 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0573925 |     -0.130076 |
|      3 | 2026-07-03    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      94.557  | 15.72%             | 3.98%             | 50.00%                  |        815.268 |   69.58 |                    nan |                       nan |                  94.557  |         29.76 |        48.91 |  95.41 |             87.1974 |               88.069  |             99.109  |              93.6084 |         68.97 |                 0 | SCAN        | 可买              |                 0.993569 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0397193 |     -0.128392 |
|      4 | 2026-07-03    | production_governed_vol_position |   300223 | 北京君正   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      93.278  | 9.04%              | 2.22%             | 50.00%                  |        259.56  |   66.06 |                    nan |                       nan |                  93.278  |         29.79 |        48.91 |  99.42 |             86.1321 |               90.2189 |             98.8185 |              79.7598 |         66.08 |                 1 | WATCH       | 可买              |                 0.993569 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.071103  |      0        |
|      5 | 2026-07-03    | production_governed_vol_position |   603986 | 兆易创新   | 半导体        | 半导体            | liquidity_quality | champion_core         | normal_risk_on  | liquidity_detail_score |      93.2532 | 9.43%              | 2.35%             | 50.00%                  |        677.77  |   68.92 |                    nan |                       nan |                  93.2532 |         29.99 |        48.91 |  94.81 |             82.239  |               81.5611 |             99.632  |              96.3974 |         64.29 |                 0 | SCAN        | 可买              |                 0.993569 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0673152 |     -0.193131 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260703_213759_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260703_213759_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260703_213759_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260703_213759_production_governed_vol_position/trusted_strategy_market_environment.csv`
