# Trusted Account 回测归档

## 1. 策略列表

- `tiered_liquidity_then_bs_v2`
- `adaptive_style_switch`
- `baseline_full_dynamic_factor_industry_cap2`
- `baseline_full_liquidity_detail`
- `baseline_full_score`

## 2. 关键问题

- 历史回测收益是否稳定？
- 最大回撤是否可接受？
- 月度波动是否适合 50 万启动资金？
- 是否适合进入实盘观察？
- 是否存在未来函数或模型字段穿越风险？

## 3. 最近一年核心结论

最近一年账户级 T+1 回测周期为 `2025-06-03` 至 `2026-05-29`，口径为 50 万初始资金、Top5、持有 10 个交易日、账户最多 5 只、单边成本 0.075%、无滑点。

| 策略 | 期末权益 | 总收益 | 最大回撤 | 结论 |
|---|---:|---:|---:|---|
| `tiered_liquidity_then_bs_v2` | 1,544,720 | +208.94% | -23.28% | 表现最好，后续重点观察。 |
| `adaptive_style_switch` | 778,429 | +55.69% | -23.64% | 未跑赢固定进攻策略，暂不生产化。 |
| `baseline_full_liquidity_detail` | 845,960 | +69.19% | -30.80% | 可作防守对照。 |
| `baseline_full_dynamic_factor_industry_cap2` | 827,899 | +65.58% | -32.90% | 均衡对照。 |
| `baseline_full_score` | 653,208 | +30.64% | -40.87% | 兜底基准。 |

## 4. 文件位置

最新回测输出目录：

```text
exports/signal_research/20260601_221903_213811_trusted_account_backtest/
```

关键文件：

- `trusted_account_backtest_summary.csv`
- `trusted_account_backtest_nav.csv`
- `trusted_account_backtest_positions.csv`
- `trusted_account_backtest_trades.csv`
- `trusted_account_backtest_candidates.csv`
- `trusted_account_backtest_adaptive_decisions.csv`
- `trusted_account_backtest_report.md`
- `trusted_account_backtest_report.json`

## 5. 未来函数控制

- 信号只使用 T 日已落库的 `score_rank_daily` 字段。
- 交易按 T+1 开盘价成交。
- 动态因子和自适应策略选择只使用 `exit_date < signal_date` 的已完成样本。
- 回测窗口内 `bs_model_*` 残留为 0，不纳入可信回测。
