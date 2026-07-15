# 替代策略诊断报告（非生产策略绩效报告） - 2026-07-14

## 结论

- 风险总闸触发：defensive_only，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-14（207个交易日））累计40.67%，最大回撤-13.12%。
- adaptive_market_style v2.2同窗回撤-14.54%，适合做降风险参照。
- shadow_validation_defensive
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- 实际回测策略：`production_governed_vol_position_v1_2b_dynamic_score`
- 身份状态：`SUBSTITUTE_DIAGNOSTIC`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：70.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 最近3个月收益评估

- 状态：`PASS`；freshness_ok=True
- 区间：2026-04-13 ~ 2026-07-14；交易日：63/63
- 累计收益：28.69%；年化收益：178.78%；最大回撤：-11.91%；当前回撤：-10.84%
- 胜率：58.06%；最差单日：-8.10%；波动率：42.02%；Sharpe：2.65；Calmar：15.01；平均暴露：65.17%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-14（207个交易日）|703,327|40.67%|51.80%|-13.12%|61.09%|211|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-14（207个交易日）|586,890|17.38%|21.65%|-14.54%|39.07%|188|

### 主策略近期窗口

|请求窗口|实际起始|实际结束|交易日|覆盖率|覆盖状态|收益|最大回撤|
|---|---|---|---|---|---|---|---|
|3m|2026-04-14|2026-07-14|62|98.41%|PASS|27.94%|-11.91%|
|6m|2026-01-14|2026-07-14|119|94.44%|PASS|22.82%|-11.91%|
|1y|2025-09-02|2026-07-14|207|82.14%|INSUFFICIENT_COVERAGE|42.71%|-13.12%|
|3y|2025-09-02|2026-07-14|207|27.38%|INSUFFICIENT_COVERAGE|42.71%|-13.12%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|40.67%|51.80%|-13.12%|61.09%|211|
|production_governed_vol_position_v1_2b_execution_safe_uplift|40.67%|51.80%|-13.12%|61.09%|211|
|adaptive_market_style|17.38%|21.65%|-14.54%|39.07%|188|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|688120|华海清科|半导体|10.22%|
|2|688347|华虹公司|半导体|9.91%|
|3|002371|北方华创|半导体|9.51%|
|4|002156|通富微电|半导体|10.09%|
|5|002185|华天科技|半导体|10.27%|

- 候选权重合计：50.00%
- 行业集中：半导体5只
- 订单：0 笔，BUY 0 / SELL 0，计划金额 0

## 影子盘与实盘

- 影子盘信号日：2026-07-10；执行日：2026-07-14
- 可成交：3；不可成交：0；警告：0；平均滑点：-243.1 bps
- 验收：pass / none；shadow/theory gap：2.69%
- 最近影子盘状态：fail_streak=0；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_012123_884920_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_012123_884920_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_012123_884920_trusted_account_backtest`
