# 核心精选策略收益评估 - 2026-07-02

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略三年累计42.66%，近1年弹性强，但最大回撤-13.12%偏深。
- adaptive_market_style v2.2三年回撤-14.50%，长期风险收益更稳，适合做降风险参照。
- shadow_validation_reduce
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：70.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 最近3个月收益评估

- 状态：`PASS`；freshness_ok=True
- 区间：2026-03-31 ~ 2026-07-02；交易日：63/63
- 累计收益：32.40%；年化收益：212.90%；最大回撤：-9.58%；当前回撤：-9.58%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.32%；Sharpe：2.97；Calmar：22.22；平均暴露：65.59%

## 历史回测

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略 vol_position|2025-09-02~2026-07-02|713,280|42.66%|57.17%|-13.12%|60.89%|203|
|adaptive_market_style v2.2|2025-09-02~2026-07-02|625,107|25.02%|32.87%|-14.50%|38.88%|185|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-02|2026-07-02|31.49%|-9.58%|65.45%|
|6m|2026-01-05|2026-07-02|40.54%|-10.33%|64.17%|
|1y|2025-09-02|2026-07-02|44.73%|-13.12%|60.89%|
|3y|2025-09-02|2026-07-02|44.73%|-13.12%|60.89%|

### 3个月双系统对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|baseline_full_liquidity_detail_vol_position|102.11%|144.87%|-17.60%|68.97%|207|
|production_governed_vol_position_v1_2b_dynamic_score|42.66%|57.17%|-13.12%|60.89%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|42.66%|57.17%|-13.12%|60.89%|203|
|adaptive_market_style|25.02%|32.87%|-14.50%|38.88%|185|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|688120|华海清科|半导体|4.57%|
|2|688012|中微公司|半导体|13.67%|
|3|688037|芯源微|半导体|10.54%|
|4|600378|昊华科技|化工原料|11.09%|
|5|688361|中科飞测|半导体|10.13%|

- 候选权重合计：50.00%
- 行业集中：半导体4只、化工原料1只
- 订单：4 笔，BUY 4 / SELL 0，计划金额 161,792

## 影子盘与实盘

- 影子盘信号日：2026-07-01；执行日：2026-07-02
- 可成交：3；不可成交：0；警告：0；平均滑点：-778.4 bps
- 验收：fail / reduce_position；shadow/theory gap：7.69%
- 最近影子盘状态：fail_streak=2；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260702_213213_068133_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260702_213213_068133_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260702_213213_068133_trusted_account_backtest`
