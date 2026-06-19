# Strict Cap 与预提交执行升级

状态：代码、单元测试与三年连续账户回测完成；严格候选因执行账本基础数据未补齐，保持研究禁用。

2026-06-19 账本闭环升级进行中：已加入报告 provenance、日线复权/可交易状态字段、strict ledger primitives、明确 cap 状态和新的非晋级验收器。公司行为事件尚未接入账户主循环，因此报告会明确标记 `PARTIAL_UNVERIFIED`，验收器固定返回 `LEDGER_INCOMPLETE_NON_PROMOTABLE` 或更低研究状态。

## 已完成

- strict cap 改为读取候选 `vol_20`、`ret_1`，并对四项关键输入做 fail-closed 审计。
- 日线加载新增未复权行情、ST、证券状态、流通市值和可交易性字段。
- strict 候选按 T 日未复权收盘价的 10% 缓冲（受涨停价约束）预提交固定整手股数；T+1 只按实际开盘价成交。
- 候选/交易/汇总新增 cap 与计划成交审计字段。

## 验证

已通过：

```text
python3 -m pytest -q test/test_production_risk_governor.py test/test_research_safety_and_patterns.py test/test_strict_precommit_cap_and_sizing.py test/test_execution_safe_uplift_causality.py
# 84 passed
```

## 全历史严格回测

数据库已使用本地凭据连通并完成运行：

```text
exports/signal_research/20260619_192723_910886_trusted_account_backtest/
```

cap 覆盖率 100%、缺失回退 0 天；但原始价执行账本尚无复权因子与公司行为调整，且平均现金残差 47.63%、最大开盘权重偏离 2,330.93bps。因此结果仅作研究审计，不得用于 production、shadow 或 canary 晋级。详见 `docs/03_backtest_reports/2026-06-19_strict_precommit_uplift_回测摘要.md`。
