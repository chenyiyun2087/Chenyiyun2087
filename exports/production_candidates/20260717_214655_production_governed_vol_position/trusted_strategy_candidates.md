# 可信策略生产候选名单

## 口径

- 策略：`生产治理波动仓位策略`，排序字段：`production_governed_vol_position:baseline_full_liquidity_detail_vol_position:liquidity_detail_score`。
- 策略ID：`production_governed_vol_position`。
- 风险档位：`adaptive`；收益优先主推送档：使用当前生产主策略作为进攻引擎，默认70%仓位；adaptive_market_style v2.2 保留为风控锚与仓位治理依据。
- 市场风格：`defensive`；底层策略：`纯流动性防守策略`；近期冠军：`流动性质量稳健策略（波动仓位）`；市场状态：`index_weak`；行业状态：`normal`；周切换：`允许`；目标仓位：`50%`；原因：`defensive_weak_market_or_attack_industry_risk`。
- Adaptive 版本：`v2.2`；AShare 权重：`None`；放权档位：`None`；补位上限：`None`。
- 信号日：`2026-07-17`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                         |   symbol | name   | industry   | industry_key   | candidate_pool    | candidate_pool_role   | market_regime   | sort_col               |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   | selected_strategy                           |   target_position_ratio |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:---------------------------------|---------:|:-------|:-----------|:---------------|:------------------|:----------------------|:----------------|:-----------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|:--------------------------------------------|------------------------:|----------:|--------------:|
|      1 | 2026-07-17    | production_governed_vol_position |   600900 | 长江电力   | 水力发电       | 水力发电           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      93.5848 | 16.98%             | 11.95%            | 50.00%                  |        27.2071 |   58.72 |                    nan |                       nan |                  93.5848 |         29.2  |        49.57 |  84.28 |             94.9264 |               89.6011 |             99.5933 |              72.8699 |         65.46 |                 0 | SCAN        | 可买              |                 0.851508 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0132305 |    -0.0513563 |
|      2 | 2026-07-17    | production_governed_vol_position |   688347 | 华虹公司   | 半导体        | 半导体            | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.9028 | 4.36%              | 2.49%             | 50.00%                  |       309      |   46.32 |                    nan |                       nan |                  91.9028 |         29.73 |        49.57 |  95.68 |             82.1069 |               75.3098 |             98.8768 |              97.134  |         57.28 |                 0 | BASE        | 可买              |                 0.851508 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0635542 |    -0.228464  |
|      3 | 2026-07-17    | production_governed_vol_position |   300759 | 康龙化成   | 化学制药       | 化学制药           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.8382 | 6.30%              | 3.39%             | 50.00%                  |        35.48   |   64.3  |                    nan |                       nan |                  91.8382 |         28.8  |        49.57 |  99.85 |             94.3842 |               97.3664 |             98.0442 |              52.4981 |         66.98 |                 0 | SCAN        | 可买              |                 0.851508 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.046588  |    -0.119603  |
|      4 | 2026-07-17    | production_governed_vol_position |   300308 | 中际旭创   | 通信设备       | 通信设备           | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.7554 | 5.36%              | 3.34%             | 50.00%                  |       979.46   |   41.33 |                    nan |                       nan |                  91.7554 |         30    |        49.57 |  15.28 |             85.9992 |               69.7328 |             99.8064 |              91.2471 |         47.05 |                 1 | BASE        | 可买              |                 0.851508 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0473408 |    -0.291443  |
|      5 | 2026-07-17    | production_governed_vol_position |   601398 | 工商银行   | 银行         | 银行             | liquidity_quality | champion_core         | neutral         | liquidity_detail_score |      91.1105 | 17.00%             | 10.70%            | 50.00%                  |         7.57   |   73.65 |                    nan |                       nan |                  91.1105 |         29    |        99.17 |  90.92 |             93.2998 |               85.9024 |             99.574  |              59.6243 |         69.54 |                 1 | WATCH       | 可买              |                 0.851508 | normal_liquidity          | index_weak     | baseline_full_liquidity_detail_vol_position |                     0.5 | 0.0147773 |     0         |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260717_214655_production_governed_vol_position/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260717_214655_production_governed_vol_position/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260717_214655_production_governed_vol_position/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260717_214655_production_governed_vol_position/trusted_strategy_market_environment.csv`
