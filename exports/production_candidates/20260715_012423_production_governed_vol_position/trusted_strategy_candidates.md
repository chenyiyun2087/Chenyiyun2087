# 可信策略生产候选名单

## 口径

- 策略：`生产治理波动仓位策略`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`锁定`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-14`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | candidate_pool   | candidate_pool_role   | market_regime   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy              |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:-----------------|:----------------------|:----------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:-------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-07-14    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      95.8196 | 10.22%             | 20.00%            | 50.00%                  |         309    |   69.97 |                    nan |                       nan |                  95.8196 |         29.43 |        49.26 |  99.61 |             93.0701 |               92.8378 |             97.4448 |              94.2315 |         67.82 |                 0 | SCAN        | 可买              |                 0.841882 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0764309 |     -0.101744 |
|      2 | 2026-07-14    | production_governed_vol_position |   688347 | 华虹公司   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.8178 | 9.91%              | 20.00%            | 50.00%                  |         352.59 |   62.2  |                    nan |                       nan |                  94.8178 |         29.7  |        49.26 |  99.21 |             83.9915 |               92.1022 |             98.5482 |              98.2191 |         65.45 |                 0 | SCAN        | 可买              |                 0.841882 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0625665 |     -0.119625 |
|      3 | 2026-07-14    | production_governed_vol_position |   002371 | 北方华创   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.6743 | 9.51%              | 20.00%            | 50.00%                  |         774.2  |   47.15 |                    nan |                       nan |                  94.6743 |         29.8  |        49.26 |  93.77 |             84.1463 |               90.3213 |             99.4774 |              96.4189 |         62.67 |                 0 | BASE        | 可买              |                 0.841882 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0477665 |     -0.172297 |
|      4 | 2026-07-14    | production_governed_vol_position |   002156 | 通富微电   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.5249 | 10.09%             | 20.00%            | 50.00%                  |          77.87 |   47.44 |                    nan |                       nan |                  94.5249 |         29.86 |        49.26 |  97.17 |             92.7216 |               85.6949 |             99.3225 |              84.1463 |         65.56 |                 1 | WATCH       | 可买              |                 0.841882 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0523023 |      0        |
|      5 | 2026-07-14    | production_governed_vol_position |   002185 | 华天科技   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.4041 | 10.27%             | 20.00%            | 50.00%                  |          25.68 |   70.81 |                    nan |                       nan |                  94.4041 |         29.85 |        49.26 |  99.32 |             96.1092 |               93.825  |             99.5741 |              63.7244 |         67.71 |                 0 | CORE        | 可买              |                 0.841882 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0519685 |      0        |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_012423_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_012423_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_012423_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_012423_production_governed_vol_position/trusted_strategy_market_environment.csv`
