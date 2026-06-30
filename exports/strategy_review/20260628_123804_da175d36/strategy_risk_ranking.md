# 双项目策略风险排序

## Executive Summary

- **最高风险是 `tiered_liquidity_then_bs_v2`。** 历史保存账户最大回撤约 94.20%，回撤持续 762 个交易日，同时缺新鲜复现和容量证据。
- **ADC 的主要研究风险集中在数据与容量。** `plate_enhanced_v3_v8_locked` 严格历史股票池为 false，六个活跃年度全负，并出现 27–37 次年度容量突破。
- **所有 Cheny 账户策略均有执行证据缺口。** 新鲜严格回放在数据前置条件处失败，且没有第二套独立账本，因此历史保存账户结果只能诊断，不能晋级。

## 风险优先级

| 排名 | 策略 | 主风险 | 次风险 | 实际证据 | 决策 |
|---:|---|---|---|---|---|
| 1 | `tiered_liquidity_then_bs_v2` | 回撤 | 执行/集中度 | 全历史回撤 -94.20%，持续 762 日；单股 69.41%，行业 80.90% | BLOCKED |
| 2 | `baseline_full_liquidity_detail_vol_position` | 回撤 | 集中度/执行 | 回撤 -66.41%，持续 757 日；单股 74.53%，行业 90.32% | BLOCKED |
| 3 | `ashare_auto_shadow` | 集中度 | 短样本/执行 | 单股和行业均约 99.85%；仅 61 日历史保存结果 | BLOCKED |
| 4 | `adaptive_market_style` | 回撤 | 执行 | 回撤 -37.33%，持续 621 日；行业最大 61.61% | BLOCKED |
| 5 | `plate_enhanced_v3_v8_locked` | 数据/PIT | 容量/失效 | 严格历史池 false；六个活跃年度全负；年度容量突破 27–37 次 | RESEARCH_ONLY |
| 6 | `market_regime_timing_formal` | 过拟合/夹具 | 成本 | 仅 9 日，各折 IS/OOS Sharpe 完全相同，成本摘要缺失 | RESEARCH_ONLY |
| 7 | `ashare_hybrid_conservative_shadow` | 集中度 | 短样本/执行 | 61 日亏损 11.82%；单股 98.74%，行业 99.58% | RESEARCH_ONLY |
| 8 | `dual_system_adaptive_route` | 短样本 | 执行/容量 | 仅 61 日；回撤 -12.06%；无法新鲜复现 | BLOCKED |
| 9 | `plate_enhanced_v3` | 版本错配 | 样本/PIT | exact v3 只有零交易 smoke；v8_locked 不可替代 | RESEARCH_ONLY |
| 10 | `lgbm_shadow_model` | 数据谱系 | 弱表现 | 226 个非交易日观测；成本后 IR -1.749；缺快照与配置摘要 | RESEARCH_ONLY |

其余策略没有足够的策略级绩效证据可排序。对它们最准确的风险标签是“证据缺失”，而不是低风险。

## 风险类型归因

- **数据风险：** 全部策略；ADC 无 PIT pass，Cheny 无权威不可变数据快照。
- **过拟合/夹具风险：** `market_regime_timing_formal` 最突出；`plate_enhanced_v3` 的极短零交易 smoke 也不能外推。
- **回撤风险：** `tiered_liquidity_then_bs_v2`、`baseline_full_liquidity_detail_vol_position`、`adaptive_market_style`。
- **容量风险：** `plate_enhanced_v3_v8_locked` 已观察到压力；其余策略为 `NOT_VERIFIABLE`，不能当成容量无风险。
- **执行风险：** 所有 Cheny 策略；新鲜严格回放与双账本闭环均未成立。
