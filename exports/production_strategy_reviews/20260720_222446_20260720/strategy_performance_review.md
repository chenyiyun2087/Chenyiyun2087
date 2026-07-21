# 替代策略诊断报告（非生产策略绩效报告） - 2026-07-20

## 结论

- 风险总闸触发：freeze_buy，目标仓位 50.00%
- 主策略当前回测窗口（2025-09-02~2026-07-20（211个交易日））累计52.55%，最大回撤-15.18%。
- adaptive_market_style v2.2同窗回撤-14.54%，适合做降风险参照。
- shadow_validation_defensive / shadow_theory_gap_freeze
- 实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。

## 生产口径

- 风险档：`adaptive`
- 主策略：`production_governed_vol_position`
- 实际回测策略：`production_governed_vol_position_v1_2b_dynamic_score`
- 身份状态：`SUBSTITUTE_DIAGNOSTIC`
- TopN：5；总持仓上限：5；持有期：10 个交易日；默认仓位：50.00%
- 配置文件：`/Volumes/extension/projects/Chenyiyun2087/config/production_strategy.yaml`
- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。

## 最近3个月收益评估

- 状态：`PASS`；freshness_ok=True
- 区间：2026-04-17 ~ 2026-07-20；交易日：63/63
- 累计收益：23.78%；年化收益：138.02%；最大回撤：-15.18%；当前回撤：-14.64%
- 胜率：51.61%；最差单日：-15.18%；波动率：54.00%；Sharpe：1.88；Calmar：9.09；平均暴露：48.74%

## 当前回测窗口

|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|
|---|---|---:|---:|---:|---:|---:|---:|
|主策略窗口（production_governed_vol_position_v1_2b_dynamic_score）|2025-09-02~2026-07-20（211个交易日）|762,741|52.55%|65.99%|-15.18%|47.47%|212|
|adaptive_market_style v2.2窗口|2025-09-02~2026-07-20（211个交易日）|574,265|14.85%|18.08%|-14.54%|38.55%|193|

### 主策略近期窗口

|请求窗口|实际起始|实际结束|交易日|覆盖率|覆盖状态|收益|最大回撤|
|---|---|---|---|---|---|---|---|
|3m|2026-04-20|2026-07-20|62|98.41%|PASS|20.85%|-15.18%|
|6m|2026-01-20|2026-07-20|119|94.44%|PASS|31.42%|-15.18%|
|1y|2025-09-02|2026-07-20|211|83.73%|INSUFFICIENT_COVERAGE|53.71%|-15.18%|
|3y|2025-09-02|2026-07-20|211|27.91%|INSUFFICIENT_COVERAGE|53.71%|-15.18%|

### 策略同窗对照

|策略|收益|年化|最大回撤|平均暴露|交易数|
|---|---|---|---|---|---|
|production_governed_vol_position_v1_2b_dynamic_score|52.55%|65.99%|-15.18%|47.47%|212|
|production_governed_vol_position_v1_2b_execution_safe_uplift|52.55%|65.99%|-15.18%|47.47%|212|
|adaptive_market_style|14.85%|18.08%|-14.54%|38.55%|193|

## 当前候选与订单

|排名|代码|名称|行业|权重|
|---|---|---|---|---|
|1|688498|源杰科技|半导体|4.08%|
|2|600900|长江电力|水力发电|12.65%|
|3|601668|中国建筑|建筑工程|12.16%|
|4|600519|贵州茅台|白酒|10.18%|
|5|601318|中国平安|保险|10.94%|

- 候选权重合计：50.00%
- 行业集中：半导体1只、水力发电1只、建筑工程1只、白酒1只、保险1只
- 订单：0 笔，BUY 0 / SELL 0，计划金额 0

## 影子盘与实盘

- 影子盘信号日：2026-07-17；执行日：2026-07-20
- 可成交：3；不可成交：0；警告：0；平均滑点：-11.3 bps
- 验收：pass / none；shadow/theory gap：0.00%
- 最近影子盘状态：fail_streak=0；worst_action=reduce_position
- 实盘快照：总权益 500,000，现金 500,000，持仓市值 0，日收益 0.00%
- 数据提醒：live_positions is empty; live realized strategy trend cannot be judged

## 数据来源

- primary_governed: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260720_214430_491578_trusted_account_backtest`
- adaptive_market_style_v22: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260720_214430_491578_trusted_account_backtest`
- dual_system_3m: `/Volumes/extension/projects/Chenyiyun2087/exports/signal_research/20260720_214430_491578_trusted_account_backtest`
