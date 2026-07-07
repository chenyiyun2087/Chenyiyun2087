# 核心精选策略收益评估 - 2026-07-03

## 结论

- 风险总闸触发：reduce_position，目标仓位 50.00%
- 主策略三年累计42.71%，近1年弹性强，但最大回撤-13.12%偏深。
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
- 区间：2026-04-01 ~ 2026-07-03；交易日：63/63
- 累计收益：30.26%；年化收益：192.86%；最大回撤：-9.58%；当前回撤：-9.55%
- 胜率：58.06%；最差单日：-8.10%；波动率：41.26%；Sharpe：2.81；Calmar：20.13；平均暴露：65.53%

## 历史回测

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略 vol_position|2025-09-02~2026-07-03|713,541|42.71%|56.89%|-13.12%|60.92%|203|
|adaptive_market_style v2.2|2025-09-02~2026-07-03|648,081|29.62%|38.89%|-14.50%|38.94%|185|

### 主策略近期窗口

|窗口|起始|结束|收益|最大回撤|平均暴露|
|---|---|---|---|---|---|
|3m|2026-04-03|2026-07-03|32.74%|-9.58%|65.39%|
|6m|2026-01-05|2026-07-03|40.59%|-10.33%|64.18%|
|1y|2025-09-02|2026-07-03|44.78%|-13.12%|60.92%|
|3y|2025-09-02|2026-07-03|44.78%|-13.12%|60.92%|

### 3个月双系统对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|baseline_full_liquidity_detail_vol_position|100.87%|141.88%|-17.60%|68.96%|207|
|production_governed_vol_position_v1_2b_dynamic_score|42.71%|56.89%|-13.12%|60.92%|203|
|production_governed_vol_position_v1_2b_execution_safe_uplift|42.71%|56.89%|-13.12%|60.92%|203|
|adaptive_market_style|29.62%|38.89%|-14.50%|38.94%|185|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|688120|华海清科|半导体|4.17%|
|2|000021|深科技|元器件|11.64%|
|3|002371|北方华创|半导体|15.72%|
|4|300223|北京君正|半导体|9.04%|
|5|603986|兆易创新|半导体|9.43%|

- 候选权重合计：50.00%
- 行业集中：半导体4只、元器件1只
- 订单：2 笔，BUY 2 / SELL 0，计划金额 81,866

## 影子盘与实盘

- 影子盘信号日：2026-07-02；执行日：2026-07-03
- 可成交：4；不可成交：0；警告：0；平均滑点：-132.7 bps
- 验收：pass / none；shadow/theory gap：1.29%
- 最近影子盘状态：fail_streak=0；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260703_213504_337006_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260703_213504_337006_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260703_213504_337006_trusted_account_backtest`
