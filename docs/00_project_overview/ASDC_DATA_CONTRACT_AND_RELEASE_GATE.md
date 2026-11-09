# ASDC 数据底座契约与 Release Gate 治理规范

> 本文件定义 Chenyiyun2087 消费 AShareDataCenter (ASDC) 数据底座的标准数据契约、发布门禁约束与防未来函数规则。

## 1. 核心定位与数据流向

- **AShareDataCenter (ASDC)**：唯一标准化全市场事实源、数据清洗加工、PIT 时点切片、质量门禁与发布管理。
- **Chenyiyun2087**：唯一主策略研究中心，承担 Alpha 研究、多因子组合、选股、回测、风险控制与影子账本。
- **基本原则**：策略可以有多个研究方向，但数据只有一个权威可信来源。策略模块严禁绕过 ASDC 数据质量门禁。

## 2. Release Gate 强制准入机制

Chenyiyun2087 在执行任何生产选股与订单生成前，必须通过 `ADCClient.release.check_release_gate(trade_date)` 核验 ASDC 的认证日版状态（`CERTIFIED_DAILY`）。

| Release 状态 | 生产策略处理行为 | 离线研究与诊断处理行为 |
|---|---|---|
| **READY** | 允许消费。全部必需关键数据集通过。 | 正常运行。 |
| **DEGRADED** | **逐数据集严格校验**。若任何关键数据集（`dwd_daily` / `dwd_stock_daily_standard` / `dim_stock`）降级，直接阻断；仅当降级全为可选数据集且显式配置 `allow_degraded=True` 时允许消费。 | 允许运行，需记录降级原因。 |
| **BLOCKED** | **全面阻断 (Fail-Closed)**。禁止生成新增 BUY 信号与订单草案，保留阻断原因。 | 仅允许带 `--skip-release-gate` 标记的沙箱运行，标记为 `UNVERIFIED_RESEARCH_ONLY`。 |
| **RELEASE_MISSING** | **全面阻断 (Fail-Closed)**。严禁仅凭数据库 `MAX(trade_date)` 盲目开跑。 | 仅允许带 `--skip-release-gate` 标记的沙箱运行。 |
| **SUPERSEDED** | **禁止消费**。已被新版本替代，必须切换至最新指向版本。 | 需明确指定具体 `release_id` 历史回溯。 |

## 3. PIT 完整性与 ST 状态规则

1. 优先使用 `dim_stock_status_scd` 获取精准时点切片。
2. 当发生降级回退至 `dim_stock` 时，**严禁将未知 ST 状态设为 `False`**。必须返回 `is_st=None` 与 `is_st_unknown=True`，状态标记为 `DEGRADED_UNKNOWN_ST`。
3. 在 `strict=True` 生产模式下，关键 ST 状态未知直接阻断并抛出 `ADCPITIntegrityError`。

## 4. 数据只读与访问安全

1. 生产环境优先配置独立只读用户（如 `mysql_ro` 或环境变量 `ADC_MYSQL_RO_USER`）；
2. 客户端层面内置只读守护拦截器，严禁执行 `INSERT`、`UPDATE`、`DELETE`、`DROP` 等语句；
3. 统一使用 `ADCClient.data.get_daily_bars_batch` 批量接口，禁止业务脚本分散拼接跨库 SQL。
