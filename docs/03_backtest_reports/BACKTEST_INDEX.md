# 回测报告索引

> 2026-06-18 governed 矩阵验收更新：`production_governed_vol_position` 已作为当前生产默认底座，主选股引擎仍为 `baseline_full_liquidity_detail_vol_position`，风险总闸使用 `production_risk_governor`。2023-01-04 至 2026-06-17 三年 T+1 账户级回测收益 +19.94%、年化 +7.75%、最大回撤 -24.81%、`missed_risk_events=0`；`adaptive_market_style` 同期收益 +44.91%、年化 +16.44%、最大回撤 -26.68%，资本效率更强但先保留为挑战者和风险锚。`production_governed_adaptive_pattern_guard` 三年收益 -11.79%、最大回撤 -52.06%、`missed_risk_events=6`，不得进入生产候选。

> 2026-06-18 governor v2 研究更新：`production_governed_vol_position_v2` 引入 `soft_reduce / hard_reduce` 分层后，三年收益 -1.80%、年化 -0.74%、最大回撤 -29.29%、`missed_risk_events=20`；虽然 false positive reduce days 从 132 降到 122，但风险收益显著恶化，不能进入生产候选。当前生产默认继续保持 `production_governed_vol_position`。

> 2026-06-18 v1.1 selective recovery 更新：`production_governed_vol_position_v1_1_recovery` 三年收益 +41.89%、年化 +15.44%、最大回撤 -25.65%，最差 20 日 -16.65%，平均仓位 59.17%；但 `missed_risk_events=8`，false positive reduce days 仅从 132 降到 118，未达到“missed_risk_events<=0、误降仓下降至少15%”门槛。结论：v1.1 是强观察候选，不进入当前生产默认。

> 2026-06-18 v1.2 missed-risk 清零研究更新：新增 `production_governed_vol_position_v1_2_recovery` 和 pattern veto 版本，首版参数为 `champion_score_floor=-0.03`、`recovery_position=0.58`、`nav_ret_10d_kill=-0.04`、`nav_dd_20d_kill=-0.08`、`max_recovery_streak=5`。三年结果与当前生产 v1 完全一致：收益 +19.94%、年化 +7.75%、最大回撤 -24.81%、`missed_risk_events=0`、`false_positive_reduce_days=132`、`recovery_days=0`。结论：v1.2 首版安全但过度保守，未达到收益提升和误降仓下降目标，不进入生产候选。

> 2026-06-04 更新：当前 `adaptive_market_style` 从单纯三年低回撤口径升级为“最近 3 个月收益优先 + 长期风险约束 + 日检周切”。近期冠军默认指向 `baseline_full_liquidity_detail_vol_position`，系统每天检测市场、行业和量能状态，最多每周切换一次底层基准，目标仓位约 50% / 70% / 80%。`tiered_liquidity_then_bs_v2` 只在强市场短期增强，不作为长期满仓默认。

> 2026-06-04 双系统路由更新：新增 `dual_system_adaptive_route`、`ashare_auto_shadow`、`ashare_trend_breakout_shadow`、`ashare_hybrid_conservative_shadow`。最近 3 个月账户级对照中，AShare AUTO 影子收益约 +8.72%、最大回撤约 -17.28%，`adaptive_market_style` 收益约 +7.36%、最大回撤约 -10.97%，双系统路由收益约 +5.27%、最大回撤约 -12.06%。当前结论：AShare AUTO 有收益弹性但波动更大，dual route 风控较保守，需继续优化 AShare 周线门禁和候选缓存后再跑三年验收。

> 2026-06-04 v2.1 更新：`adaptive_market_style` 已加入 AShare 加权增强，AShare 周线未确认不再硬剔除，而是降权；AShare 补位最多 2 只，ST 类外部候选硬过滤。最近半年窗口收益约 +35.14%、最大回撤约 -11.09%。三年硬底线 `-45%` 尚未完成自动验收，三年单策略回测超过 4 分钟未返回，需继续缓存 adaptive perf 与路由目标后复跑。

> 2026-06-05 v2.2 更新：`adaptive_market_style` 升级为收益优先生产版，正式标记 `adaptive_version=v2.2`、`ashare_weight_profile=prod_stage1`、`ashare_release_tier=production_stage1`，默认 AShare 补位上限 2 只。新增路由磁盘缓存与防守态风险叠加：防守状态且近期冠军分数转负时，目标仓位从 50% 降至 45%。三年 T+1 账户级回测收益约 +42.09%、年化约 +11.36%、最大回撤约 -37.33%，满足三年最大回撤不劣于 -45% 的硬底线。

> 2026-06-05 主推送切换：飞书生产主策略改为 `baseline_full_liquidity_detail_vol_position`，默认仓位 70%。该策略最近 3 个月收益约 +36.71%、最大回撤约 -12.87%，最近半年收益约 +84.99%、最大回撤约 -28.75%，但完整三年裸跑最大回撤约 -66.41%。因此本次是“收益优先主推送”，不是长期满仓无门禁策略；`adaptive_market_style` v2.2 继续保留为每日市场/行业状态风控影子对照。

| 策略名称 | 资金规模 | 回测周期 | 期末权益 | 年化收益 | 最大回撤 | 结论 | 文件夹 |
|---|---:|---|---:|---:|---:|---|---|
| `adaptive_market_style` / 挑战者对照 | 50 万 | 2023-01-04 至 2026-06-17 | 724,539 | +16.44% | -26.68% | 三年收益 +44.91%，平均仓位 37.80%，年化/仓位效率高于 governed；仍需专项归因验证，不直接替换生产默认 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `production_governed_vol_position_v1_1_recovery` / selective recovery 强观察 | 50 万 | 2023-01-04 至 2026-06-17 | 709,468 | +15.44% | -25.65% | 三年收益 +41.89%，收益和回撤接近挑战者，但 `missed_risk_events=8`，暂不进入生产默认 | `exports/signal_research/20260618_193951_941705_trusted_account_backtest/` |
| `production_governed_vol_position_v1_2_recovery` / missed-risk 清零过保守样本 | 50 万 | 2023-01-04 至 2026-06-17 | 599,703 | +7.75% | -24.81% | `missed_risk_events=0`，但 `recovery_days=0`、false positive reduce days 仍为 132，未带来收益或误降仓改善 | `exports/signal_research/20260618_204722_645522_trusted_account_backtest/` |
| `production_governed_vol_position` / 当前生产默认底座 | 50 万 | 2023-01-04 至 2026-06-17 | 599,703 | +7.75% | -24.81% | 三年收益 +19.94%，相比裸 `vol_position` 收益提升且回撤下降；`missed_risk_events=0`，作为当前生产默认固化 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `production_governed_vol_position_v2` / soft-hard reduce 研究失败样本 | 50 万 | 2023-01-04 至 2026-06-17 | 490,997 | -0.74% | -29.29% | 三年收益 -1.80%，`missed_risk_events=20`；误降仓仅小幅下降，不满足最大回撤和收益目标 | `exports/signal_research/20260618_131158_324863_trusted_account_backtest/` |
| `production_governed_adaptive_pattern_guard` / 下一代候选失败样本 | 50 万 | 2023-01-04 至 2026-06-17 | 441,040 | -5.02% | -52.06% | 三年收益 -11.79%，最坏归因 `missed_risk_events=6`，未达“回撤不劣于当前 governed”门槛，不进入生产候选 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `baseline_full_liquidity_detail_vol_position` / 飞书主推送 | 50 万 | 2023-01-04 至 2026-06-02 | 592,007 | +5.32% | -66.41% | 最近 3 个月 +36.71%、半年 +84.99%、一年 +177.92%；近期收益弹性最强，但三年回撤深，默认生产仓位降为 70%，并保留 adaptive 防守影子对照 | `exports/signal_research/20260604_152142_206060_trusted_account_backtest/` |
| `adaptive_market_style` / AShare 加权增强 v2.2 | 50 万 | 2023-01-04 至 2026-06-04 | 710,445 | +11.36% | -37.33% | 三年收益约 +42.09%，默认 AShare 补位上限 2 只；防守态近期冠军转负时降至 45% 仓位，已通过 `-45%` 回撤硬底线 | `exports/signal_research/20260605_004258_229723_trusted_account_backtest/` |
| `adaptive_market_style` / AShare 加权增强 v2.1 | 50 万 | 2025-12-04 至 2026-06-04 | 675,724 | +91.30% | -11.09% | 半年收益约 +35.14%，周线未确认走降权，AShare 补位最多 2 只；三年硬底线待缓存优化后复跑 | `exports/signal_research/20260604_231724_811158_trusted_account_backtest/` |
| `dual_system_adaptive_route` / v2.1 对照 | 50 万 | 2025-12-04 至 2026-06-04 | 640,090 | +70.23% | -9.26% | 半年收益约 +28.02%，回撤低于 adaptive，但收益也低于正式 adaptive | `exports/signal_research/20260604_231724_811158_trusted_account_backtest/` |
| `dual_system_adaptive_route` / 双系统路由 | 50 万 | 2026-03-03 至 2026-06-02 | 526,350 | +24.07% | -12.06% | 最近 3 个月收益约 +5.27%，低于 `adaptive_market_style` 和 AShare AUTO；路由状态含 18 天 attack、22 天 neutral、14 天 defensive、7 天 freeze | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `ashare_auto_shadow` / AShare AUTO 影子 | 50 万 | 2026-03-03 至 2026-06-02 | 543,590 | +42.06% | -17.28% | 最近 3 个月收益约 +8.72%，收益弹性最高但波动和回撤也最高，仅作外部源影子观察 | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `adaptive_market_style` / 双系统对照窗口 | 50 万 | 2026-03-03 至 2026-06-02 | 536,817 | +34.77% | -10.97% | 最近 3 个月收益约 +7.36%，回撤低于 AShare AUTO，是当前双系统路由的主要对照基准 | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `adaptive_market_style` / 3个月收益优先周切换 | 50 万 | 2023-01-04 至 2026-06-02 | 696,564 | +10.71% | -43.01% | 三年收益约 +39.31%，回撤低于单独 `vol_position`；最近 3 个月 +6.39%、半年 +41.73%、一年 +47.67% | `exports/signal_research/20260604_152142_206060_trusted_account_backtest/` |
| 核心策略风格画像研究 | - | 2023-01-04 至 2026-06-02 | - | - | - | 输出市场/行业/量能分组、网格阈值和每日 adaptive 决策表 | `exports/signal_research/20260604_105452_core_strategy_style_research/` |
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
