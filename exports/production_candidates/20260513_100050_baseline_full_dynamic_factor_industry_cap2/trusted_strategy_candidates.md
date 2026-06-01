# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-12`；候选数：Top 5。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name     | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:---------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-05-12    | baseline_full_dynamic_factor_industry_cap2 |   002281 | 光迅科技 | 通信设备   | 通信设备       | dynamic_factor_score |      99.5415 | 20.00%             | 20.00%            | 100.00%                 |         201.5  |   76.1  |                99.5415 |                   99.7348 |                  92.5653 |         29.91 |        95.46 |  99.4  |             87.4854 |               73.1859 |             98.9911 |             93.6166  |         71.07 |                 0 |             | 可买            |                  1.20945 | high_liquidity            | index_strong   | 0.0461777 |             0 |
|      2 | 2026-05-12    | baseline_full_dynamic_factor_industry_cap2 |   300395 | 菲利华   | 玻璃       | 玻璃           | dynamic_factor_score |      99.2079 | 20.00%             | 20.00%            | 100.00%                 |         148.16 |   77.33 |                99.2079 |                   98.8797 |                  93.2603 |         29.58 |        99.39 |  97.09 |             94.6643 |               83.6244 |             96.7792 |             78.2693  |         72.54 |                 0 |             | 可买            |                  1.20945 | high_liquidity            | index_strong   | 0.0496083 |             0 |
|      3 | 2026-05-12    | baseline_full_dynamic_factor_industry_cap2 |   600522 | 中天科技 | 通信设备   | 通信设备       | dynamic_factor_score |      98.9941 | 20.00%             | 20.00%            | 100.00%                 |          44.08 |   68.51 |                98.9941 |                   99.6796 |                  94.2861 |         29.88 |        46.69 |  94.63 |             93.7136 |               85.584  |             99.1075 |             79.9961  |         56.55 |                 0 |             | 过滤            |                  1.20945 | high_liquidity            | index_strong   | 0.0499921 |             0 |
|      4 | 2026-05-12    | baseline_full_dynamic_factor_industry_cap2 |   000066 | 中国长城 | IT设备     | IT设备         | dynamic_factor_score |      98.8271 | 20.00%             | 20.00%            | 100.00%                 |          25.36 |   74.44 |                98.8271 |                   98.9769 |                  88.9391 |         29.67 |        96.7  |  98.78 |             98.4478 |               98.4672 |             98.6612 |              1.20295 |         58.48 |                 1 |             | 过滤            |                  1.20945 | high_liquidity            | index_strong   | 0.0404931 |             0 |
|      5 | 2026-05-12    | baseline_full_dynamic_factor_industry_cap2 |   301217 | 铜冠铜箔 | 元器件     | 元器件         | dynamic_factor_score |      98.537  | 20.00%             | 20.00%            | 100.00%                 |          94.51 |   75.1  |                98.537  |                   98.1073 |                  91.1175 |         29.3  |        96.53 |  99.88 |             87.7377 |               83.3721 |             93.4614 |             79.7827  |         71.1  |                 0 |             | 可买            |                  1.20945 | high_liquidity            | index_strong   | 0.0685765 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_100050_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_100050_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_100050_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260513_100050_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
