# 回测报告索引

> 2026-06-04 更新：生产策略选择以三年 T+1 账户级回测为最高优先级；最近一年结果只用于判断阶段性市场风格，不再作为长期满仓进攻策略的充分依据。当前默认生产风险档位为 `balanced`：`baseline_full_liquidity_detail_market_gate`、Top5、持有 12 日、最多 5 只、80% 基准仓位，弱市场由门禁降至约 50%。

| 策略名称 | 资金规模 | 回测周期 | 期末权益 | 年化收益 | 最大回撤 | 结论 | 文件夹 |
|---|---:|---|---:|---:|---:|---|---|
| `baseline_full_liquidity_detail_market_gate` / 50% 仓位 | 50 万 | 2023-01-04 至 2026-06-02 | 475,270 | -1.54% | -43.42% | 三年回撤控制相对最好，可做防守影子候选 | `exports/signal_research/trusted_strategy_optimization_20260603_224953/` |
| `baseline_full_liquidity_detail` / hold12 | 50 万 | 2023-01-04 至 2026-06-02 | 489,717 | -0.64% | -65.17% | 三年持仓期矩阵相对最好，仍需降回撤 | `exports/signal_research/trusted_strategy_optimization_20260603_203215/` |
| `tiered_liquidity_then_bs_v2` / stop8 | 50 万 | 2023-01-04 至 2026-06-02 | 123,306 | -34.90% | -90.45% | 硬止损未修复进攻策略长期风险，不建议默认上线 | `exports/signal_research/trusted_strategy_optimization_20260603_212905/` |
| `tiered_liquidity_then_bs_v2_industry_cap1` | 50 万 | 2023-01-04 至 2026-06-02 | 100,684 | -38.82% | -88.50% | 行业集中下降但收益恶化，不建议默认上线 | `exports/signal_research/trusted_strategy_optimization_20260603_223619/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2023-01-04 至 2026-06-02 | 268,830 | -17.32% | -66.46% | 三年弱于防守半仓，可继续影子观察 | `exports/signal_research/trusted_strategy_optimization_20260603_232619/` |
| `baseline_full_liquidity_detail` | 50 万 | 2023-01-04 至 2026-06-02 | 283,045 | -16.01% | -75.27% | 完整三年口径相对最好，但仍为负收益 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2023-01-04 至 2026-06-02 | 268,830 | -17.32% | -66.46% | 三年回撤低于固定进攻，但仍未盈利 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `tiered_liquidity_then_bs_v2` | 50 万 | 2023-01-04 至 2026-06-02 | 146,850 | -31.31% | -94.20% | 最近一年强、三年回撤极深，不能单独长期满仓外推 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `baseline_full_score` | 50 万 | 2023-01-04 至 2026-06-02 | 121,280 | -35.23% | -91.67% | 基础综合分长期风险很高 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `adaptive_style_switch` | 50 万 | 2023-01-04 至 2026-06-02 | 99,602 | -39.02% | -88.72% | 硬切换规则三年未通过，需要继续优化 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `baseline_full_dynamic_factor_industry_cap2` | 50 万 | 2023-01-04 至 2026-06-02 | 30,490 | -57.58% | -96.23% | 动态因子行业约束在三年窗口失效严重 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `tiered_liquidity_then_bs_v2` | 50 万 | 2025-06-03 至 2026-05-29 | 1,544,720 | +228.49% | -23.28% | 最近一年固定策略第一 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `adaptive_style_switch` | 50 万 | 2025-06-03 至 2026-05-29 | 778,429 | +59.48% | -23.64% | 未跑赢固定进攻策略，仅研究观察 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2025-06-04 至 2026-05-29 | 730,623 | +49.17% | -17.80% | 降低回撤但牺牲收益，作为动态仓位研究观察 | `exports/signal_research/20260603_094422_665991_trusted_account_backtest/` |
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
