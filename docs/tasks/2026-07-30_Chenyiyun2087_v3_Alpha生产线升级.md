# Chenyiyun2087 v3.0 Alpha 生产线升级记录

## 实施结论

`alpha_v3` 工程闭环已经落地，但当前 release 的研究验收与生产晋级均为
`BLOCKED`，资金状态为 `NO_SCALE`，允许新增风险资金为 0 元。该结论没有改变
生产默认策略、Canary、券商 API 或现有资金阶梯。

## 已完成

- 在 `config/production_acceptance.yaml` 增加单一版本化 `alpha_v3` 验收档案，
  固定 2018-01-01 起的核心验证期、50 万元本金、T 日信号/T+1 开盘执行、绩效
  与因子门槛、成本压力、股票池扰动和失败关闭规则。
- 新增 `scripts/research/run_alpha_v3_validation.py`，统一生成 Alpha 归因、因子
  IC、Walk-forward、执行成本、晋级门禁和策略计分卡六份 release-scoped 报告，
  并记录 HEAD、配置 SHA、输入 SHA、代码文件 SHA 和内容 SHA。
- 将既有 Meta Allocator 验证本金统一为 50 万元；核心、进攻、均衡、防御角色
  和生产路由保持不变。
- 扩展市场状态观测，纳入沪深300/中证1000趋势、成交额、上涨广度、涨跌停、
  Top5 成交集中度和 HHI，继续沿用确认期、最短持有期与危机即时降仓。
- 更新动态冠军准入评估器，使历史研究证据引用 `alpha_v3`，同时保留真实
  Shadow、闭环交易、人工审批和 5万/12.5万/25万/50万元资金阶梯。
- 增加配置校验、归因闭合、IC/IR、市场状态、扰动确定性、成本压力和失败关闭
  回归测试。

## 实际证据

- Release：`champion-v1-2b-dynamic-score-20260618`
- 策略：`production_governed_vol_position_v1_2b_dynamic_score`
- 可用样本：2023-11-30 至 2026-06-17，共 615 个交易日
- 初始资金：500,000 元
- 成本模型：单边 7.5bp；原冻结回测滑点为 0，v3 另生成 20/50/100bp 压力情景
- 累计收益：+57.23%
- 年化收益：+20.37%
- 最大回撤：-25.52%
- Sharpe：0.82
- 未来函数控制：T 日信号/T+1 执行；动态样本仍须满足
  `exit_date < signal_date`

上述实际结果没有达到 `alpha_v3` 的年化大于 25%、最大回撤小于 25% 和
Sharpe 大于 1 的硬门槛；基准、正式 PIT、归因、因子覆盖、Walk-forward、
真实成交压力和 release-scoped Shadow 证据也不完整。因此不得记录 PASS。

原始证据位于：

- `exports/alpha_v3_validation/20260730_alpha_v3/`
- `exports/dynamic_champion_live_readiness/20260730_alpha_v3/`

## 测试

- v3 及相关治理回归：111 passed。
- Python 3.11 完整套件：1500 passed、15 skipped、1 failed。
- 唯一失败为要求真实 MySQL 的 `TestL10QuarterlySmoke`；当前测试环境的数据库
  连接对象被 Mock，无法完成 MySQL 服务器版本握手。此项按环境/正式数据阻塞
  披露，不改成跳过，也不伪造通过。
- 动态准入 Artifact 结构校验通过；阻断状态与 0 元资金结论符合预期。

## 后续最高价值事项

1. 生成 2018-01-01 起、带正式 PIT 快照和基准净值的统一账户账本。
2. 在同一冻结输入上完成 Alpha 归因、5/10/20 日 Rank IC 和滚动样本外验证。
3. 用真实涨跌停、未成交冻结和成交回报重跑成本/容量压力。
4. 从零累计本 release 的 60 个真实 Shadow 交易日和 30 个闭环交易，再申请
   人工审批；历史回填不得计入。
