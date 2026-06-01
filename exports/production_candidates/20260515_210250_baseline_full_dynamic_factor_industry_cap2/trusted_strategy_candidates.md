# 可信策略生产候选名单

## 口径

- 策略：`baseline_full_dynamic_factor_industry_cap2`，排序字段：`dynamic_factor_score`。
- 信号日：`2026-05-15`；候选数：Top 5。
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
|      1 | 2026-05-15    | baseline_full_dynamic_factor_industry_cap2 |   603986 | 兆易创新   | 半导体        | 半导体            | dynamic_factor_score |      99.1064 | 20.00%             | 20.00%            | 100.00%                 |         375.34 |   74.05 |                99.1064 |                   99.8136 |                  92.4675 |         29.94 |        96.67 |  93.78 |             86.1187 |               73.3036 |             99.3602 |              94.242  |         69.67 |                 0 |             | 可买              |                  1.17628 | normal_liquidity          | index_neutral  | 0.0385016 |             0 |
|      2 | 2026-05-15    | baseline_full_dynamic_factor_industry_cap2 |   688012 | 中微公司   | 半导体        | 半导体            | dynamic_factor_score |      98.9551 | 20.00%             | 20.00%            | 100.00%                 |         432.9  |   74.92 |                98.9551 |                   99.3635 |                  91.0856 |         29.76 |        99.15 |  93.54 |             95.9093 |               83.3075 |             97.499  |              51.0275 |         68.66 |                 0 |             | 可买              |                  1.17628 | normal_liquidity          | index_neutral  | 0.0399539 |             0 |
|      3 | 2026-05-15    | baseline_full_dynamic_factor_industry_cap2 |   688017 | 绿的谐波   | 机械基件       | 机械基件           | dynamic_factor_score |      98.0864 | 20.00%             | 20.00%            | 100.00%                 |         305.5  |   75.76 |                98.0864 |                   96.8569 |                  90.8427 |         28.6  |        99.71 |  97.11 |             97.1694 |               94.2032 |             93.2144 |              51.6285 |         59.05 |                 1 |             | 过滤              |                  1.17628 | normal_liquidity          | index_neutral  | 0.0523698 |             0 |
|      4 | 2026-05-15    | baseline_full_dynamic_factor_industry_cap2 |   300433 | 蓝思科技   | 元器件        | 元器件            | dynamic_factor_score |      97.4775 | 20.00%             | 20.00%            | 100.00%                 |          34.21 |   76.44 |                97.4775 |                   98.4777 |                  91.69   |         29.33 |        97.89 |  79    |             96.4909 |               93.0012 |             99.147  |              44.6297 |         68.78 |                 0 |             | 可买              |                  1.17628 | normal_liquidity          | index_neutral  | 0.047409  |             0 |
|      5 | 2026-05-15    | baseline_full_dynamic_factor_industry_cap2 |   300274 | 阳光电源   | 电气设备       | 电气设备           | dynamic_factor_score |      97.1182 | 20.00%             | 20.00%            | 100.00%                 |         152.95 |   70.03 |                97.1182 |                   99.5887 |                  89.9539 |         29.9  |        98.88 |  72.26 |             95.9287 |               67.4874 |             99.0888 |              59.1508 |         66.05 |                 0 |             | 可买              |                  1.17628 | normal_liquidity          | index_neutral  | 0.0397353 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260515_210250_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260515_210250_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260515_210250_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260515_210250_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
