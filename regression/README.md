# 版本化量化回归基线

本目录提供**策略升级的业务语义门禁**：不仅验证代码能运行，也验证固定数据快照上的候选、订单、账本与关键绩效指标没有发生未经批准的漂移。

## 目录与职责

```text
regression/
├── baselines/                  # 已审批、只读的 golden baseline
├── contracts/                  # JSON 契约说明
└── tests/                      # 比对器自身的单元测试
scripts/quality/
├── regression_baseline.py      # 契约校验、比对、JSON/JUnit 报告
└── build_strict_ledger_regression_fixture.py
                                # 无数据库严格账本样例产物生成器
```

## 两类输出

- **Baseline**：已审阅的期望值，包含精确不变量、指标目标、允许偏差、数据夹具与样本摘要。
- **Actual artifact**：某一提交在完全相同 fixture 上运行得到的实际结果。

安全不变量、数据快照标识、订单/账本状态、样本哈希必须**精确一致**；只有被 baseline 显式授权的数值指标和名单变化可以存在容忍区间。未写入容忍区间即等价于不允许变化。

## 本地命令

```bash
# 校验 baseline 契约
python -m scripts.quality.regression_baseline validate \
  --baseline regression/baselines/strict_ledger_core.v1.json

# 从确定性账本 fixture 构建实际结果
python -m scripts.quality.build_strict_ledger_regression_fixture \
  --output /tmp/strict-ledger-actual.json

# 对比并生成可被 CI 收集的报告
python -m scripts.quality.regression_baseline compare \
  --baseline regression/baselines/strict_ledger_core.v1.json \
  --actual /tmp/strict-ledger-actual.json \
  --report /tmp/regression-report.json \
  --junit /tmp/regression-junit.xml
```

## 新建生产策略基线的流程

1. 固定并版本化最小数据快照：交易日历、原始/复权行情、停牌/ST、公司行为、因子、候选池和预期订单。
2. 编写 `scripts/quality/build_<component>_regression_fixture.py`，只能依赖 fixture，不得访问生产数据库、网络或当前日期。
3. 生成 actual artifact，并人工检查候选列表、目标权重、订单、账本守恒、收益、回撤、换手和风险事件。
4. 将人工批准的 artifact 转换为 `regression/baselines/<component>.vN.json`；必须在 PR 描述中说明：
   - 为什么结果变化；
   - 影响的交易日、股票、权重和收益风险指标；
   - 是否改变生产策略语义；
   - 审批人及关联回测报告。
5. 将该 fixture 加到 `golden-regression` CI 工作流。基线更新和生产逻辑更新应处于同一个、经过审阅的 PR。

## 迁移优先级

1. 严格执行账本与订单状态机（已落地样例）。
2. `chenyiyunSelected` 的固定选股输入 → TopN、权重、订单与 NAV 基线。
3. `ScoreRank` M1/M2/M6/M7 的固定事件数据 → 评分、候选、回测和调仓基线。
4. 生产日间管线的容器化端到端 fixture（MySQL + 固定交易日历 + 脱敏快照）。

## 基线治理规则

- 禁止在 CI 中自动“接受”新结果。
- baseline 文件的变更必须触发 `golden-regression`，并由至少一名策略/风控负责人复核。
- `metadata.fixture_id`、关键数据摘要和所有不变量必须相同；任何变化都需要新版本 baseline。
- 收益类指标可设置审慎容差；订单、风控、公司行为、T+1 以及反未来函数约束不得设置宽松容差。
