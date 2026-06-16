# 可信策略生产候选名单

## 口径

- 策略：`流动性质量稳健策略（波动仓位）`，排序字段：`liquidity_detail_score`。
- 策略ID：`baseline_full_liquidity_detail_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-15`；候选数：Top 5。
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
|      1 | 2026-06-15    | baseline_full_liquidity_detail_vol_position |   002466 | 天齐锂业   | 小金属        | 小金属            | liquidity_detail_score |      95.1597 | 20.18%             | 5.76%             | 70.00%                  |          63.72 |   42.07 |                    nan |                       nan |                  95.1597 |         29.36 |        48.63 |  60.74 |             88.1543 |               90.6941 |             99.2051 |              98.9725 |         52.47 |                 0 |             | 观察              |                   1.0148 | normal_liquidity          | index_neutral  |                     0.7 | 0.0274554 |    -0.0651408 |
|      2 | 2026-06-15    | baseline_full_liquidity_detail_vol_position |   002460 | 赣锋锂业   | 小金属        | 小金属            | liquidity_detail_score |      94.7193 | 19.60%             | 5.59%             | 70.00%                  |          71.32 |   38.49 |                    nan |                       nan |                  94.7193 |         29.37 |        48.63 |  53.1  |             85.9442 |               92.0318 |             98.9531 |              97.2276 |         35.39 |                 0 |             | 观察              |                   1.0148 | normal_liquidity          | index_neutral  |                     0.7 | 0.0282669 |    -0.0758067 |
|      3 | 2026-06-15    | baseline_full_liquidity_detail_vol_position |   688146 | 中船特气   | 半导体        | 半导体            | liquidity_detail_score |      94.3423 | 5.82%              | 1.66%             | 70.00%                  |         318.3  |   53.57 |                    nan |                       nan |                  94.3423 |         29.41 |        48.63 | 100    |             88.0186 |               94.7073 |             95.7348 |              89.589  |         58.05 |                 0 |             | 可买              |                   1.0148 | normal_liquidity          | index_neutral  |                     0.7 | 0.0951472 |    -0.0900515 |
|      4 | 2026-06-15    | baseline_full_liquidity_detail_vol_position |   600869 | 远东股份   | 电气设备       | 电气设备           | liquidity_detail_score |      94.3175 | 10.44%             | 2.98%             | 70.00%                  |          32.68 |   54.39 |                    nan |                       nan |                  94.3175 |         29.27 |        48.63 |  98.91 |             90.2869 |               95.4827 |             96.7623 |              83.9667 |         58.3  |                 0 |             | 可买              |                   1.0148 | normal_liquidity          | index_neutral  |                     0.7 | 0.0530662 |     0         |
|      5 | 2026-06-15    | baseline_full_liquidity_detail_vol_position |   002240 | 盛新锂能   | 小金属        | 小金属            | liquidity_detail_score |      94.0849 | 13.96%             | 3.98%             | 70.00%                  |          51.63 |   36.68 |                    nan |                       nan |                  94.0849 |         28.9  |        48.63 |  81.39 |             91.5471 |               96.3746 |             96.0838 |              83.734  |         54.17 |                 0 |             | 可买              |                   1.0148 | normal_liquidity          | index_neutral  |                     0.7 | 0.0396921 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260615_212551_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260615_212551_baseline_full_liquidity_detail_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260615_212551_baseline_full_liquidity_detail_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260615_212551_baseline_full_liquidity_detail_vol_position/trusted_strategy_market_environment.csv`
