# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-09`；候选数：Top 5。
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
|      1 | 2026-06-09    | baseline_full_liquidity_detail_vol_position |   002463 | 沪电股份   | 元器件        | 元器件            | liquidity_detail_score |      96.6565 | 17.19%             | 3.09%             | 70.00%                  |         141.09 |   64.93 |                    nan |                       nan |                  96.6565 |         29.87 |        49.2  |  97.35 |             94.2133 |               93.0714 |             99.6129 |              90.8458 |         65.31 |                 0 |             | 可买              |                 0.865631 | normal_liquidity          | index_neutral  |                     0.7 | 0.0511087 |    0          |
|      2 | 2026-06-09    | baseline_full_liquidity_detail_vol_position |   688498 | 源杰科技   | 半导体        | 半导体            | liquidity_detail_score |      95.045  | 5.08%              | 0.91%             | 70.00%                  |        1475    |   44.77 |                    nan |                       nan |                  95.045  |         29.75 |        49.2  |  42.96 |             89.1426 |               91.3489 |             98.3937 |              90.8845 |         51.96 |                 0 |             | 可买              |                 0.865631 | normal_liquidity          | index_neutral  |                     0.7 | 0.173054  |   -0.136417   |
|      3 | 2026-06-09    | baseline_full_liquidity_detail_vol_position |   600522 | 中天科技   | 通信设备       | 通信设备           | liquidity_detail_score |      94.993  | 16.33%             | 2.94%             | 70.00%                  |          54.48 |   78.89 |                    nan |                       nan |                  94.993  |         29.92 |        99.81 |  96.13 |             95.2197 |               93.9036 |             99.5549 |              70.3697 |         64.62 |                 1 |             | 过滤              |                 0.865631 | normal_liquidity          | index_neutral  |                     0.7 | 0.0538191 |    0          |
|      4 | 2026-06-09    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      94.4004 | 9.54%              | 1.72%             | 70.00%                  |         250.82 |   57.31 |                    nan |                       nan |                  94.4004 |         29.26 |        49.2  | 100    |             88.6394 |               94.6391 |             96.6712 |              89.6265 |         61.95 |                 0 |             | 可买              |                 0.865631 | normal_liquidity          | index_neutral  |                     0.7 | 0.0920917 |   -0.00539297 |
|      5 | 2026-06-09    | baseline_full_liquidity_detail_vol_position |   300308 | 中际旭创   | 通信设备       | 通信设备           | liquidity_detail_score |      94.342  | 21.86%             | 3.93%             | 70.00%                  |        1180    |   61.49 |                    nan |                       nan |                  94.342  |         30    |        49.2  |  93.96 |             93.2262 |               92.9359 |             99.8452 |              67.7956 |         60.75 |                 0 |             | 可买              |                 0.865631 | normal_liquidity          | index_neutral  |                     0.7 | 0.0401945 |   -0.078125   |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260609_212547_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260609_212547_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260609_212547_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260609_212547_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
