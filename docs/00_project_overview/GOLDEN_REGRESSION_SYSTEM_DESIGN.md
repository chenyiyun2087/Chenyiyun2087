# 量化系统 Golden Regression 具体设计

**版本：v1.0**  
**状态：设计已落地第一条严格执行账本基线；其余模块按本设计分批接入。**

---

## 1. 设计目标

本设计解决的不是“代码是否能运行”，而是升级后是否在**相同输入、相同执行假设、相同数据快照**下保持已批准的业务行为。

系统必须在 Pull Request 阶段阻止以下未经批准的变化：

1. T 日信号 / T+1 执行、原始价成交、涨跌停、停牌、ST、公司行为、订单状态机、资金约束等交易安全规则被破坏；
2. 同一策略样本的候选股、排序、目标权重、预提交订单或账本结果发生漂移；
3. 收益、回撤、换手、胜率、因子暴露等指标超出预先声明的合理容差；
4. 测试所依赖的数据、模型、配置或代码版本无法追溯；
5. CI 因网络、时间、生产数据库、动态行情等环境变量造成非确定性结果。

不在 v1 范围内：用 Golden Regression 直接判断策略未来收益能力、替代全历史研究回测、自动批准任何新的基线结果。

---

## 2. 目标架构

```text
固定 Fixture 数据包
      │
      ▼
组件 Fixture Builder ──► Actual Artifact ──► Comparator ──► PASS / FAIL + Evidence
      │                      │                    │
      │                      │                    ├─ JSON report
      │                      │                    └─ JUnit XML
      ▼                      ▼
生产/研究模块          Approved Baseline

GitHub PR
  ├─ fast-unit
  ├─ golden-regression       ← 必过
  ├─ mysql-integration
  ├─ production-core-audit
  └─ nightly-full-backtest   ← 不阻塞普通 PR，但必须留档
```

### 2.1 核心目录

```text
regression/
├── fixtures/
│   └── <component>/<fixture_id>/
│       ├── manifest.json
│       ├── input/                 # 脱敏且冻结的 CSV/Parquet/JSON
│       └── expected_context/       # 可选：人工审阅所需的辅助说明
├── baselines/
│   └── <component>.vN.json        # 已审批的目标结果
├── contracts/
│   └── artifact_contract.v1.md
├── tests/                          # comparator 与 fixture builder 自身测试
└── README.md

scripts/quality/
├── regression_baseline.py          # 比对、契约校验、JSON/JUnit 输出
├── build_<component>_regression_fixture.py
└── validate_regression_fixture.py  # 后续新增：manifest、哈希、禁止联网校验

artifacts/regression/               # CI 临时证据，不提交仓库
```

### 2.2 责任边界

| 组件 | 职责 | 禁止行为 |
|---|---|---|
| Fixture | 固定输入、版本、摘要与敏感信息边界 | 访问生产数据库、网络、当前日期、未固定随机数 |
| Builder | 调用真实生产/研究函数，生成实际结果 | 在 builder 内复刻业务算法以“伪造通过” |
| Baseline | 经人工审查的期望行为与允许偏差 | 自动根据最新运行结果覆盖 |
| Comparator | 严格比较并输出证据 | 对未声明的字段默认宽容 |
| CI | 固定环境、执行、保存证据、强制状态 | 因外部服务抖动而静默跳过安全检查 |

---

## 3. Fixture 数据包设计

每一个 fixture 是一个不可变的最小可复现实验。以 `regression/fixtures/chenyiyun_selected/weekly_rebalance_2024w20.v1/` 为例：

```text
manifest.json
input/
  trade_calendar.csv
  universe.csv
  daily_prices_raw.csv
  adj_factor.csv
  stock_status.csv
  corporate_actions.csv
  factors.csv
  prior_positions.csv
  prior_orders.csv
```

### 3.1 `manifest.json` 必填结构

```json
{
  "schema_version": "1.0",
  "fixture_id": "chenyiyun-weekly-rebalance-2024w20.v1",
  "component": "chenyiyun_selected",
  "as_of_date": "2024-05-17",
  "execution_date": "2024-05-20",
  "timezone": "Asia/Shanghai",
  "seed": 42,
  "inputs": {
    "daily_prices_raw": {"path": "input/daily_prices_raw.csv", "row_count": 0, "sha256": "..."},
    "factors": {"path": "input/factors.csv", "row_count": 0, "sha256": "..."}
  },
  "assumptions": {
    "signal_timing": "T_CLOSE",
    "execution_timing": "T_PLUS_1_OPEN",
    "price_basis": "raw",
    "commission_rate": 0.00075,
    "slippage_bps": 0
  },
  "provenance": {
    "source_snapshot": "local-export:<date>",
    "sanitization": "security codes retained; credentials and PII excluded"
  }
}
```

### 3.2 Fixture 强制规则

- `fixture_id` 一经审批不可修改；输入变化必须新建 `v2` fixture。
- 所有输入文件必须记录行数与 SHA-256，运行前验证。
- 交易日历、证券状态、原始行情、复权因子、公司行为必须来自**同一 as-of 数据视图**。
- fixture builder 必须显式传入业务日期，禁止调用 `date.today()`、网络接口或默认生产库连接。
- 随机算法必须固定 seed；模型推理必须使用冻结模型版本或 mock 的固定输出。
- 真实证券代码可保留；账号、订单号、Webhook、个人信息、数据库密码必须剔除或替换。

---

## 4. Baseline 与 Actual Artifact 设计

当前 `artifact_contract.v1` 是统一接口。Baseline 定义预期结果，Actual Artifact 定义当前提交的运行结果。

### 4.1 必须精确相等的字段

以下字段不允许设置宽松容差：

- `metadata.component`、`metadata.fixture_id`、`baseline_id`；
- `invariants.signal_timing`、`execution_timing`、`mark_price_basis`；
- T+1、涨跌停、停牌、ST、可交易性、订单状态转换、资金上限、公司行为处理、反未来函数开关；
- fixture / 中间关键数据的 `sha256`、`row_count`；
- 严格执行账本中的 replay pass、对账误差、拒单原因、订单事件序列；
- 数据库 schema migration 的 DDL 版本与关键约束。

### 4.2 可声明容差的字段

| 类别 | 示例 | 默认规则 |
|---|---|---|
| 回测指标 | 年化、Sharpe、最大回撤、换手 | 未声明即 0 容差 |
| 目标权重 | 单股权重、行业权重、现金权重 | 默认精确；仅非安全型优化允许小容差 |
| 候选股 | TopN 代码与排名 | 默认完全一致；探索策略可限制最多替换数 |
| 模型分数 | `score`、`opt_score`、`claude_score` | 固定模型/输入时应严格；模型升级须新 baseline |
| 性能指标 | 耗时、内存 | 仅监控，默认不阻塞策略正确性 |

容差不是“通过开关”。每一条 tolerance 必须在 baseline 元数据中说明业务理由和审批人。

### 4.3 指标比较规则

对某个指标 `x`：

```text
allowed_delta = max(absolute_tolerance, abs(expected) * relative_tolerance)
PASS 当且仅当 abs(actual - expected) <= allowed_delta
```

任何 NaN、Infinity、缺失字段、未知 schema、fixture 哈希不匹配都必须失败。

---

## 5. 回归层级与优先级

| 层级 | 名称 | 输入 | 断言重点 | PR 阻塞 | 首批组件 |
|---|---|---|---|---|---|
| L0 | Safety Contract | 内存对象 / 小样本 | 交易规则、风控、状态机、反未来函数 | 是 | strict ledger、risk governor |
| L1 | Deterministic Business | 小型冻结 fixture | 候选、评分、权重、订单、账本 | 是 | strict ledger、chenyiyunSelected |
| L2 | Snapshot Integration | 脱敏数据库 / 容器 | SQL、schema、跨模块数据传递 | 是 | order v2、scheduler 核心 |
| L3 | Historical Strategy | 固定历史快照 | NAV、收益、回撤、换手、因子暴露 | 标签触发 / 夜间 | 主策略、M6/M7 |
| L4 | Production-like E2E | MySQL + 固定任务队列 | 任务依赖、幂等、通知、审计留痕 | 夜间 / 发布前 | web scheduler |

### 5.1 必须接入的基线清单

#### A. 严格执行账本（P1，已实现首个样例）

- fixture：订单计划、部分成交、撤单、公司行为、原始价格、NAV；
- 结果：事件序列、现金/持仓、对账误差、拒单原因；
- 阈值：对账误差 0 bps，事件和样本哈希精确一致；
- CI：`golden-regression`，每个 PR 必过。

#### B. `chenyiyunSelected` 周度调仓（P2）

- fixture：连续 20 个交易日、股票池、因子、证券状态、持仓、交易成本；
- Builder：调用 `local_strategy_adapter`、订单生成器和 `backtest` 引擎真实入口；
- 结果：过滤每一层的股票数、Top15、Top10、目标权重、预提交订单、交易后 NAV、换手、现金残差；
- 严格项：排除 ST/科创板/北交所、上市天数、T+1、整手约束、最大持仓数；
- 基线：至少 `normal-week`、`limit-up-week`、`suspension-week`、`corporate-action-week` 四套。

#### C. ScoreRank M1/M2/M6/M7（P3）

- M1：固定技术输入 → `score`/`opt_score`/风险扣分/TRADE-WATCH 候选；
- M2/M3：固定事件与参数 → preset/网格结果排序；
- M6：固定事件数据 → NAV、成本、滑点、最大回撤；
- M7：固定上期持仓和评分 → 强制卖出原因、调仓订单、目标仓位；
- AI 评分：不直接调用外部模型；使用冻结响应 fixture，模型升级时单独建立新模型版本 baseline。

#### D. 生产调度与订单迁移（P4）

- Docker MySQL fixture；
- 断言 schema version、唯一约束、幂等键、任务依赖、业务日去重、失败重试、订单 supersede 行为；
- 所有外部通知改为 fake notifier，验证 payload 哈希和发送决策，不发送真实消息。

---

## 6. Builder 接口标准

每个 Builder 必须使用统一的纯函数入口，便于 pytest 与 CLI 共用：

```python
from pathlib import Path
from typing import Any


def build_payload(fixture_dir: Path) -> dict[str, Any]:
    """仅从 fixture_dir 读取输入，并返回 Actual Artifact v1。"""
```

CLI 入口统一为：

```bash
python -m scripts.quality.build_<component>_regression_fixture \
  --fixture regression/fixtures/<component>/<fixture_id> \
  --output artifacts/regression/<component>-actual.json
```

Builder 在生成 Actual Artifact 前必须：

1. 校验 manifest 和所有输入哈希；
2. 固定时区、业务日期和随机数；
3. 设置 `NO_NETWORK=1`、`CHENYIYUN_DB_URL` 为 fixture 专用地址或空值；
4. 记录代码提交 SHA、配置 SHA、fixture SHA、运行耗时和 Python 版本；
5. 只调用被测模块公开入口，不复制策略逻辑。

---

## 7. CI 具体设计

### 7.1 PR 必过工作流

| 工作流 | 触发 | 内容 | Required Status |
|---|---|---|---|
| `fast-unit` | push / PR | 不依赖 DB 的单元与契约测试 | 是 |
| `golden-regression` | push / PR | L0/L1 baseline 生成、比对、JUnit、证据包 | 是 |
| `mysql-integration` | push / PR | L2 migration/SQL/订单集成 | 是 |
| `secret-scan` | push / PR | gitleaks | 是，但需独立治理误报 |

`golden-regression` 每一个 component 使用独立 job，避免一个失败掩盖其他基线的执行结果：

```yaml
strategy:
  fail-fast: false
  matrix:
    component:
      - strict_ledger_core
      - chenyiyun_weekly_rebalance
      - scorerank_m1
      - scorerank_m7
```

每个 job 的固定步骤：

1. checkout；
2. 使用锁定依赖安装环境；
3. 验证 fixture manifest；
4. 生成 actual artifact；
5. baseline compare；
6. 总是上传 artifact、report、JUnit、stdout/stderr；
7. 失败时在 GitHub Step Summary 展示差异前 20 条。

### 7.2 夜间与发布前工作流

| 工作流 | 时间 / 触发 | 内容 | 结果用途 |
|---|---|---|---|
| `nightly-historical-regression` | 每个交易日收盘后 | L3 固定快照历史回测 | 趋势监控、人工复核 |
| `release-candidate-audit` | `release-candidate` 标签 | L0-L4 全量、数据完整性、可复现性 | 生产/Shadow/Canary 晋级依据 |
| `weekly-fixture-health` | 每周 | 哈希、过期、样本覆盖、敏感字段扫描 | 维护治理 |

L3 和 L4 如果失败，不能自动更新 baseline；必须生成差异报告并阻止 release-candidate 晋级。

### 7.3 当前 CI 的先决修复项

本设计不把红色 CI 当作正常状态。PR #135 的新 `golden-regression` 已通过，但仓库既有 `strict-ledger-audit`、`production-core-audit` 已出现测试失败，且两个 workflow 的 `gitleaks` 也失败。现阶段必须先完成以下 P0 工作：

1. 分别记录 pytest、integration 和 secret-scan 的原始失败原因；
2. 将“真实缺陷”“测试环境缺失”“历史密钥泄露/误报”分开处理；
3. 禁止用 `continue-on-error`、全局 ignore 或删除断言来制造绿色 CI；
4. 对确认为历史误报的 secret 建立最小化 allowlist，并写明 fingerprint、原因、到期日；
5. 所有 Required Status 连续 5 次主干通过后，再启用分支保护强制门禁。

---

## 8. Baseline 更新治理

### 8.1 允许更新的唯一流程

```text
发现合法策略变化
  → 新 fixture 或同 fixture 的新 baseline 版本
  → 生成 old/new 差异报告
  → PR 中说明业务原因与风险影响
  → 代码负责人 + 策略/风控负责人审批
  → CI 运行全部相关基线
  → 合并
```

Baseline 修改 PR 必须带 `baseline-update` 标签，并包含：

- 受影响的 component、fixture 与 baseline id；
- 代码版本、配置版本、数据快照版本；
- 候选股票、排名、权重、订单、NAV、回撤、换手、因子暴露的差异摘要；
- 哪些变化是主动设计，哪些是修复副作用；
- 对实盘、shadow、canary 的影响等级；
- 审批人。

### 8.2 审批规则

| 变更类型 | 最低审批 |
|---|---|
| 文档或新 fixture，不改变生产逻辑 | 模块负责人 |
| 收益/候选/权重变化 | 策略负责人 + 代码负责人 |
| 订单、账本、风控、公司行为、T+1 变化 | 策略负责人 + 风控负责人 + 代码负责人 |
| 生产调度、数据库迁移、密钥规则变化 | 运维/平台负责人 + 代码负责人 |

禁止行为：CI 自动接受、在同一 PR 中无差异解释地替换 baseline、对 safety invariants 设置宽容阈值。

---

## 9. 交付节奏与验收标准

### P0：CI 可相信（优先立即处理）

- 既有失败工作流完成根因修复；
- 固定依赖使用 `requirements.lock.txt` 或等效 lock；
- 根目录提供统一 pytest 配置、marker、超时和 testpath；
- 验收：main 连续 5 次全 Required Status 通过。

### P1：执行安全闭环（已完成首个样例）

- strict ledger baseline、builder、comparator、CI、证据包；
- 验收：刻意修改任一订单状态、公司行为金额、回放误差或输入摘要时，PR 必须失败并给出可读差异。

### P2：本地策略业务回归

- 完成 `chenyiyunSelected` 四类 fixture；
- 验收：对因子过滤、持仓上限、整手、停牌和涨停逻辑的回归能被定位至具体阶段和股票。

### P3：ScoreRank 研究链路回归

- 完成 M1、M2/M3、M6、M7 的冻结输入与 baseline；
- 验收：分数、候选、调仓、NAV 的差异可追溯至配置、模型、输入或算法提交。

### P4：发布前端到端治理

- MySQL + scheduler + fake notifier + order repository 的 Docker E2E；
- 验收：同一业务日期重复运行不产生重复订单；上游失败时下游正确阻断；发布证据包可独立复核。

---

## 10. 成功定义

项目达到“完整回归保障”状态需同时满足：

1. 每一条生产交易路径至少拥有一个 L1 fixture；
2. 每个生产策略至少拥有 normal、异常行情、不可交易、公司行为四类场景；
3. 关键数据表和配置都能通过 fixture 与 provenance 回溯；
4. PR 中任何未批准的候选、订单、账本、风控或收益风险漂移都会阻塞合并；
5. 全部证据包可在不访问生产环境的条件下复核；
6. baseline 更新拥有明确的人类审批记录。

在完成 P0-P4 前，系统应被定义为：**具备核心安全测试与部分 golden regression，但尚未形成全策略、全数据、全生产链路的完整回归案例库。**
