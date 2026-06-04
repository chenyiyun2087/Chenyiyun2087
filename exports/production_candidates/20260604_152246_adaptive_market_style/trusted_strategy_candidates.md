# 可信策略生产候选名单

## 口径

- 策略：`市场风格自适应生产策略`，排序字段：`adaptive:baseline_full_liquidity:s_liquidity`。
- 策略ID：`adaptive_market_style`。
- 风险档位：`adaptive`；自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`锁定`；目标仓位：`50%`；原因：`hold_min_state_5_days`。
- 信号日：`2026-06-03`；候选数：Top 5。
- 执行层：目标资金比例 `100%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

- 组合有效仓位为 50.00%，请确认是否由市场门禁或风格状态降仓触发。
- 未找到信号日动态权重记录，动态排序可能退化为等权因子。

## 候选明细

|   rank | signal_date   | strategy              |   symbol | name     | industry   | industry_key   | sort_col                                     |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | market_style_state   | selected_strategy       | recent_champion_strategy                    |   champion_score |   weekly_switch_allowed | market_state   | industry_state   |   target_position_ratio | style_reason          | switch_reason         |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:----------------------|---------:|:---------|:-----------|:---------------|:---------------------------------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:---------------------|:------------------------|:--------------------------------------------|-----------------:|------------------------:|:---------------|:-----------------|------------------------:|:----------------------|:----------------------|----------:|--------------:|
|      1 | 2026-06-03    | adaptive_market_style |   300308 | 中际旭创 | 通信设备   | 通信设备       | adaptive:baseline_full_liquidity:s_liquidity |        30    | 10.00%             | 20.00%            | 50.00%                  |        1275    |   74.19 |                    nan |                       nan |                  95.9129 |         30    |        99.54 |  98.3  |             92.3911 |               86.9894 |             99.8064 |              94.153  |         69.76 |                 0 |             | 可买            |                 0.997168 | normal_liquidity          | index_neutral  | defensive            | baseline_full_liquidity | baseline_full_liquidity_detail_vol_position |          8.06964 |                       0 | index_neutral  | normal           |                     0.5 | hold_min_state_5_days | hold_min_state_5_days | 0.0381538 |     0         |
|      2 | 2026-06-03    | adaptive_market_style |   300502 | 新易盛   | 通信设备   | 通信设备       | adaptive:baseline_full_liquidity:s_liquidity |        29.99 | 10.00%             | 20.00%            | 50.00%                  |         782.25 |   76.55 |                    nan |                       nan |                  91.8502 |         29.99 |        99.3  |  98.35 |             89.332  |               75.0823 |             99.7677 |              77.696  |         71.17 |                 0 |             | 可买            |                 0.997168 | normal_liquidity          | index_neutral  | defensive            | baseline_full_liquidity | baseline_full_liquidity_detail_vol_position |          8.06964 |                       0 | index_neutral  | normal           |                     0.5 | hold_min_state_5_days | hold_min_state_5_days | 0.04625   |     0         |
|      3 | 2026-06-03    | adaptive_market_style |   603986 | 兆易创新 | 半导体     | 半导体         | adaptive:baseline_full_liquidity:s_liquidity |        29.99 | 10.00%             | 20.00%            | 50.00%                  |         492.23 |   58.83 |                    nan |                       nan |                  92.9567 |         29.99 |        49.04 |  97.73 |             83.7754 |               78.9545 |             99.7483 |              94.0949 |         65.03 |                 0 |             | 可买            |                 0.997168 | normal_liquidity          | index_neutral  | defensive            | baseline_full_liquidity | baseline_full_liquidity_detail_vol_position |          8.06964 |                       0 | index_neutral  | normal           |                     0.5 | hold_min_state_5_days | hold_min_state_5_days | 0.0496942 |    -0.0632347 |
|      4 | 2026-06-03    | adaptive_market_style |   688008 | 澜起科技 | 半导体     | 半导体         | adaptive:baseline_full_liquidity:s_liquidity |        29.98 | 10.00%             | 20.00%            | 50.00%                  |         253.9  |   65.27 |                    nan |                       nan |                  84.1147 |         29.98 |        49.04 |  95.28 |             80.2904 |               49.5257 |             99.5934 |              57.1539 |         65.56 |                 0 |             | 可买            |                 0.997168 | normal_liquidity          | index_neutral  | defensive            | baseline_full_liquidity | baseline_full_liquidity_detail_vol_position |          8.06964 |                       0 | index_neutral  | normal           |                     0.5 | hold_min_state_5_days | hold_min_state_5_days | 0.0590676 |    -0.106238  |
|      5 | 2026-06-03    | adaptive_market_style |   688256 | 寒武纪-U | 半导体     | 半导体         | adaptive:baseline_full_liquidity:s_liquidity |        29.98 | 10.00%             | 20.00%            | 50.00%                  |        1378.1  |   39.92 |                    nan |                       nan |                  87.1824 |         29.98 |        49.04 |   4.72 |             79.9226 |               55.3533 |             99.3998 |              80.1162 |         46.38 |                 0 |             | 可买            |                 0.997168 | normal_liquidity          | index_neutral  | defensive            | baseline_full_liquidity | baseline_full_liquidity_detail_vol_position |          8.06964 |                       0 | index_neutral  | normal           |                     0.5 | hold_min_state_5_days | hold_min_state_5_days | 0.183439  |    -0.262259  |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_152246_adaptive_market_style/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_152246_adaptive_market_style/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_152246_adaptive_market_style/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260604_152246_adaptive_market_style/trusted_strategy_market_environment.csv`
