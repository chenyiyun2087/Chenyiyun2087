# 核心精选策略收益评估 - 2026-06-18

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略三年累计19.94%，近1年弹性强，但最大回撤-24.81%偏深。
- adaptive_market_style v2.2三年回撤-37.33%，长期风险收益更稳，适合做降风险参照。
- shadow_validation_reduce
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：70.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 历史回测

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略 vol_position|2023-11-30~2026-06-17|599,703|19.94%|7.75%|-24.81%|55.63%|611|
|adaptive_market_style v2.2|2023-01-05~2026-06-04|710,445|42.09%|11.36%|-37.33%|54.08%|835|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-03-17|2026-06-17|22.57%|-9.06%|67.39%|
|6m|2025-12-17|2026-06-17|41.00%|-15.97%|65.17%|
|1y|2025-06-17|2026-06-17|42.09%|-18.91%|60.70%|
|3y|2023-11-30|2026-06-17|19.01%|-24.81%|55.63%|

### 3个月双系统对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|ashare_auto_shadow|8.72%|42.06%|-17.28%|93.21%|27|
|adaptive_market_style|7.36%|34.77%|-10.97%|61.79%|55|
|dual_system_adaptive_route|5.27%|24.07%|-12.06%|68.76%|56|
|ashare_trend_breakout_shadow|0.00%|0.00%|0.00%|0.00%|0|
|ashare_hybrid_conservative_shadow|-11.82%|-41.05%|-15.56%|9.75%|2|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|300285|国瓷材料|陶瓷|6.48%|
|2|301217|铜冠铜箔|元器件|8.47%|
|3|600392|盛和资源|小金属|13.27%|
|4|300857|协创数据|IT设备|11.74%|
|5|600378|昊华科技|化工原料|10.04%|

- 候选权重合计：50.00%
- 行业集中：陶瓷1只、元器件1只、小金属1只、IT设备1只、化工原料1只
- 订单：5 笔，BUY 5 / SELL 0，计划金额 231,343

## 影子盘与实盘

- 影子盘信号日：2026-06-17；执行日：2026-06-18
- 可成交：5；不可成交：0；警告：1；平均滑点：54.9 bps
- 验收：fail / reduce_position；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=1；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260618_123340_650630_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260605_004258_229723_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260604_163941_308980_trusted_account_backtest`
