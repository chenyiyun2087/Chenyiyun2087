# 2026-08-01 Formal Happy-path 与 TopK Challenger 复评升级

## 实际改动

- Formal Runner 绑定 `formal_pit_run_id`、`package_id`、Admission/PR-B；非 fixture
  运行必须找到并验证唯一的 Admission Seal，缺失时在回测前 `BLOCKED`。
- PIT Adapter 分离记录 SQL 文本 SHA、参数 SHA、源覆盖区间和完整性声明；Package
  只继承上游身份，不再补写 `field_definition_hash` 或日历来源。
- Readiness canonical 路径校验八类快照的参数 SHA、完整性声明、合同扩展字段和
  `available_at <= T 15:30`；Runner registry 区分 manifest content/file SHA，注册失败
  不再吞掉。
- Formal Score 按每日可交易池计算覆盖和排名，缺失因子不填中性值，95% 以下阻断；
  多策略不得复制单一策略定义；流动性因子只在一个层级应用方向。
- TopK Challenger 使用 T 日信号/T+1 开盘事件账本、前一交易日滚动 ADV、买卖双向
  0.2% ADV、100 股最小单位、开盘涨跌停/停牌冻结、现有仓位约束和双倍全成本压力。
  Top5 去除基于同一账本的已实现净 P&L，并保留 `NO_SCALE`。
- Alpha Proof 增加 HAC 残差/截距 t 值、经济基准门禁、年度贡献首年基线和绝对时点
  比较（显式时区输入）；PBO 未有正式组合矩阵时继续不合格。

## 验证结果

- Python 3.11 完整套件：`1635 passed, 15 skipped, 3 failed`。
- 失败项为本地 MySQL quarterly smoke 环境连接异常，以及两项既有 PR-I 旧 fixture
  缺少正式 PIT/身份字段；没有将其伪报为通过。
- 变更相关聚焦套件均通过；真实八类 PIT、稳定 GTID/snapshot token、正式 OOS、容量
  和 120 个交易日 Shadow 仍未提供，因此本轮没有产生历史 E3 或真实绩效 PASS。

## 当前边界

`Research`、`Trading`、`Capital` 独立；当前结论保持
`BLOCKED_DATA / TRADING_BLOCKED / NO_SCALE`，允许新增资金为 `0 CNY`。未连接或写入
用户提供的 MySQL 密码，也未启用 Canary、券商报单或生产默认策略。
