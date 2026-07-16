# 可信策略生产候选名单

## 口径

- 策略：`生产治理波动仓位策略`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-15`；候选数：Top 5。
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
|      1 | 2026-07-15    | production_governed_vol_position |   002156 | 通富微电   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      95.6308 | 8.94%              | 3.03%             | 50.00%                  |          78.71 |   46.94 |                    nan |                       nan |                  95.6308 |         29.87 |        47.88 |  97.29 |             94.4455 |               91.8521 |             99.6323 |              81.9237 |         63.5  |                 1 | WATCH       | 可买              |                 0.807227 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0522566 |     0         |
|      2 | 2026-07-15    | production_governed_vol_position |   002384 | 东山精密   | 元器件        | 元器件            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      94.989  | 7.03%              | 2.66%             | 50.00%                  |         262.49 |   46.97 |                    nan |                       nan |                  94.989  |         29.97 |        47.88 |  87.67 |             91.4457 |               90.2071 |             99.7871 |              82.4076 |         56.52 |                 1 | WATCH       | 观察              |                 0.807227 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0594594 |    -0.0482596 |
|      3 | 2026-07-15    | production_governed_vol_position |   603501 | 豪威集团   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      92.5203 | 13.93%             | 4.74%             | 50.00%                  |         111.12 |   73.26 |                    nan |                       nan |                  92.5203 |         29.12 |        97.41 |  97.48 |             95.9357 |               93.4198 |             99.0517 |              56.3577 |         74.91 |                 0 | CORE        | 可买              |                 0.807227 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0333366 |     0         |
|      4 | 2026-07-15    | production_governed_vol_position |   300759 | 康龙化成   | 化学制药       | 化学制药           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.7591 | 13.35%             | 4.36%             | 50.00%                  |          40.3  |   73.96 |                    nan |                       nan |                  91.7591 |         28.38 |        99.79 |  99.96 |             98.6646 |               97.5227 |             97.484  |              49.3517 |         75.44 |                 0 | CORE        | 可买              |                 0.807227 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0363041 |     0         |
|      5 | 2026-07-15    | production_governed_vol_position |   688498 | 源杰科技   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      90.9577 | 6.75%              | 2.55%             | 50.00%                  |        1809    |   46.28 |                    nan |                       nan |                  90.9577 |         29.7  |        47.88 |  93.63 |             77.666  |               74.7049 |             98.8775 |              97.8711 |         63.08 |                 0 | BASE        | 可买              |                 0.807227 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0620093 |    -0.0408271 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_214704_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_214704_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_214704_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260715_214704_production_governed_vol_position/trusted_strategy_market_environment.csv`
