# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-06-30`；候选数：Top 5。
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
|      1 | 2026-06-30    | production_governed_vol_position |   000021 | 深科技    | 元器件        | 元器件            | generic          | research              | normal_risk_on  | liquidity_detail_score |      97.9961 | 10.00%             | 20.00%            | 50.00%                  |          64.27 |   70.96 |                    nan |                       nan |                  97.9961 |         29.71 |        99.48 |  98.72 |             96.8005 |               95.2298 |             98.9141 |              99.0111 |         55.15 |                 0 | CORE        | 过滤              |                  1.04681 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0479128 |     0         |
|      2 | 2026-06-30    | production_governed_vol_position |   688110 | 东芯股份   | 半导体        | 半导体            | generic          | research              | normal_risk_on  | liquidity_detail_score |      95.0852 | 10.00%             | 20.00%            | 50.00%                  |         203.7  |   61.76 |                    nan |                       nan |                  95.0852 |         29.24 |        96.74 |  98.14 |             89.3931 |               94.7256 |             96.083  |              95.986  |         63.28 |                 0 | SCAN        | 可买              |                  1.04681 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.039292  |     0         |
|      3 | 2026-06-30    | production_governed_vol_position |   688120 | 华海清科   | 半导体        | 半导体            | generic          | research              | normal_risk_on  | liquidity_detail_score |      94.9491 | 10.00%             | 20.00%            | 50.00%                  |         322.18 |   45.96 |                    nan |                       nan |                  94.9491 |         29.01 |        47.61 |  67.79 |             96.6259 |               87.4539 |             97.4985 |              92.0109 |         55.25 |                 0 | BASE        | 可买              |                  1.04681 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.128398  |    -0.0989691 |
|      4 | 2026-06-30    | production_governed_vol_position |   688012 | 中微公司   | 半导体        | 半导体            | generic          | research              | normal_risk_on  | liquidity_detail_score |      94.4519 | 10.00%             | 20.00%            | 50.00%                  |         468.5  |   49.3  |                    nan |                       nan |                  94.4519 |         29.81 |        97.01 |  98.55 |             91.8751 |               90.285  |             99.3795 |              78.8055 |         57.6  |                 1 | BASE        | 过滤              |                  1.04681 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0383584 |     0         |
|      5 | 2026-06-30    | production_governed_vol_position |   300223 | 北京君正   | 半导体        | 半导体            | generic          | research              | normal_risk_on  | liquidity_detail_score |      94.4187 | 10.00%             | 20.00%            | 50.00%                  |         248.9  |   67.19 |                    nan |                       nan |                  94.4187 |         29.74 |        47.61 |  98.6  |             91.6424 |               92.2629 |             98.3324 |              78.4759 |         67.91 |                 1 | WATCH       | 可买              |                  1.04681 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0636403 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_032712_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_032712_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_032712_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260701_032712_production_governed_vol_position/trusted_strategy_market_environment.csv`
