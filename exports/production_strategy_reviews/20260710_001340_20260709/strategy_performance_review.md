# 核心精选策略收益评估 - 2026-07-09

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-09（204个交易日））累计46.17%，最大回撤-13.12%。
- adaptive_market_style v2.2同窗回撤-14.50%，适合做降风险参照。
- shadow_validation_reduce
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- 实际回测策略：`production_governed_vol_position_v1_2b_dynamic_score`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：70.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 最近3个月收益评估

- 状态：`PASS`；freshness_ok=True
- 区间：2026-04-08 ~ 2026-07-09；交易日：63/63
- 累计收益：30.23%；年化收益：192.57%；最大回撤：-11.60%；当前回撤：-7.35%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.45%；Sharpe：2.80；Calmar：16.59；平均暴露：65.28%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-09（204个交易日）|730,851|46.17%|60.20%|-13.12%|61.01%|203|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-09（204个交易日）|618,918|23.78%|30.33%|-14.50%|39.08%|186|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-09|2026-07-09|31.54%|-11.60%|65.19%|
|6m|2026-01-09|2026-07-09|35.75%|-11.60%|64.85%|
|1y|2025-09-02|2026-07-09|48.30%|-13.12%|61.01%|
|3y|2025-09-02|2026-07-09|48.30%|-13.12%|61.01%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|46.17%|60.20%|-13.12%|61.01%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|46.17%|60.20%|-13.12%|61.01%|203|
|adaptive_market_style|23.78%|30.33%|-14.50%|39.08%|186|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002371|北方华创|半导体|14.63%|
|2|002384|东山精密|元器件|9.38%|
|3|688347|华虹公司|半导体|9.99%|
|4|688120|华海清科|半导体|7.74%|
|5|603986|兆易创新|半导体|8.26%|

- 候选权重合计：50.00%
- 行业集中：半导体4只、元器件1只
- 订单：3 笔，BUY 3 / SELL 0，计划金额 100,582

## 影子盘与实盘

- 影子盘信号日：2026-07-07；执行日：2026-07-09
- 可成交：2；不可成交：0；警告：1；平均滑点：362.2 bps
- 验收：fail / reduce_position；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=3；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260710_000907_178875_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260710_000907_178875_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260710_000907_178875_trusted_account_backtest`
