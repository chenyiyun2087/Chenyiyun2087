# 核心精选策略收益评估 - 2026-06-25

## 结论

- 风险总闸触发：defensive_only，目标仓位 50.00%
- 主策略三年累计50.01%，近1年弹性强，但最大回撤-13.12%偏深。
- adaptive_market_style v2.2三年回撤-14.50%，长期风险收益更稳，适合做降风险参照。
- shadow_validation_defensive
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：70.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 最近3个月收益评估

- 状态：`PASS`；freshness_ok=True
- 区间：2026-03-24 ~ 2026-06-25；交易日：63/63
- 累计收益：41.70%；年化收益：312.35%；最大回撤：-8.10%；当前回撤：-4.74%
- 胜率：54.84%；最差单日：-8.10%；波动率：38.03%；Sharpe：3.92；Calmar：38.54；平均暴露：65.45%

## 历史回测

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略 vol_position|2025-09-02~2026-06-25|750,049|50.01%|69.81%|-13.12%|60.74%|193|
|adaptive_market_style v2.2|2025-09-02~2026-06-25|649,745|29.95%|40.78%|-14.50%|39.00%|177|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-03-25|2026-06-25|37.04%|-8.10%|65.40%|
|6m|2025-12-25|2026-06-25|49.76%|-10.33%|63.30%|
|1y|2025-09-02|2026-06-25|52.19%|-13.12%|60.74%|
|3y|2025-09-02|2026-06-25|52.19%|-13.12%|60.74%|

### 3个月双系统对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|baseline_full_liquidity_detail_vol_position|78.18%|112.58%|-17.60%|68.97%|200|
|production_governed_vol_position_v1_2b_dynamic_score|50.01%|69.81%|-13.12%|60.74%|193|
|production_governed_vol_position_v1_2b_execution_safe_uplift|50.01%|69.81%|-13.12%|60.74%|193|
|adaptive_market_style|29.95%|40.78%|-14.50%|39.00%|177|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|000021|深科技|元器件|10.00%|
|2|300285|国瓷材料|陶瓷|10.00%|
|3|688110|东芯股份|半导体|10.00%|
|4|688521|芯原股份|半导体|10.00%|
|5|603986|兆易创新|半导体|10.00%|

- 候选权重合计：50.00%
- 行业集中：半导体3只、元器件1只、陶瓷1只
- 订单：4 笔，BUY 4 / SELL 0，计划金额 166,775

## 影子盘与实盘

- 影子盘信号日：2026-06-18；执行日：2026-06-25
- 可成交：5；不可成交：0；警告：2；平均滑点：368.5 bps
- 验收：fail / reduce_position；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=5；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260626_085333_028519_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260626_085333_028519_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260626_085333_028519_trusted_account_backtest`
