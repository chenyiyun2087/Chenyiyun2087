# Formal Happy-path 与 50 万元 TopK Challenger 升级记录

## 实际改动

- 统一 `ashare_pit_semantics_v1` 的 8 类 PIT family；正式信号截止改为
  `T 15:30:00+08:00`，执行模型固定为 `T+1 09:30:00+08:00`。
- Adapter 的正式 FILE/MYSQL 路径输出同一批 canonical 快照、内容/Schema/查询
  SHA、覆盖区间和数据库快照身份；MYSQL 使用单一只读 `REPEATABLE READ`
  事务。未提供稳定 snapshot token/GTID 时失败关闭。
- Package、Readiness、Formal Runner 绑定 canonical 字段、CNY 500,000 空账户、
  final manifest SHA 和 seal SHA；seal 后不再重写 manifest。
- Formal Builder 对 financial revision 与 industry SCD 使用可验证的 PIT as-of
  连接；交易日历和公司行动按各自事件行校验，不把事件快照错误扩成每日股票事实。
- Formal Score 应用 `factor_signs`，按每日可交易股票池计算覆盖率，缺失因子不再
  以 0 填充，并记录信号/执行时间与方向 SHA。
- 正式策略定义的信号截止与验收档案已切换到 `T15:30:00+08:00` / `alpha_v3_2`。
- 新增 `scripts/research/topk_alpha_lab.py` 与
  `config/topk_alpha_challenger.yaml`。Top5/Top10、5/10/20 日调仓、7.5bp 成本、
  10/20/50bp 滑点、ADV 0.2%、100 股最小单位、行业/个股上限及涨跌停/停牌冻结均
  为独立 Challenger 研究路径，不替换正式策略；每个因子同时输出 5/10/20 日
  Rank IC、IR、正 IC 比例、衰减、换手、ADV 容量和成本压力证据，缺标签时失败关闭。

## 当前状态

TopK 报告只在实际输入存在时生成研究结果；缺少真实 8 类 PIT、完整 OOS、三基准、
执行与 Shadow 证据时输出 `BLOCKED_DATA / TRADING_BLOCKED / NO_SCALE`，允许新增
资金保持 0 元。合成输入最高为 `S3`，不升级为历史 `E3`。

## 验证记录

- Python 3.11 已通过 PIT Adapter、Factor Builder、Readiness、Immutable Runner、
  Formal admission、严格快照相关回归测试。
- 真实数据尚未在本轮提供，因此未记录任何历史收益、容量或
  `CORE_ALPHA_TARGET_PASS` 结论；大型明细继续保留于 `exports/`。
- 新增的正式 OOS/成本门禁只读取最后 12 个月 Holdout 的指标；组合矩阵的 PBO
  目前仍明确标记为未完成的组合检验，不会用窗口负收益比例冒充正式 PBO。
