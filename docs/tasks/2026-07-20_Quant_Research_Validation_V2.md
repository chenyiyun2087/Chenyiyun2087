# Quant Research Validation V2 实施与持续验收

## 工程结论

Validation V2 的代码、配置合同、迁移工具、回归基线和四层 CI 已落地。生产治理状态为
`PRODUCTION_EXCEPTION_FIXED_CAPITAL`：本金固定 50 万元，候选可生成，但只能产生人工订单草案，禁止增加风险敞口、禁止外部资金、禁止券商 API 和自动下单。

工程完成不等于策略验证完成。正式结论保持：

```text
promotion_status = BLOCKED
capital_status = NO_SCALE
risk_exposure_increase_allowed = false
external_capital_allowed = false
canary_capital_authorized = false
```

## 已实现范围

- ScoreRank 行业共振以调整前分数 30–65 触发 bullish bonus；bearish penalty 仍限交易池，并输出前分、调整值、原因码和后分。
- 正式路径只允许 `construct_portfolio()`；旧 Builder 标记 `DEPRECATED_DO_NOT_USE`。硬约束统一为单股 15%、行业 30%、主题 40%、前两大风险贡献 45%，生产总仓位 50% NAV，约束后的余额保留现金。
- Readiness 健康记录最多落后一个交易日；YELLOW 冻结新增高风险，RED、过期和缺失只许卖出与维持。候选限定 1–20，订单数量必须已知，零订单原因码白名单化，证据包含日期、数量、原因和最终权限。
- Walk-forward 严格分离 Train、Validation、Test。Validation 只从预注册持有期中选唯一冠军并冻结身份；Test 只计算冻结冠军的账户经济结果。季度 OOS 注册表只能人工追加。
- 因子平台分成 PIT 原始层、每日截面层和 Fold 冻结组合层；因子卡合同覆盖 IC、ICIR、单调性、中性收益、换手、半衰期、状态稳定性、容量和边际 Calmar。
- 精确消融矩阵包含完整策略、去 B/S、去行业共振、去非线性、去 AShare 补位、去流动性、去趋势、趋势加流动性、等权 Top5、随机 Top5、反向 Top5；缺一项即阻断。
- 10 日规则改为最短持有偏好，账户硬风控、重大事件、ST/退市、行业红门、连续不可成交、Alpha 卖出分位和数据/公司行为异常可按结构化优先级提前退出。
- PIT Manifest V2 固定 14 类组件与七项 lineage 字段，提供只读源审计、非覆盖元数据迁移、增量合并、全量冻结、主副本复制与 SHA256 验证路径。
- 2013+ 验收合同固定 5 个资金规模乘 5 个成本/滑点情景；Release Gate 要求完整 25 格、覆盖率至少 98%、未来数据违规为零、双账本 VERIFIED、极端成本累计收益为正、最大回撤不低于 -35%。
- Golden Regression 已合并独立 Oracle/Evidence 语义，并覆盖 TopN、权重、订单、NAV、双账本、ScoreRank M1/M2/M6/M7 与脱敏 MySQL fixture；基线变更必须记录人工审批人、时间、旧哈希和变更原因。
- Shadow 状态机只累计唯一真实交易日：先 20 日技术、再 60 日经济、至少 30 个完整回合。通过后只能生成 Canary 审批包，永不自动授权资金。

## 外部证据阻断

当前环境未配置 `CHENYIYUN_DB_URL`、`CHENYIYUN_EVIDENCE_ROOT` 和
`CHENYIYUN_EVIDENCE_REPLICA_ROOT`。因此不能生成或声称以下结果：2013+ 正式 PIT 覆盖率、完整 25 情景经济回测、真实数据库集成零跳过、真实双账本 VERIFIED，以及 20+60 个真实交易日 Shadow。

这些缺口是验收输入，不是可由历史模拟填补的工程缺口。数据接入后运行 Release Evidence workflow；失败时自动延长观察并继续 `BLOCKED / NO_SCALE`。

## 本地验证结果

- 全量测试：`1392 passed, 16 skipped, 0 failed`；16 项均未被当作 Release Evidence 接受，正式 CI 的数据库层配置为发现 skip 即失败。
- V2 与 Golden 定向合同：`20 passed`。
- 2013+ 执行矩阵 dry-run：5 个资金规模 × 5 个成本/滑点情景，共 25 项，命令与参数合同有效；没有把 dry-run 记为经济结果。
- Golden comparator：`PASS`，差异数 0。
- Release freeze SHA、OOS 注册表、Python 编译和 `git diff --check`：通过。

## 复现入口

```bash
python scripts/research/oos_registry.py
python scripts/research/run_full_history_strict_backtest.py --dry-run --output-dir /tmp/v2-grid
python scripts/maintenance/audit_pit_v2_sources.py --output /tmp/pit-v2-audit.json
python scripts/ops/evaluate_validation_v2_lifecycle.py --daily-evidence shadow.json --output status.json
python -m pytest -q test/test_quant_validation_v2.py regression/tests
```
