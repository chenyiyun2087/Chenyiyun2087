# 可信策略生产候选名单

## 口径

- 策略：`市场风格自适应生产策略`，排序字段：`adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score`。
- 策略ID：`adaptive_market_style`。
- 风险档位：`adaptive`；自适应档：按T日市场风格在进攻/均衡/防守策略间切换，并动态调整50%-100%仓位。
- 市场风格：`balanced`；底层策略：`流动性质量防守策略（市场门禁）`；目标仓位：`80%`；原因：`balanced_rolling_performance_leads`。
- 信号日：`2026-06-02`；候选数：Top 5。
- 执行层：目标资金比例 `100%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

- 组合有效仓位为 80.00%，请确认是否由市场门禁或风格状态降仓触发。
- 未找到信号日动态权重记录，动态排序可能退化为等权因子。

## 候选明细

|   rank | signal_date   | strategy              |   symbol | name     | industry   | industry_key   | sort_col                                                                   |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | market_style_state   | selected_strategy                          |   target_position_ratio | style_reason                       |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:----------------------|---------:|:---------|:-----------|:---------------|:---------------------------------------------------------------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:---------------------|:-------------------------------------------|------------------------:|:-----------------------------------|----------:|--------------:|
|      1 | 2026-06-02    | adaptive_market_style |   600183 | 生益科技 | 元器件     | 元器件         | adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score |      96.5002 | 16.00%             | 20.00%            | 80.00%                  |         141.07 |   67.13 |                    nan |                       nan |                  96.5002 |         29.81 |        49.02 |  99.61 |             95.3304 |               97.6167 |             99.225  |              81.6121 |         66.6  |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | balanced             | baseline_full_liquidity_detail_market_gate |                     0.8 | balanced_rolling_performance_leads | 0.0448641 |      0        |
|      2 | 2026-06-02    | adaptive_market_style |   300620 | 光库科技 | 通信设备   | 通信设备       | adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score |      95.5255 | 16.00%             | 20.00%            | 80.00%                  |         320.02 |   74.33 |                    nan |                       nan |                  95.5255 |         29.56 |        99.53 |  96.32 |             95.0397 |               90.3507 |             97.7717 |              88.8587 |         70.01 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | balanced             | baseline_full_liquidity_detail_market_gate |                     0.8 | balanced_rolling_performance_leads | 0.0581968 |      0        |
|      3 | 2026-06-02    | adaptive_market_style |   688498 | 源杰科技 | 半导体     | 半导体         | adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score |      95.2009 | 16.00%             | 20.00%            | 80.00%                  |        1206.91 |   40.13 |                    nan |                       nan |                  95.2009 |         29.69 |        49.02 |   4.94 |             92.8502 |               85.5067 |             98.043  |              95.1172 |         46.27 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | balanced             | baseline_full_liquidity_detail_market_gate |                     0.8 | balanced_rolling_performance_leads | 0.16955   |     -0.302767 |
|      4 | 2026-06-02    | adaptive_market_style |   300408 | 三环集团 | 元器件     | 元器件         | adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score |      94.97   | 16.00%             | 20.00%            | 80.00%                  |         134.46 |   65.52 |                    nan |                       nan |                  94.97   |         29.66 |        49.02 |  98.74 |             96.2023 |               97.6942 |             98.3143 |              67.8163 |         66.36 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | balanced             | baseline_full_liquidity_detail_market_gate |                     0.8 | balanced_rolling_performance_leads | 0.0490663 |      0        |
|      5 | 2026-06-02    | adaptive_market_style |   600036 | 招商银行 | 银行       | 银行           | adaptive:baseline_full_liquidity_detail_market_gate:liquidity_detail_score |      94.6292 | 16.00%             | 20.00%            | 80.00%                  |          38.8  |   53.66 |                    nan |                       nan |                  94.6292 |         29.19 |        98.31 |  74.4  |             92.0752 |               87.3087 |             99.6125 |              92.5596 |         63.11 |                 0 |             | 可买            |                 0.888244 | normal_liquidity          | index_neutral  | balanced             | baseline_full_liquidity_detail_market_gate |                     0.8 | balanced_rolling_performance_leads | 0.0072641 |      0        |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_110839_adaptive_market_style/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_110839_adaptive_market_style/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_110839_adaptive_market_style/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_110839_adaptive_market_style/trusted_strategy_market_environment.csv`
