# 可信策略生产候选名单

## 口径

- 策略：`动态因子均衡策略（单行业最多2只）`，排序字段：`dynamic_factor_score`。
- 策略ID：`baseline_full_dynamic_factor_industry_cap2`。
- 风险档位：`adaptive`；收益优先主推送档：使用最近3个月收益风险最平衡的vol_position作为主策略，默认70%仓位；adaptive_market_style保留为市场/行业状态风控影子对照。


- 信号日：`2026-06-08`；候选数：Top 5。
- 执行层：目标资金比例 `70%`；持有 `10` 个交易日；最多持仓 `5` 只。
- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。
- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。

## 风险提示

- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。
- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。

## 告警

_无_

## 候选明细

|   rank | signal_date   | strategy                                   |   symbol | name    | industry   | industry_key   | sort_col             |   rank_score | effective_weight   | position_weight   | market_exposure_scale   |   latest_close |   score |   dynamic_factor_score |   dynamic_ic_factor_score |   liquidity_detail_score |   s_liquidity |   s_breakout |   s_rs |   s_relative_amount |   s_amount_ratio_5_20 |   s_low_impact_cost |   s_amount_stability |   bs_score_v2 |   is_bs_candidate | pool_type   | bs_gate_label   |   market_amount_ratio_20 | market_liquidity_bucket   | index_bucket   |    vol_20 |   hist_mdd_20 |
|-------:|:--------------|:-------------------------------------------|---------:|:--------|:-----------|:---------------|:---------------------|-------------:|:-------------------|:------------------|:------------------------|---------------:|--------:|-----------------------:|--------------------------:|-------------------------:|--------------:|-------------:|-------:|--------------------:|----------------------:|--------------------:|---------------------:|--------------:|------------------:|:------------|:----------------|-------------------------:|:--------------------------|:---------------|----------:|--------------:|
|      1 | 2026-06-08    | baseline_full_dynamic_factor_industry_cap2 |   688017 | 绿的谐波    | 机械基件       | 机械基件           | dynamic_factor_score |      99.2625 | 20.00%             | 20.00%            | 100.00%                 |         428.25 |   78.1  |                99.2625 |                   99.5627 |                  92.6202 |         29.53 |        99.85 |  99.36 |             98.394  |               92.1827 |             98.1424 |            50.1935   |         72.96 |                 1 | WATCH       | 观察              |                 0.908433 | normal_liquidity          | index_weak     | 0.0660355 |             0 |
|      2 | 2026-06-08    | baseline_full_dynamic_factor_industry_cap2 |   688146 | 中船特气    | 半导体        | 半导体            | dynamic_factor_score |      98.9498 | 20.00%             | 20.00%            | 100.00%                 |         252.18 |   71.08 |                98.9498 |                   99.4611 |                  96.0875 |         29.22 |        99.48 | 100    |             95.6269 |               94.6788 |             96.7105 |            92.9373   |         68.05 |                 0 |             | 可买              |                 0.908433 | normal_liquidity          | index_weak     | 0.0913802 |             0 |
|      3 | 2026-06-08    | baseline_full_dynamic_factor_industry_cap2 |   600522 | 中天科技    | 通信设备       | 通信设备           | dynamic_factor_score |      98.5998 | 20.00%             | 20.00%            | 100.00%                 |          49.53 |   58.16 |                98.5998 |                   97.0449 |                  94.6362 |         29.91 |        98.92 |  94.02 |             94.9497 |               91.8731 |             99.4582 |            70.6656   |         72.43 |                 1 | WATCH       | 观察              |                 0.908433 | normal_liquidity          | index_weak     | 0.0496841 |             0 |
|      4 | 2026-06-08    | baseline_full_dynamic_factor_industry_cap2 |   688322 | 奥比中光-UW | 元器件        | 元器件            | dynamic_factor_score |      98.2355 | 20.00%             | 20.00%            | 100.00%                 |         144.17 |   77.71 |                98.2355 |                   99.2295 |                  90.479  |         28.76 |        99.85 |  99.46 |             97.9489 |               91.9892 |             94.2337 |            46.0913   |         71.82 |                 0 |             | 可买              |                 0.908433 | normal_liquidity          | index_weak     | 0.0535718 |             0 |
|      5 | 2026-06-08    | baseline_full_dynamic_factor_industry_cap2 |   603773 | 沃格光电    | 元器件        | 元器件            | dynamic_factor_score |      98.0985 | 20.00%             | 20.00%            | 100.00%                 |         136.5  |   74.56 |                98.0985 |                   99.0115 |                  86.5327 |         29.03 |        99.05 |  99.59 |             95.1625 |               95.7817 |             95.9172 |             0.386997 |         68.64 |                 0 |             | 可买              |                 0.908433 | normal_liquidity          | index_weak     | 0.0661304 |             0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260608_210238_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260608_210238_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_candidates.json`
- Dynamic Weights CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260608_210238_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_dynamic_weights.csv`
- Market Environment CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/production_candidates/20260608_210238_baseline_full_dynamic_factor_industry_cap2/trusted_strategy_market_environment.csv`
