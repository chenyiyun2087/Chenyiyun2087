# 回测报告索引

| 策略名称 | 资金规模 | 回测周期 | 期末权益 | 年化收益 | 最大回撤 | 结论 | 文件夹 |
|---|---:|---|---:|---:|---:|---|---|
| `tiered_liquidity_then_bs_v2` | 50 万 | 2025-06-03 至 2026-05-29 | 1,544,720 | +228.49% | -23.28% | 最近一年固定策略第一 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `adaptive_style_switch` | 50 万 | 2025-06-03 至 2026-05-29 | 778,429 | +59.48% | -23.64% | 未跑赢固定进攻策略，仅研究观察 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `baseline_full_liquidity_detail` | 50 万 | 2025-06-03 至 2026-05-29 | 845,960 | +74.10% | -30.80% | 防守/流动性质量对照 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `baseline_full_dynamic_factor_industry_cap2` | 50 万 | 2025-06-03 至 2026-05-29 | 827,899 | +70.18% | -32.90% | 均衡策略对照 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `baseline_full_score` | 50 万 | 2025-06-03 至 2026-05-29 | 653,208 | +32.55% | -40.87% | 综合分兜底基准 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |

## 文件说明

- `trusted_account_backtest_summary.csv`：账户级回测汇总。
- `trusted_account_backtest_nav.csv`：净值曲线。
- `trusted_account_backtest_positions.csv`：每日持仓。
- `trusted_account_backtest_trades.csv`：交易日志。
- `trusted_account_backtest_candidates.csv`：每日候选。
- `trusted_account_backtest_adaptive_decisions.csv`：自适应策略每日切换决策。
- `trusted_account_backtest_report.md/json`：回测报告。

## 归档原则

- 原始导出文件保留在 `exports/signal_research/`。
- `docs/03_backtest_reports/` 只存索引、摘要和人工复盘文档。
- 影响生产策略选择的回测必须记录：周期、资金、成本、滑点、T+1 口径、未来函数控制。
