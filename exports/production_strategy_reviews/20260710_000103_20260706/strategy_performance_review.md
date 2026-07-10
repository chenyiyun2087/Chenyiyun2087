# 核心精选策略收益评估 - 2026-07-06

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-06（201个交易日））累计41.19%，最大回撤-13.12%。
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
- 区间：2026-04-02 ~ 2026-07-06；交易日：63/63
- 累计收益：30.15%；年化收益：191.81%；最大回撤：-10.51%；当前回撤：-10.51%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.27%；Sharpe：2.80；Calmar：18.25；平均暴露：65.46%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-06（201个交易日）|705,972|41.19%|54.44%|-13.12%|60.94%|203|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-06（201个交易日）|637,673|27.53%|35.86%|-14.50%|38.98%|185|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-07|2026-07-06|31.41%|-10.51%|65.32%|
|6m|2026-01-06|2026-07-06|35.17%|-10.51%|64.34%|
|1y|2025-09-02|2026-07-06|43.25%|-13.12%|60.94%|
|3y|2025-09-02|2026-07-06|43.25%|-13.12%|60.94%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|41.19%|54.44%|-13.12%|60.94%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|41.19%|54.44%|-13.12%|60.94%|203|
|adaptive_market_style|27.53%|35.86%|-14.50%|38.98%|185|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|002371|北方华创|半导体|11.80%|
|2|001309|德明利|半导体|8.24%|
|3|002050|三花智控|家用电器|11.99%|
|4|688372|伟测科技|半导体|8.89%|
|5|603667|五洲新春|机械基件|9.09%|

- 候选权重合计：50.00%
- 行业集中：半导体3只、家用电器1只、机械基件1只
- 订单：0 笔，BUY 0 / SELL 0，计划金额 0

## 影子盘与实盘

- 影子盘信号日：2026-07-03；执行日：2026-07-06
- 可成交：2；不可成交：0；警告：0；平均滑点：96.4 bps
- 验收：pass / none；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=0；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_235811_659375_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_235811_659375_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260709_235811_659375_trusted_account_backtest`
