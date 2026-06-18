# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；自适应主推送档：动态选择最近3个月最强策略，按T日市场/行业/量能状态调整45%-80%仓位。adaptive_market_style v2.2已通过三年回撤-37.33%硬底线。


- 信号日：`2026-06-17`；候选数：Top 5。
- 执行层：目标资金比例 `100%`；持有 `10` 个交易日；最多持仓 `5` 只。
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
|      1 | 2026-06-17    | baseline_full_liquidity_detail_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | liquidity_detail_score |      95.7868 | 16.61%             | 1.85%             | 100.00%                 |          78.5  |   74.12 |                    nan |                       nan |                  95.7868 |         29.76 |        99.59 |  99.65 |             94.34   |               88.4861 |             97.422  |              93.5259 |         59.77 |                 0 |             | 过滤              |                  1.03071 | normal_liquidity          | index_neutral  |                       1 | 0.085263  |    0          |
|      2 | 2026-06-17    | baseline_full_liquidity_detail_vol_position |   301217 | 铜冠铜箔   | 元器件        | 元器件            | liquidity_detail_score |      95.7343 | 20.33%             | 2.27%             | 100.00%                 |         181.69 |   63.6  |                    nan |                       nan |                  95.7343 |         29.48 |        48.14 |  99.81 |             96.7048 |               95.0959 |             98.3524 |              80.6939 |         62.18 |                 0 |             | 可买              |                  1.03071 | normal_liquidity          | index_neutral  |                       1 | 0.0696285 |    0          |
|      3 | 2026-06-17    | baseline_full_liquidity_detail_vol_position |   301526 | 国际复材   | 玻璃         | 玻璃             | liquidity_detail_score |      94.2917 | 16.79%             | 1.87%             | 100.00%                 |          40.23 |   77.11 |                    nan |                       nan |                  94.2917 |         29.54 |        97.44 |  99.9  |             96.2396 |               92.0915 |             96.8017 |              73.2312 |         69.93 |                 0 |             | 可买              |                  1.03071 | normal_liquidity          | index_neutral  |                       1 | 0.0843529 |    0          |
|      4 | 2026-06-17    | baseline_full_liquidity_detail_vol_position |   600378 | 昊华科技   | 化工原料       | 化工原料           | liquidity_detail_score |      94.2902 | 24.44%             | 2.73%             | 100.00%                 |          63.8  |   39.84 |                    nan |                       nan |                  94.2902 |         29.19 |        48.14 |  99.38 |             96.9955 |               98.1004 |             95.7162 |              68.9862 |         45.47 |                 0 |             | 过滤              |                  1.03071 | normal_liquidity          | index_neutral  |                       1 | 0.0579329 |   -0.00140867 |
|      5 | 2026-06-17    | baseline_full_liquidity_detail_vol_position |   002080 | 中材科技   | 玻璃         | 玻璃             | liquidity_detail_score |      94.2014 | 21.83%             | 2.44%             | 100.00%                 |          82.41 |   58.07 |                    nan |                       nan |                  94.2014 |         29.3  |        99.59 |  95.58 |             95.3479 |               82.3028 |             97.7321 |              90.599  |         61.4  |                 0 |             | 过滤              |                  1.03071 | normal_liquidity          | index_neutral  |                       1 | 0.0648508 |    0          |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260617_210210_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260617_210210_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260617_210210_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260617_210210_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
