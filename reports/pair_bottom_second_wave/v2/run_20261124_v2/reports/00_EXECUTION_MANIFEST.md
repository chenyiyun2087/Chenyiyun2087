# SWI_FACTOR V2.0 执行与环境审计报告 (00_EXECUTION_MANIFEST)

- **生成时间**: 2026-11-24 20:11:14
- **Git 分支**: `review/swi-factor-v2-audit-20261124`
- **运行模式**: `RESEARCH_ONLY` (禁止向生产交易及配置写入任何数据)
- **底层数据源**: 本地 ASDC MySQL (`ods_daily`, `dwd_adj_factor`, `ods_official_announcement_v2`, `tushare.repurchase`)
- **数据回测区间**: 2019-11-25 至 2026-11-24 (最近五年以上完整跨度)
- **目标股票**: 路维光电 (`688401.SH`)

## 1. 核心事实核验状态表

| 事实项 | 声明数值 | 本地ASDC核验结果 | 判定状态 | 证据出处 |
|---|---|---|---|---|
| 2026-07-01 盘中最高价 | 103.90 元 | 103.90 元 | **CONFIRMED** | `ods_daily` high (15:00:00) |
| 2026-07-01 收盘价 | 99.49 元 | 99.49 元 | **CONFIRMED** | `ods_daily` close |
| 2026-11-23 阶段低点 | 40.89 元 | 40.89 元 | **CONFIRMED** | `ods_daily` low (前低支点) |
| 2026-11-24 收盘价 | 43.43 元 | 43.43 元 | **CONFIRMED** | `ods_daily` close (PB03对子底) |
| 峰值至收盘回撤 | -58.20% | -58.20% | **CONFIRMED** | (43.43/103.90 - 1) |
| 峰值至低点回撤 | -60.64% | -60.64% | **CONFIRMED** | (40.89/103.90 - 1) |
| 定增转股价格调整 | 2026-07-16 | 2026-07-16 | **CONFIRMED** | `ods_official_announcement_v2` |
| 预期解禁时点 | 2027-01-14 | 预计 ~35 交易日 | **PLAUSIBLE_SCENARIO** | 限售股挂牌预期估算 |
| 股份回购实施 | 2026-11-05 | 2.0亿预案/已回购1231万元 | **CONFIRMED** | `tushare.repurchase` 均价41.73-46.48 |

## 2. Release Gate 检查清单

- [x] **RG-01**: `RESEARCH_ONLY` 模式强制开启，无写入 production 数据库或模型文件行为。
- [x] **RG-02**: 目标价比例严格设定为 `150.0 / 43.43 - 1 = +245.38338%`。
- [x] **RG-03**: 无未来函数 (PIT) 严格审计，T日信号T+1日开盘执行。
- [x] **RG-04**: 历史前复权基准锚定在信号触发日，避免前向收益乘数漂移。
