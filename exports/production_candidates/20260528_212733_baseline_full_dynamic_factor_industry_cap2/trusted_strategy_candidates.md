# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-28`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name   | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:-------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-05-28    | baseline_full_dynamic_factor_industry_cap2 |   002185 | 华天科技   | 半导体        | 半导体            | dynamic_factor_score |      99.4162 | 20.00%             | 20.00%            | 100.00%                 |          21.49 |   80.57 |                99.4162 |                   99.4098 |                  92.6709 |         29.71 |        98.99 |  98.86 |             99.4575 |               98.8762 |             99.535  |              34.0438 |         62.42 |                 1 |             | 过滤              |                 0.960492 | normal_liquidity          | index_neutral  | 0.0413658 |             0 |
|      2 | 2026-05-28    | baseline_full_dynamic_factor_industry_cap2 |   301217 | 铜冠铜箔   | 元器件        | 元器件            | dynamic_factor_score |      99.329  | 20.00%             | 20.00%            | 100.00%                 |         119.08 |   80.74 |                99.329  |                   99.5367 |                  91.1561 |         29.44 |        99.66 |  99.88 |             90.7382 |               69.1726 |             93.2765 |              93.8772 |         66.44 |                 0 |             | 过滤              |                 0.960492 | normal_liquidity          | index_neutral  | 0.0822994 |             0 |
|      3 | 2026-05-28    | baseline_full_dynamic_factor_industry_cap2 |   301511 | 德福科技   | 电气设备       | 电气设备           | dynamic_factor_score |      99.1772 | 20.00%             | 20.00%            | 100.00%                 |         119.52 |   79.06 |                99.1772 |                   99.3264 |                  92.585  |         29.4  |        99.09 |  99.26 |             92.3658 |               74.7723 |             94.1097 |              95.7954 |         65.15 |                 0 |             | 过滤              |                 0.960492 | normal_liquidity          | index_neutral  | 0.0711215 |             0 |
|      4 | 2026-05-28    | baseline_full_dynamic_factor_industry_cap2 |   300308 | 中际旭创   | 通信设备       | 通信设备           | dynamic_factor_score |      99.1256 | 20.00%             | 20.00%            | 100.00%                 |        1197.99 |   77.87 |                99.1256 |                   98.7265 |                  92.7698 |         30    |        98.95 |  95.85 |             86.9018 |               73.5516 |             99.7675 |              93.9159 |         72.35 |                 0 |             | 可买              |                 0.960492 | normal_liquidity          | index_neutral  | 0.0347432 |             0 |
|      5 | 2026-05-28    | baseline_full_dynamic_factor_industry_cap2 |   600396 | 华电辽能   | 火力发电       | 火力发电           | dynamic_factor_score |      98.9876 | 20.00%             | 20.00%            | 100.00%                 |          19.26 |   82.4  |                98.9876 |                   99.1894 |                  87.5712 |         29.41 |        98.86 |  99.59 |             94.2453 |               80.9533 |             97.4036 |              27.5528 |         73.44 |                 0 |             | 可买              |                 0.960492 | normal_liquidity          | index_neutral  | 0.066319  |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260528_212733_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260528_212733_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260528_212733_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260528_212733_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
