# 替代策略诊断报告（非生产策略绩效报告） - 2026-07-17

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-17（210个交易日））累计27.30%，最大回撤-19.32%。
- adaptive_market_style v2.2同窗回撤-14.54%，适合做降风险参照。
- shadow_validation_reduce
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
- 区间：2026-04-16 ~ 2026-07-17；交易日：63/63
- 累计收益：14.97%；年化收益：76.31%；最大回撤：-19.32%；当前回撤：-19.32%
- 胜率：54.84%；最差单日：-8.10%；波动率：44.26%；Sharpe：1.50；Calmar：3.95；平均暴露：65.16%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-17（210个交易日）|636,481|27.30%|33.78%|-19.32%|61.19%|213|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-17（210个交易日）|580,903|16.18%|19.82%|-14.54%|38.67%|192|

### 主策略近期窗口

|请求窗口|实际起始|实际结束|交易日|覆盖率|覆盖状态|收益|最大回撤|
|---|---|---|---|---|---|---|---|
|3m|2026-04-17|2026-07-17|62|98.41%|PASS|12.27%|-19.32%|
|6m|2026-01-19|2026-07-17|119|94.44%|PASS|18.48%|-19.32%|
|1y|2025-09-02|2026-07-17|210|83.33%|INSUFFICIENT_COVERAGE|29.15%|-19.32%|
|3y|2025-09-02|2026-07-17|210|27.78%|INSUFFICIENT_COVERAGE|29.15%|-19.32%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|27.30%|33.78%|-19.32%|61.19%|213|
|production_governed_vol_position_v1_2b_execution_safe_uplift|27.30%|33.78%|-19.32%|61.19%|213|
|adaptive_market_style|16.18%|19.82%|-14.54%|38.67%|192|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|600900|长江电力|水力发电|16.98%|
|2|688347|华虹公司|半导体|4.36%|
|3|300759|康龙化成|化学制药|6.30%|
|4|300308|中际旭创|通信设备|5.36%|
|5|601398|工商银行|银行|17.00%|

- 候选权重合计：50.00%
- 行业集中：水力发电1只、半导体1只、化学制药1只、通信设备1只、银行1只
- 订单：3 笔，BUY 3 / SELL 0，计划金额 197,510

## 影子盘与实盘

- 影子盘信号日：2026-07-16；执行日：2026-07-17
- 可成交：4；不可成交：0；警告：0；平均滑点：-235.4 bps
- 验收：fail / reduce_position；shadow/theory gap：3.48%
- 最近影子盘状态：fail_streak=3；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260717_214356_034734_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260717_214356_034734_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260717_214356_034734_trusted_account_backtest`
