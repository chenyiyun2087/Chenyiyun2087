# 可信策略生产候选名单

## 口径

- 策略：`流动性分层B点进攻策略`，排序字段：`bs_score_v2`。
- 策略ID：`tiered_liquidity_then_bs_v2`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。


- 信号日：`2026-06-18`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                    |   symbol | name   | industry   | industry_key   | sort_col    |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:----------------------------|---------:|:-------|:-----------|:---------------|:------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|------------------------:|----------:|--------------:|
|      1 | 2026-06-18    | tiered_liquidity_then_bs_v2 |   002741 | 光华科技   | 化工原料       | 化工原料           | bs_score_v2 |        74.66 | 10.00%             | 20.00%            | 50.00%                  |          41.3  |   80.76 |                    nan |                       nan |                  81.7799 |         27.75 |        98.74 |  99.13 |             98.7008 |               69.2651 |             96.3739 |              1.93911 |         74.66 |                 0 | nan         | 可买              |                  1.10597 | normal_liquidity          | index_neutral  |                     0.5 | 0.0612897 |             0 |
|      2 | 2026-06-18    | tiered_liquidity_then_bs_v2 |   300373 | 扬杰科技   | 半导体        | 半导体            | bs_score_v2 |        74.11 | 10.00%             | 20.00%            | 50.00%                  |         123.13 |   78.47 |                    nan |                       nan |                  88.307  |         29.16 |        98.49 |  98.39 |             84.7586 |               66.5115 |             93.8724 |             84.1768  |         74.11 |                 0 | nan         | 可买              |                  1.10597 | normal_liquidity          | index_neutral  |                     0.5 | 0.0690405 |             0 |
|      3 | 2026-06-18    | tiered_liquidity_then_bs_v2 |   300260 | 新莱应材   | 机械基件       | 机械基件           | bs_score_v2 |        73.73 | 10.00%             | 20.00%            | 50.00%                  |          82.97 |   78.96 |                    nan |                       nan |                  82.2754 |         28.23 |        96.24 |  95.15 |             84.5841 |               76.5174 |             90.731  |             26.3137  |         73.73 |                 0 | nan         | 可买              |                  1.10597 | normal_liquidity          | index_neutral  |                     0.5 | 0.0694892 |             0 |
|      4 | 2026-06-18    | tiered_liquidity_then_bs_v2 |   002600 | 领益智造   | 元器件        | 元器件            | bs_score_v2 |        73.26 | 10.00%             | 20.00%            | 50.00%                  |          16.82 |   60.59 |                    nan |                       nan |                  87.4851 |         29.23 |        96.26 |  86.35 |             93.5815 |               70.7776 |             96.8974 |             46.4417  |         73.26 |                 1 | TRADE       | 可买              |                  1.10597 | normal_liquidity          | index_neutral  |                     0.5 | 0.0417392 |             0 |
|      5 | 2026-06-18    | tiered_liquidity_then_bs_v2 |   002436 | 兴森科技   | 元器件        | 元器件            | bs_score_v2 |        72.49 | 10.00%             | 20.00%            | 50.00%                  |          52.06 |   79.86 |                    nan |                       nan |                  89.5227 |         29.77 |        99.53 |  98.24 |             90.5177 |               81.753  |             97.6149 |             48.2063  |         72.49 |                 0 | nan         | 可买              |                  1.10597 | normal_liquidity          | index_neutral  |                     0.5 | 0.0530768 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260621_052552_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260621_052552_tiered_liquidity_then_bs_v2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260621_052552_tiered_liquidity_then_bs_v2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260621_052552_tiered_liquidity_then_bs_v2/trusted_strategy_market_environment.csv`
