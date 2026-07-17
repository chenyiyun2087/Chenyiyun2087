# 替代策略诊断报告（非生产策略绩效报告） - 2026-07-16

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-16（209个交易日））累计34.73%，最大回撤-14.61%。
- adaptive_market_style v2.2同窗回撤-14.54%，适合做降风险参照。
- low_liquidity / shadow_validation_reduce
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
- 区间：2026-04-15 ~ 2026-07-16；交易日：63/63
- 累计收益：22.91%；年化收益：131.25%；最大回撤：-14.61%；当前回撤：-14.61%
- 胜率：56.45%；最差单日：-8.10%；波动率：42.66%；Sharpe：2.18；Calmar：8.99；平均暴露：65.18%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-16（209个交易日）|673,650|34.73%|43.50%|-14.61%|61.16%|213|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-16（209个交易日）|580,101|16.02%|19.72%|-14.54%|38.80%|192|

### 主策略近期窗口

|请求窗口|实际起始|实际结束|交易日|覆盖率|覆盖状态|收益|最大回撤|
|---|---|---|---|---|---|---|---|
|3m|2026-04-16|2026-07-16|62|98.41%|PASS|21.69%|-14.61%|
|6m|2026-01-16|2026-07-16|119|94.44%|PASS|24.13%|-14.61%|
|1y|2025-09-02|2026-07-16|209|82.94%|INSUFFICIENT_COVERAGE|36.69%|-14.61%|
|3y|2025-09-02|2026-07-16|209|27.65%|INSUFFICIENT_COVERAGE|36.69%|-14.61%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|34.73%|43.50%|-14.61%|61.16%|213|
|production_governed_vol_position_v1_2b_execution_safe_uplift|34.73%|43.50%|-14.61%|61.16%|213|
|adaptive_market_style|16.02%|19.72%|-14.54%|38.80%|192|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002156|通富微电|半导体|6.05%|
|2|002384|东山精密|元器件|4.68%|
|3|000938|紫光股份|IT设备|5.71%|
|4|300759|康龙化成|化学制药|7.84%|
|5|600900|长江电力|水力发电|25.71%|

- 候选权重合计：50.00%
- 行业集中：半导体1只、元器件1只、IT设备1只、化学制药1只、水力发电1只
- 订单：4 笔，BUY 4 / SELL 0，计划金额 215,478

## 影子盘与实盘

- 影子盘信号日：2026-07-15；执行日：2026-07-16
- 可成交：4；不可成交：0；警告：0；平均滑点：-561.6 bps
- 验收：fail / reduce_position；shadow/theory gap：5.19%
- 最近影子盘状态：fail_streak=2；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260716_214405_092382_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260716_214405_092382_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260716_214405_092382_trusted_account_backtest`
