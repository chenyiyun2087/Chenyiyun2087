# 可信策略生产候选名单

## 口径

- 策略：`生产治理波动仓位策略`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认50%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_weak`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-20`；候选数：Top 5。
- 执行层：目标资金比例 `50%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

- 候选组合触发 NAV 风险硬门禁：candidate:missing_theme:688498; candidate:missing_theme:600900; candidate:missing_theme:601668; candidate:missing_theme:600519; candidate:missing_theme:601318; candidate:theme:0.50000000>0.40000000

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | candidate_pool    | candidate_pool_role   | market_regime   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy                           |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:------------------|:----------------------|:----------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:--------------------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-07-20    | production_governed_vol_position |   688498 | 源杰科技   | 半导体        | 半导体            | liquidity_quality | champion_core         | risk_off        | liquidity_detail_score |      93.2579 | 4.08%              | 2.33%             | 50.00%                  |        1403.42 |   41.23 |                    nan |                       nan |                  93.2579 |         29.74 |        48.81 |  26.05 |             87.0331 |               79.6981 |             97.8711 |              95.6261 |         48.72 |                 0 | BASE        | 可买              |                 0.880964 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0678805 |    -0.255875  |
|      2 | 2026-07-20    | production_governed_vol_position |   600900 | 长江电力   | 水力发电       | 水力发电           | liquidity_quality | champion_core         | risk_off        | liquidity_detail_score |      92.7116 | 12.65%             | 8.13%             | 50.00%                  |          28.98 |   70.77 |                    nan |                       nan |                  92.7116 |         29.27 |        98.08 |  92.74 |             92.6456 |               88.175  |             99.4775 |              70.0793 |         71.77 |                 0 | CORE        | 可买              |                 0.880964 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.019456  |     0         |
|      3 | 2026-07-20    | production_governed_vol_position |   601668 | 中国建筑   | 建筑工程       | 建筑工程           | liquidity_quality | champion_core         | risk_off        | liquidity_detail_score |      92.6422 | 12.16%             | 8.84%             | 50.00%                  |           4.65 |   52.68 |                    nan |                       nan |                  92.6422 |         26.41 |        48.81 |  79.94 |             96.2454 |               91.4457 |             98.8194 |              96.4002 |         62.74 |                 0 | BASE        | 可买              |                 0.880964 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0178868 |    -0.0148305 |
|      4 | 2026-07-20    | production_governed_vol_position |   600519 | 贵州茅台   | 白酒         | 白酒             | liquidity_quality | champion_core         | risk_off        | liquidity_detail_score |      92.4131 | 10.18%             | 6.35%             | 50.00%                  |        1327.5  |   72.87 |                    nan |                       nan |                  92.4131 |         29.56 |        99.65 |  92.05 |             97.7743 |               90.42   |             99.7871 |              49.1388 |         71.43 |                 0 | CORE        | 可买              |                 0.880964 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0248922 |     0         |
|      5 | 2026-07-20    | production_governed_vol_position |   601318 | 中国平安   | 保险         | 保险             | liquidity_quality | champion_core         | risk_off        | liquidity_detail_score |      91.7283 | 10.94%             | 6.89%             | 50.00%                  |          53.23 |   52.16 |                    nan |                       nan |                  91.7283 |         29.5  |        98.8  |  85.33 |             94.6003 |               80.4529 |             99.5936 |              64.6797 |         66.52 |                 1 | WATCH       | 可买              |                 0.880964 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0229463 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260720_220957_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260720_220957_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260720_220957_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260720_220957_production_governed_vol_position/trusted_strategy_market_environment.csv`
