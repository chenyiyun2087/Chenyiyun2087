# 可信策略生产候选名单

## 口径

- 策略：`production_governed_vol_position`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`recent_champion`；底层策略：`流动性质量稳健策略（波动仓位）`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_neutral`；行业状态：`normal`；周切换：`锁定`；目标仓位：`50%`；原因：`recent_champion_default_3m_return_priority`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
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

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate |   pool_type | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy                           |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|------------:|:----------------|-------------------------:|:--------------------------|:---------------|:--------------------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-06-18    | production_governed_vol_position |   300285 | 国瓷材料   | 陶瓷         | 陶瓷             | liquidity_detail_score |      96.0185 | 6.44%              | 1.81%             | 50.00%                  |          89.53 |   64.71 |                    nan |                       nan |                  96.0185 |         29.78 |        99.53 |  99.81 |             93.6009 |               89.7227 |             98.6426 |              93.3682 |         59.79 |                 0 |         nan | 过滤              |                  1.10553 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0874989 |             0 |
|      2 | 2026-06-18    | production_governed_vol_position |   301217 | 铜冠铜箔   | 元器件        | 元器件            | liquidity_detail_score |      94.6819 | 8.41%              | 2.36%             | 50.00%                  |         200    |   63.53 |                    nan |                       nan |                  94.6819 |         29.52 |        96.3  |  99.96 |             92.7089 |               96.3351 |             96.8586 |              78.0105 |         59.64 |                 0 |         nan | 过滤              |                  1.10553 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0670174 |             0 |
|      3 | 2026-06-18    | production_governed_vol_position |   600392 | 盛和资源   | 小金属        | 小金属            | liquidity_detail_score |      94.524  | 13.18%             | 3.70%             | 50.00%                  |          31.66 |   72.04 |                    nan |                       nan |                  94.524  |         28.79 |        99.05 |  96.47 |             99.1662 |               99.5734 |             97.9833 |              66.7054 |         61.17 |                 1 |         nan | 过滤              |                  1.10553 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0427567 |             0 |
|      4 | 2026-06-18    | production_governed_vol_position |   300857 | 协创数据   | IT设备       | IT设备           | liquidity_detail_score |      94.1957 | 11.66%             | 3.27%             | 50.00%                  |         277.76 |   46    |                    nan |                       nan |                  94.1957 |         29.58 |        47.99 |  86.25 |             92.069  |               88.0939 |             99.0498 |              82.7031 |         61.72 |                 0 |         nan | 可买              |                  1.10553 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0483345 |             0 |
|      5 | 2026-06-18    | production_governed_vol_position |   600378 | 昊华科技   | 化工原料       | 化工原料           | liquidity_detail_score |      93.9389 | 10.30%             | 2.79%             | 50.00%                  |          65.16 |   64.36 |                    nan |                       nan |                  93.9389 |         29.29 |        96.86 |  99.24 |             94.3378 |               97.4016 |             97.0719 |              68.47   |         69.91 |                 0 |         nan | 可买              |                  1.10553 | normal_liquidity          | index_neutral  | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0567368 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_110412_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_110412_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_110412_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260623_110412_production_governed_vol_position/trusted_strategy_market_environment.csv`
