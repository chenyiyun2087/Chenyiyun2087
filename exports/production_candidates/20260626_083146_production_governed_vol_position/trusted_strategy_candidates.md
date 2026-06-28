# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-06-25`；候选数：Top 5。
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
|      1 | 2026-06-25    | production_governed_vol_position |   000021 | 深科技    | 元器件        | 元器件            | generic          | research              | neutral         | liquidity_detail_score |      95.0552 | 10.00%             | 20.00%            | 50.00%                  |          53.2  |   69.73 |                    nan |                       nan |                  95.0552 |         29.7  |        98.97 |  92.08 |             91.2841 |               83.5948 |             99.4577 |              97.4046 |         61.94 |                 0 | SCAN        | 观察              |                  1.17228 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0527785 |    0          |
|      2 | 2026-06-25    | production_governed_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | generic          | research              | neutral         | liquidity_detail_score |      94.9593 | 10.00%             | 20.00%            | 50.00%                  |          98.4  |   67.29 |                    nan |                       nan |                  94.9593 |         29.83 |        48.13 |  99.79 |             87.8172 |               86.2289 |             98.1406 |              99.6707 |         55.86 |                 0 | SCAN        | 过滤              |                  1.17228 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0867044 |   -0.00505561 |
|      3 | 2026-06-25    | production_governed_vol_position |   688110 | 东芯股份   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.7893 | 10.00%             | 20.00%            | 50.00%                  |         179.45 |   68.12 |                    nan |                       nan |                  94.7893 |         29.12 |        98.16 |  92.48 |             92.7755 |               90.3157 |             96.8817 |              93.2791 |         65.46 |                 0 | SCAN        | 可买              |                  1.17228 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0502143 |    0          |
|      4 | 2026-06-25    | production_governed_vol_position |   688521 | 芯原股份   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.7499 | 10.00%             | 20.00%            | 50.00%                  |         338.05 |   69.89 |                    nan |                       nan |                  94.7499 |         29.58 |        99.51 |  95.74 |             92.6206 |               88.2433 |             96.6686 |              90.49   |         67.96 |                 0 | SCAN        | 可买              |                  1.17228 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0539923 |    0          |
|      5 | 2026-06-25    | production_governed_vol_position |   603986 | 兆易创新   | 半导体        | 半导体            | generic          | research              | neutral         | liquidity_detail_score |      94.5451 | 10.00%             | 20.00%            | 50.00%                  |         775.21 |   63.66 |                    nan |                       nan |                  94.5451 |         29.99 |        99.51 |  97.89 |             84.1178 |               86.3064 |             99.7288 |              98.2956 |         61.16 |                 0 | SCAN        | 过滤              |                  1.17228 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0605656 |    0          |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260626_083146_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260626_083146_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260626_083146_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260626_083146_production_governed_vol_position/trusted_strategy_market_environment.csv`
