# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-06-24`；候选数：Top 5。
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
|      1 | 2026-06-24    | production_governed_vol_position |   002008 | 大族激光   | 专用机械       | 专用机械           | generic          | research              | neutral         | liquidity_detail_score |      95.2821 | 10.00%             | 20.00%            | 50.00%                  |         152.23 |   50.29 |                    nan |                       nan |                  95.2821 |         29.76 |        98.84 |  90.01 |             95.8761 |               97.3669 |             98.7028 |              70.1646 |         69.93 |                 0 | BASE        | 可买              |                  1.07427 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0452662 |     0         |
|      2 | 2026-06-24    | production_governed_vol_position |   603986 | 兆易创新   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      95.19   | 10.00%             | 20.00%            | 50.00%                  |         705.09 |   51.13 |                    nan |                       nan |                  95.19   |         29.99 |        97.68 |  96.44 |             87.667  |               85.9245 |             99.7289 |              98.2188 |         55.65 |                 0 | BASE        | 过滤              |                  1.07427 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0585238 |     0         |
|      3 | 2026-06-24    | production_governed_vol_position |   300857 | 协创数据   | IT设备       | IT设备           | generic          | research              | neutral         | liquidity_detail_score |      94.987  | 10.00%             | 20.00%            | 50.00%                  |         340.19 |   63.39 |                    nan |                       nan |                  94.987  |         29.62 |        99.11 |  96.17 |             91.2101 |               95.3146 |             98.0833 |              82.4201 |         68.88 |                 0 | SCAN        | 可买              |                  1.07427 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0600366 |     0         |
|      4 | 2026-06-24    | production_governed_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | generic          | research              | neutral         | liquidity_detail_score |      94.4192 | 10.00%             | 20.00%            | 50.00%                  |          91.72 |   61.04 |                    nan |                       nan |                  94.4192 |         29.82 |        48.19 |  99.69 |             81.7425 |               89.6225 |             99.2449 |              99.8064 |         55.6  |                 0 | SCAN        | 过滤              |                  1.07427 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0871073 |    -0.0725986 |
|      5 | 2026-06-24    | production_governed_vol_position |   300373 | 扬杰科技   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.1792 | 10.00%             | 20.00%            | 50.00%                  |         132.99 |   65.85 |                    nan |                       nan |                  94.1792 |         29.2  |        48.19 |  96.73 |             90.8616 |               88.6931 |             96.728  |              92.6041 |         63.21 |                 0 | SCAN        | 可买              |                  1.07427 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0635102 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260628_082511_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260628_082511_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260628_082511_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260628_082511_production_governed_vol_position/trusted_strategy_market_environment.csv`
