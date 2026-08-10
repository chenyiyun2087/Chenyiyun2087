# Current Production State

> 此文件由 `scripts/maintenance/generate_current_production_state.py` 确定性生成；禁止手工维护。

| 字段 | 当前值 |
|---|---|
| 生产发布 | `prod-fixed-v2-20260720-01` |
| 固定资本策略 | `production_governed_vol_position` |
| 选择引擎 | `baseline_full_liquidity_detail_vol_position` |
| 生命周期 | `PRODUCTION_EXCEPTION_FIXED_CAPITAL` |
| 本金例外 | ¥500,000（仅存量） |
| 目标仓位上限 | 50% |
| 执行 | `t_plus_1_open` / `MANUAL_ORDER_DRAFT_ONLY` |
| 扩资状态 | `NO_SCALE` |
| 新增资本 | ¥0 |
| 风险暴露增加 | 禁止 |
| 外部资本 | 禁止 |
| Broker API | 禁止 |

Smart Beta 与 Pure Alpha 均为隔离的 T21:30 研究身份；未取得路径绑定的正式 E3、正式前向经济门槛和人工审批前，不得晋级或分配资本。

来源：`config/strategy_release_registry.yaml`、`config/production_strategy.yaml`、`config/release_freeze/prod-fixed-v2-20260720-01.json`。
