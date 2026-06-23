# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_strong`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-06-22`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate |   pool_type | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy                           |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|------------:|:----------------|-------------------------:|:--------------------------|:---------------|:--------------------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-06-22    | production_governed_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | liquidity_detail_score |      95.9174 | 7.10%              | 1.88%             | 50.00%                  |          98.9  |   71.59 |                    nan |                       nan |                  95.9174 |         29.8  |        99.29 |  99.71 |             88.6434 |               91.0078 |             98.9535 |              99.6124 |         58.97 |                 0 |         nan | 过滤              |                  1.23123 | high_liquidity            | index_strong   | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0839945 |        0      |
|      2 | 2026-06-22    | production_governed_vol_position |   301217 | 铜冠铜箔   | 元器件        | 元器件            | liquidity_detail_score |      95.7319 | 8.85%              | 2.34%             | 50.00%                  |         196.5  |   59.75 |                    nan |                       nan |                  95.7319 |         29.57 |        47.52 |  99.88 |             95.7946 |               97.1899 |             98.2171 |              78.3527 |         60.57 |                 0 |         nan | 可买              |                  1.23123 | high_liquidity            | index_strong   | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0674344 |       -0.0175 |
|      3 | 2026-06-22    | production_governed_vol_position |   600392 | 盛和资源   | 小金属        | 小金属            | liquidity_detail_score |      95.6301 | 13.07%             | 3.46%             | 50.00%                  |          34.48 |   72.79 |                    nan |                       nan |                  95.6301 |         29.03 |        99.29 |  97.69 |             99.2054 |               99.593  |             98.7984 |              73.2364 |         58.33 |                 1 |         nan | 过滤              |                  1.23123 | high_liquidity            | index_strong   | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.045637  |        0      |
|      4 | 2026-06-22    | production_governed_vol_position |   300857 | 协创数据   | IT设备       | IT设备           | liquidity_detail_score |      95.4957 | 10.62%             | 2.81%             | 50.00%                  |         316.81 |   51.27 |                    nan |                       nan |                  95.4957 |         29.6  |        99.29 |  91.76 |             95      |               93.469  |             98.0814 |              82.9651 |         64.69 |                 0 |         nan | 可买              |                  1.23123 | high_liquidity            | index_strong   | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0562    |        0      |
|      5 | 2026-06-22    | production_governed_vol_position |   600378 | 昊华科技   | 化工原料       | 化工原料           | liquidity_detail_score |      93.4308 | 10.36%             | 2.74%             | 50.00%                  |          70.22 |   67.75 |                    nan |                       nan |                  93.4308 |         29.35 |        98.24 |  99.55 |             88.7791 |               96.4341 |             97.5388 |              74.4574 |         67.89 |                 0 |         nan | 可买              |                  1.23123 | high_liquidity            | index_strong   | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0576091 |        0      |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_065545_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_065545_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_065545_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_065545_production_governed_vol_position/trusted_strategy_market_environment.csv`
