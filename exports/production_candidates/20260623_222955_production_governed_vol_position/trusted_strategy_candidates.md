# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量防守策略（市场门禁）`；近期冠军：`流动性质量防守策略（市场门禁）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-06-23`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate |   pool_type | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy              |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|------------:|:----------------|-------------------------:|:--------------------------|:---------------|:-------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-06-23    | production_governed_vol_position |   002008 | 大族激光   | 专用机械       | 专用机械           | liquidity_detail_score |      94.7163 | 10.00%             | 20.00%            | 50.00%                  |         145.11 |   46.81 |                    nan |                       nan |                  94.7163 |         29.74 |        48.63 |  83.01 |             96.048  |               95.8156 |             99.2832 |              65.8853 |         57.33 |                 1 |         nan | 过滤              |                  1.12991 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0456796 |     0         |
|      2 | 2026-06-23    | production_governed_vol_position |   600392 | 盛和资源   | 小金属        | 小金属            | liquidity_detail_score |      94.4227 | 10.00%             | 20.00%            | 50.00%                  |          31.03 |   61.06 |                    nan |                       nan |                  94.4227 |         29.15 |        48.63 |  96.92 |             95.7381 |               99.4188 |             97.6366 |              68.5006 |         59.96 |                 0 |         nan | 过滤              |                  1.12991 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0525338 |    -0.100058  |
|      3 | 2026-06-23    | production_governed_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | liquidity_detail_score |      94.3953 | 10.00%             | 20.00%            | 50.00%                  |          90.78 |   60.52 |                    nan |                       nan |                  94.3953 |         29.8  |        48.63 |  99.55 |             80.8989 |               91.4568 |             98.8377 |              99.3801 |         53.01 |                 0 |         nan | 过滤              |                  1.12991 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0881344 |    -0.0821031 |
|      4 | 2026-06-23    | production_governed_vol_position |   300857 | 协创数据   | IT设备       | IT设备           | liquidity_detail_score |      94.2098 | 10.00%             | 20.00%            | 50.00%                  |         306.5  |   60.41 |                    nan |                       nan |                  94.2098 |         29.61 |        48.63 |  92.6  |             87.5823 |               94.6532 |             98.8183 |              81.9256 |         61.62 |                 0 |         nan | 可买              |                  1.12991 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.0566546 |    -0.0325432 |
|      5 | 2026-06-23    | production_governed_vol_position |   603986 | 兆易创新   | 半导体        | 半导体            | liquidity_detail_score |      93.5405 | 10.00%             | 20.00%            | 50.00%                  |         640.99 |   66.92 |                    nan |                       nan |                  93.5405 |         29.99 |        48.63 |  95.54 |             83.301  |               80.7826 |             99.7482 |              98.1403 |         65.77 |                 0 |         nan | 可买              |                  1.12991 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail |                     0.5 | 0.055135  |    -0.0706249 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_222955_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_222955_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_222955_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_222955_production_governed_vol_position/trusted_strategy_market_environment.csv`
