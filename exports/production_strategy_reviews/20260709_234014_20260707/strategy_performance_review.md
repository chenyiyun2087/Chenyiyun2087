# 核心精选策略收益评估 - 2026-07-07

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-07（202个交易日））累计41.07%，最大回撤-13.12%。
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
- 区间：2026-04-03 ~ 2026-07-07；交易日：63/63
- 累计收益：31.21%；年化收益：201.68%；最大回撤：-10.58%；当前回撤：-10.58%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.19%；Sharpe：2.89；Calmar：19.05；平均暴露：65.39%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-07（202个交易日）|705,364|41.07%|53.94%|-13.12%|60.96%|203|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-07（202个交易日）|614,579|22.92%|29.52%|-14.50%|39.02%|185|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-07|2026-07-07|31.30%|-10.58%|65.33%|
|6m|2026-01-07|2026-07-07|35.42%|-10.58%|64.49%|
|1y|2025-09-02|2026-07-07|43.13%|-13.12%|60.96%|
|3y|2025-09-02|2026-07-07|43.13%|-13.12%|60.96%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|41.07%|53.94%|-13.12%|60.96%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|41.07%|53.94%|-13.12%|60.96%|203|
|adaptive_market_style|22.92%|29.52%|-14.50%|39.02%|185|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002371|北方华创|半导体|16.21%|
|2|688120|华海清科|半导体|4.22%|
|3|000938|紫光股份|IT设备|13.17%|
|4|688766|普冉股份|半导体|7.83%|
|5|688017|绿的谐波|机械基件|8.58%|

- 候选权重合计：50.00%
- 行业集中：半导体3只、IT设备1只、机械基件1只
- 订单：2 笔，BUY 2 / SELL 0，计划金额 146,747

## 影子盘与实盘

- 影子盘信号日：2026-07-03；执行日：2026-07-07
- 可成交：2；不可成交：0；警告：0；平均滑点：-681.7 bps
- 验收：fail / reduce_position；shadow/theory gap：6.55%
- 最近影子盘状态：fail_streak=1；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_233801_654697_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_233801_654697_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_233801_654697_trusted_account_backtest`
