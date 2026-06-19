# Strict Ledger 唯一账本烟测摘要

## 结论

strict 路径已改为由 `ExecutionLedger` 记录预提交订单、T+1 成交/拒单/取消、原始价格持仓与公司行为事件；T+1 不可交易和涨跌停阻断均不会虚构成交。当前状态仍为 **`CAUSAL_BUT_LEDGER_UNVERIFIED`**，`promotion_enabled=false`，不进入 shadow、canary 或生产。

## 本轮证据

- 代码烟测：2026-01-06 至 2026-01-30，18 个有效交易日；输出位于 `exports/signal_research/20260620_015636_218614_trusted_account_backtest/`。
- 验收输出：`validation_status=CAUSAL_BUT_LEDGER_UNVERIFIED`、`ledger_reconciliation_error_bps=0`、`t1_wrong_fill_count=0`。
- 不可复现标记：该输出来自脏工作区，`report_worktree_clean=false`、`reproducibility_status=NON_REPRODUCIBLE`。
- 公司行为数据：`corporate_action_coverage_status=PARTIAL_UNVERIFIED`；配股/未知拆并/数据源异常均 fail closed，尚无独立配股与拆并事件表，不能标记为 `RECONCILED`。

## 边界与下一步

- 本烟测 18 日全部为 `no_incremental_uplift`，未覆盖 normal/high/extreme cap 情景；严格候选的短期收益不作为比较结论。
- 全历史、成本压力与开发/保留期矩阵被明确推迟到 clean worktree、固定 SHA 与公司行为/账本覆盖可对账之后。
- 生产默认继续为 `production_governed_vol_position`；`research_shadow_candidate.enabled=false` 保持不变。
