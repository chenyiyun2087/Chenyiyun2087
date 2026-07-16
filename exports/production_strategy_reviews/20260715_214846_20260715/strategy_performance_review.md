# 替代策略诊断报告（非生产策略绩效报告） - 2026-07-15

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-15（208个交易日））累计36.80%，最大回撤-13.30%。
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
- 区间：2026-04-14 ~ 2026-07-15；交易日：63/63
- 累计收益：24.42%；年化收益：143.02%；最大回撤：-13.30%；当前回撤：-13.30%
- 胜率：56.45%；最差单日：-8.10%；波动率：42.51%；Sharpe：2.30；Calmar：10.76；平均暴露：65.16%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-15（208个交易日）|683,983|36.80%|46.44%|-13.30%|61.12%|211|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-15（208个交易日）|584,823|16.96%|21.02%|-14.54%|38.94%|192|

### 主策略近期窗口

|请求窗口|实际起始|实际结束|交易日|覆盖率|覆盖状态|收益|最大回撤|
|---|---|---|---|---|---|---|---|
|3m|2026-04-15|2026-07-15|62|98.41%|PASS|24.79%|-13.30%|
|6m|2026-01-15|2026-07-15|119|94.44%|PASS|21.55%|-13.30%|
|1y|2025-09-02|2026-07-15|208|82.54%|INSUFFICIENT_COVERAGE|38.79%|-13.30%|
|3y|2025-09-02|2026-07-15|208|27.51%|INSUFFICIENT_COVERAGE|38.79%|-13.30%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|36.80%|46.44%|-13.30%|61.12%|211|
|production_governed_vol_position_v1_2b_execution_safe_uplift|36.80%|46.44%|-13.30%|61.12%|211|
|adaptive_market_style|16.96%|21.02%|-14.54%|38.94%|192|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002156|通富微电|半导体|8.94%|
|2|002384|东山精密|元器件|7.03%|
|3|603501|豪威集团|半导体|13.93%|
|4|300759|康龙化成|化学制药|13.35%|
|5|688498|源杰科技|半导体|6.75%|

- 候选权重合计：50.00%
- 行业集中：半导体3只、元器件1只、化学制药1只
- 订单：4 笔，BUY 4 / SELL 0，计划金额 196,756

## 影子盘与实盘

- 影子盘信号日：2026-07-10；执行日：2026-07-15
- 可成交：3；不可成交：0；警告：1；平均滑点：-17.6 bps
- 验收：fail / reduce_position；shadow/theory gap：1.09%
- 最近影子盘状态：fail_streak=1；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_214400_731072_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_214400_731072_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260715_214400_731072_trusted_account_backtest`
