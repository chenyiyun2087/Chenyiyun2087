# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-13`；候选数：Top 5。
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
|      1 | 2026-05-13    | baseline_full_dynamic_factor_industry_cap2 |   301308 | 江波龙    | 半导体        | 半导体            | dynamic_factor_score |      99.3009 | 20.00%             | 20.00%            | 100.00%                 |         611.9  |   72.84 |                99.3009 |                   99.6779 |                  94.1064 |         29.87 |        99.21 |  99.05 |             88.3094 |               87.3401 |             98.0613 |              88.0768 |         69.31 |                 0 |             | 可买              |                  1.18706 | normal_liquidity          | index_strong   | 0.0650247 |             0 |
|      2 | 2026-05-13    | baseline_full_dynamic_factor_industry_cap2 |   301217 | 铜冠铜箔   | 元器件        | 元器件            | dynamic_factor_score |      98.8583 | 20.00%             | 20.00%            | 100.00%                 |          99.01 |   77.1  |                98.8583 |                   98.2588 |                  89.533  |         29.34 |        96.18 |  99.88 |             84.0248 |               79.1004 |             90.8104 |              81.2136 |         72.14 |                 0 |             | 可买              |                  1.18706 | normal_liquidity          | index_strong   | 0.0672941 |             0 |
|      3 | 2026-05-13    | baseline_full_dynamic_factor_industry_cap2 |   600584 | 长电科技   | 半导体        | 半导体            | dynamic_factor_score |      98.857  | 20.00%             | 20.00%            | 100.00%                 |          57.49 |   71.53 |                98.857  |                   99.0779 |                  95.4133 |         29.62 |        92.65 |  91.92 |             95.9287 |               96.1031 |             98.5266 |              75.3974 |         56.76 |                 1 |             | 过滤              |                  1.18706 | normal_liquidity          | index_strong   | 0.0314141 |             0 |
|      4 | 2026-05-13    | baseline_full_dynamic_factor_industry_cap2 |   300433 | 蓝思科技   | 元器件        | 元器件            | dynamic_factor_score |      98.2737 | 20.00%             | 20.00%            | 100.00%                 |          32.69 |   74.53 |                98.2737 |                   97.7533 |                  88.6309 |         29.16 |        97.65 |  61.96 |             91.7991 |               82.9391 |             95.444  |              46.3358 |         65.82 |                 0 |             | 可买              |                  1.18706 | normal_liquidity          | index_strong   | 0.0459443 |             0 |
|      5 | 2026-05-13    | baseline_full_dynamic_factor_industry_cap2 |   301511 | 德福科技   | 电气设备       | 电气设备           | dynamic_factor_score |      98.1315 | 20.00%             | 20.00%            | 100.00%                 |         102.16 |   72.02 |                98.1315 |                   98.0261 |                  88.7359 |         29.27 |        97.5  |  99.9  |             75.1842 |               78.0341 |             88.7359 |              96.5684 |         69.35 |                 0 |             | 可买              |                  1.18706 | normal_liquidity          | index_strong   | 0.0588286 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_212639_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_212639_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_212639_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_212639_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
