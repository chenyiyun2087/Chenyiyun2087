# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-04`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

- 组合有效仓位为 19.79%，请确认是否由市场门禁或风格状态降仓触发。
- 未找到信号日动态权重记录，动态排序可能退化为等权因子。

## 候选明细

|   rank | signal_date   | strategy                                    |   symbol | name     | industry   | industry_key   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:--------------------------------------------|---------:|:---------|:-----------|:---------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-06-04    | baseline_full_liquidity_detail_vol_position |   002463 | 沪电股份 | 元器件     | 元器件         | liquidity_detail_score |      95.9512 | 3.25%              | 3.25%             | 100.00%                 |         140.82 |   74.26 |                    nan |                       nan |                  95.9512 |         29.84 |        98.41 |  96.3  |             93.7851 |               90.3001 |             99.0513 |              90.0484 |         69.94 |                 0 |             | 可买            |                 0.884327 | normal_liquidity          | index_neutral  | 0.0486439 |     0         |
|      2 | 2026-06-04    | baseline_full_liquidity_detail_vol_position |   600183 | 生益科技 | 元器件     | 元器件         | liquidity_detail_score |      94.9921 | 3.52%              | 3.52%             | 100.00%                 |         143.96 |   61.97 |                    nan |                       nan |                  94.9921 |         29.83 |        48.95 |  99.5  |             89.7193 |               95.334  |             99.1094 |              81.0842 |         64.18 |                 0 |             | 可买            |                 0.884327 | normal_liquidity          | index_neutral  | 0.044945  |     0         |
|      3 | 2026-06-04    | baseline_full_liquidity_detail_vol_position |   688630 | 芯碁微装 | 专用机械   | 专用机械       | liquidity_detail_score |      93.977  | 2.62%              | 2.62%             | 100.00%                 |         408    |   74.07 |                    nan |                       nan |                  93.977  |         28.62 |        99.82 |  98.51 |             93.3979 |               93.0106 |             91.3262 |              94.8693 |         70.85 |                 0 |             | 可买            |                 0.884327 | normal_liquidity          | index_neutral  | 0.0603445 |     0         |
|      4 | 2026-06-04    | baseline_full_liquidity_detail_vol_position |   300620 | 光库科技 | 通信设备   | 通信设备       | liquidity_detail_score |      93.7595 | 2.63%              | 2.63%             | 100.00%                 |         319.26 |   54.89 |                    nan |                       nan |                  93.7595 |         29.58 |        48.95 |  94.66 |             83.7561 |               90.5324 |             98.7803 |              91.7135 |         58.77 |                 0 |             | 可买            |                 0.884327 | normal_liquidity          | index_neutral  | 0.0600125 |    -0.0528096 |
|      5 | 2026-06-04    | baseline_full_liquidity_detail_vol_position |   300750 | 宁德时代 | 电气设备   | 电气设备       | liquidity_detail_score |      93.7365 | 7.77%              | 7.77%             | 100.00%                 |         408.2  |   38.57 |                    nan |                       nan |                  93.7365 |         29.92 |        48.95 |  56.71 |             87.0281 |               84.453  |             99.787  |              88.0155 |         49.12 |                 0 |             | 可买            |                 0.884327 | normal_liquidity          | index_neutral  | 0.0203501 |    -0.084979  |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_124647_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_124647_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_124647_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260605_124647_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
