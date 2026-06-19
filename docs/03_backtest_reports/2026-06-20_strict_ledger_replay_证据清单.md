# Strict Ledger 独立重放证据清单

## 固定运行信息

- 代码提交：`a71f1c235a0aeab26bfe245e39d49454f5d7a012`
- 隔离 worktree：`/tmp/chenyiyun2087-replay-a71f1c23/`
- 报告状态：`report_worktree_clean=true`、`reproducibility_status=REPRODUCIBLE`
- 数据 fingerprint：`1d90c7ab48bf2777f332aee6dbeb0631227ea6d0ce5e7525c3a37ddc0428a02a`
- 配置 fingerprint：`9bcc0bd77a15ef357bc55014a7b858dd1a89565dbb20d8c80a176a6a9cab335d`
- 账本口径：`strict_daily_ledger_v2` / `t_raw_close_limit_capped_10pct_v1`

## 原始产物

原始 CSV、JSON 和 pytest 输出位于：

`/tmp/chenyiyun2087-replay-a71f1c23/exports/signal_research/20260620_065027_551123_trusted_account_backtest/`

| 产物 | SHA-256 |
|---|---|
| `pytest_output.txt` | `a74444a7fa4994d8844999d1dec1457110ecaec5a5d31537a60a1c4bb1232bda` |
| `replay/strict_ledger_replay_report.json` | `9851bc5d52c47d5d288e0c9d2621fba1075314521bf96a2f162aa49d79ce4273` |
| `validation/strict_precommit_account_validation.json` | `3aa54deae02b2e5da1ad11ca19974db7dc598db7d59863004ef7bdaa7ec79759` |

## 结果与边界

- 相关测试：48 passed。
- 独立 replay：15 个订单，订单守恒通过；`event_replay_error_bps=0`、`ledger_vs_nav_error_bps=0`、T+1 错成交为 0。
- 验收状态：`CAUSAL_BUT_LEDGER_UNVERIFIED`，`promotion_enabled=false`。
- 未通过/阻断：`corporate_action_coverage_status=PARTIAL_UNVERIFIED`、`missed_risk_events=2`、最大非预期现金残差约 9.50%、P95/最大权重偏离约 298.95/315.85bps。
- 本清单不构成三年收益、成本压力、shadow、canary 或生产晋级证据。
