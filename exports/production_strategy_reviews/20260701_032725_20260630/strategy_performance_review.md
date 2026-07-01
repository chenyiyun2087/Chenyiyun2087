# 核心精选策略收益评估 - 2026-06-30

## 结论

- 风险总闸触发：defensive_only，目标仓位 50.00%
- 主策略三年累计57.77%，近1年弹性强，但最大回撤-13.12%偏深。
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
- 区间：2026-03-27 ~ 2026-06-30；交易日：63/63
- 累计收益：44.72%；年化收益：349.29%；最大回撤：-8.10%；当前回撤：0.00%
- 胜率：58.06%；最差单日：-8.10%；波动率：38.01%；Sharpe：4.15；Calmar：43.10；平均暴露：65.50%

## 历史回测

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略 vol_position|2025-09-02~2026-06-30|788,864|57.77%|79.73%|-13.12%|60.84%|201|
|adaptive_market_style v2.2|2025-09-02~2026-06-30|639,591|27.92%|37.24%|-14.50%|38.78%|183|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-03-30|2026-06-30|45.15%|-8.10%|65.54%|
|6m|2025-12-30|2026-06-30|58.90%|-10.33%|63.83%|
|1y|2025-09-02|2026-06-30|60.07%|-13.12%|60.84%|
|3y|2025-09-02|2026-06-30|60.07%|-13.12%|60.84%|

### 3个月双系统对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|baseline_full_liquidity_detail_vol_position|120.98%|177.17%|-17.60%|68.99%|205|
|production_governed_vol_position_v1_2b_dynamic_score|57.77%|79.73%|-13.12%|60.84%|201|
|production_governed_vol_position_v1_2b_execution_safe_uplift|57.77%|79.73%|-13.12%|60.84%|201|
|adaptive_market_style|27.92%|37.24%|-14.50%|38.78%|183|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|000021|深科技|元器件|10.00%|
|2|688110|东芯股份|半导体|10.00%|
|3|688120|华海清科|半导体|10.00%|
|4|688012|中微公司|半导体|10.00%|
|5|300223|北京君正|半导体|10.00%|

- 候选权重合计：50.00%
- 行业集中：半导体4只、元器件1只
- 订单：0 笔，BUY 0 / SELL 0，计划金额 0

## 影子盘与实盘

- 影子盘信号日：2026-06-29；执行日：2026-06-30
- 可成交：4；不可成交：0；警告：0；平均滑点：14.8 bps
- 验收：pass / none；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=0；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260701_032445_701175_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260701_032445_701175_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260701_032445_701175_trusted_account_backtest`
