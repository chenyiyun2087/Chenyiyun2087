# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-10`；候选数：Top 5。
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
|      1 | 2026-06-10    | baseline_full_liquidity_detail_vol_position |   002463 | 沪电股份   | 元器件        | 元器件            | liquidity_detail_score |      94.9719 | 12.90%             | 2.90%             | 70.00%                  |         130.6  |   55.63 |                    nan |                       nan |                  94.9719 |         29.87 |        49.1  |  95.08 |             87.2967 |               92.5833 |             99.3997 |              88.8846 |         60.33 |                 0 |             | 可买              |                 0.868159 | normal_liquidity          | index_weak     |                     0.7 | 0.0544966 |    -0.0743497 |
|      2 | 2026-06-10    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      94.6043 | 7.86%              | 1.77%             | 70.00%                  |         292    |   69.92 |                    nan |                       nan |                  94.6043 |         29.29 |        99.76 |  99.96 |             90.6662 |               94.1131 |             95.5655 |              89.6592 |         68.47 |                 0 |             | 可买              |                 0.868159 | normal_liquidity          | index_weak     |                     0.7 | 0.0893685 |     0         |
|      3 | 2026-06-10    | baseline_full_liquidity_detail_vol_position |   600522 | 中天科技   | 通信设备       | 通信设备           | liquidity_detail_score |      93.8242 | 12.26%             | 2.76%             | 70.00%                  |          50.2  |   55.25 |                    nan |                       nan |                  93.8242 |         29.93 |        49.1  |  94.75 |             88.4198 |               93.4741 |             99.5546 |              72.7924 |         61.59 |                 1 | WATCH       | 观察              |                 0.868159 | normal_liquidity          | index_weak     |                     0.7 | 0.0573467 |    -0.0785609 |
|      4 | 2026-06-10    | baseline_full_liquidity_detail_vol_position |   601208 | 东材科技   | 化工原料       | 化工原料           | liquidity_detail_score |      93.6747 | 11.45%             | 2.58%             | 70.00%                  |          62.35 |   78.28 |                    nan |                       nan |                  93.6747 |         29.51 |        99.11 |  97.21 |             96.708  |               90.8792 |             99.2835 |              64.6204 |         70.56 |                 0 |             | 可买              |                 0.868159 | normal_liquidity          | index_weak     |                     0.7 | 0.061371  |     0         |
|      5 | 2026-06-10    | baseline_full_liquidity_detail_vol_position |   002460 | 赣锋锂业   | 小金属        | 小金属            | liquidity_detail_score |      93.42   | 25.53%             | 5.74%             | 70.00%                  |          67.6  |   33.99 |                    nan |                       nan |                  93.42   |         29.34 |        49.1  |  32.24 |             89.9884 |               79.0085 |             98.5089 |              96.7467 |         32.52 |                 0 |             | 观察              |                 0.868159 | normal_liquidity          | index_weak     |                     0.7 | 0.0275339 |    -0.154789  |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260610_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260610_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260610_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260610_212538_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
