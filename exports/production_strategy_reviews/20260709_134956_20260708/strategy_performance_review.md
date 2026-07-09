# 核心精选策略收益评估 - 2026-07-08

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-08（203个交易日））累计39.46%，最大回撤-13.12%。
- adaptive_market_style v2.2同窗回撤-14.50%，适合做降风险参照。
- 今日影子盘不可成交订单 1 个。
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
- 区间：2026-04-07 ~ 2026-07-08；交易日：63/63
- 累计收益：29.80%；年化收益：188.69%；最大回撤：-11.60%；当前回撤：-11.60%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.31%；Sharpe：2.78；Calmar：16.26；平均暴露：65.32%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-08（203个交易日）|697,321|39.46%|51.43%|-13.12%|60.98%|203|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-08（203个交易日）|595,461|19.09%|24.36%|-14.50%|39.05%|185|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-08|2026-07-08|24.25%|-11.60%|65.26%|
|6m|2026-01-08|2026-07-08|33.80%|-11.60%|64.66%|
|1y|2025-09-02|2026-07-08|41.49%|-13.12%|60.98%|
|3y|2025-09-02|2026-07-08|41.49%|-13.12%|60.98%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|39.46%|51.43%|-13.12%|60.98%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|39.46%|51.43%|-13.12%|60.98%|203|
|adaptive_market_style|19.09%|24.36%|-14.50%|39.05%|185|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002371|北方华创|半导体|15.53%|
|2|688120|华海清科|半导体|3.85%|
|3|688432|有研硅|半导体|5.93%|
|4|603893|瑞芯微|半导体|14.98%|
|5|002384|东山精密|元器件|9.71%|

- 候选权重合计：50.00%
- 行业集中：半导体4只、元器件1只
- 订单：0 笔，BUY 0 / SELL 0，计划金额 0

## 影子盘与实盘

- 影子盘信号日：2026-07-07；执行日：2026-07-08
- 可成交：1；不可成交：1；警告：0；平均滑点：0.0 bps
- 验收：fail / reduce_position；shadow/theory gap：45.05%
- 最近影子盘状态：fail_streak=2；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_134735_974993_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_134735_974993_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_134735_974993_trusted_account_backtest`
