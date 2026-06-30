# 未来 30 个交易日验证计划

## Executive Summary

- **前 5 日先修证据基础设施。** 若不可变数据快照、共同策略版本和双账本仍未建立，后续 25 日只能继续观察，不能积累可晋级证据。
- **全程只做影子记录，不使用资金晋级。** 当前没有 `SHADOW_ONLY` 或 `CANARY_ELIGIBLE` 策略。
- **30 日是执行闭环窗口，不是长期绩效证明。** 它可以证明候选、订单、成交、现金、持仓和 NAV 是否一致，不能替代多市场状态 OOS/WF 与容量验证。

## 分阶段计划

| 交易日 | 所有策略共同交付 | 失败判定 |
|---|---|---|
| D1–D5 | 固化共同数据快照 ID、公司行为、生命周期、权威日历；为每个候选映射固化策略版本与配置哈希；冻结成本、滑点、持仓、调仓、执行时点合同 | 任一缺失则继续 `BLOCKED/RESEARCH_ONLY` |
| D6–D10 | ADC 产出 exact-version PIT/OOS 候选；Cheny 以相同 strategy/version/snapshot 接收；记录候选 ID→订单草稿 ID | 无一对一关联则证据链失败 |
| D11–D20 | 两套独立影子账本并行消费同一冻结事件；逐单记录拒单、停牌、涨跌停、部分成交、费用、现金和持仓 | 任一无法独立计算或差异无法解释则 `BLOCKED` |
| D21–D25 | 每日核对现金、股数、持仓市值、交易数、NAV；补做成交率、ADV 占比、冲击成本和集中度 | 容量或集中度字段缺失则不得晋级 |
| D26–D30 | 汇总 30 日执行稳定性，并与历史多状态 OOS/WF 证据合并；委员会只评估是否满足下一轮影子观察 | 不得用 30 日年化或单只牛股贡献晋级 |

## 每个策略还缺什么

| 策略/策略组 | 未来 30 日必须补齐 | 30 日后最高可能阶段 |
|---|---|---|
| `AUTO↔ashare_auto_shadow` | 证明身份/信号/特征关系；降低近 100% 单股与行业集中；exact-version OOS/WF；双账本 | SHADOW_ONLY，不能直接 CANARY |
| `hybrid_conservative_v1↔ashare_hybrid_conservative_shadow` | 解决 2 仓/25% 与 5 仓/20% 规则冲突；共同快照；OOS/WF；账户重放；双账本 | SHADOW_ONLY |
| `trend_breakout_v1↔ashare_trend_breakout_shadow` | 证明身份；解释并跨状态验证零交易；建立有效交易样本与账本 | RESEARCH_ONLY 或 SHADOW_ONLY |
| `plate_enhanced_v3_v8_locked` | 严格历史股票池和逐行可见日；解释六年负收益与两年零交易；连续 NAV/逐笔账本；容量修复 | RESEARCH_ONLY |
| `plate_enhanced_v3` | exact v3 的多状态 OOS/WF 和组合回测；不得借用 v8_locked | RESEARCH_ONLY |
| `market_regime_timing_formal` | 非夹具、足够长、多折且 IS/OOS 不机械相同的 WF；完整成本摘要 | RESEARCH_ONLY |
| `lgbm_shadow_model` | 数据/配置摘要、交易日口径、账户化信号到成交链、成本与尾部风险 | RESEARCH_ONLY |
| `baseline_full_liquidity_detail_vol_position` | 新鲜双账本；修复 -66.41% 回撤及高集中；OOS/WF 和容量 | BLOCKED 或 SHADOW_ONLY |
| `adaptive_market_style` | 新鲜双账本；回撤/行业集中约束；OOS/WF 和容量 | BLOCKED 或 SHADOW_ONLY |
| `dual_system_adaptive_route` | 超越 61 日的多状态研究；双账本；容量与尾部风险 | BLOCKED 或 SHADOW_ONLY |
| `production_governed_vol_position` | 生成实际候选/订单/影子成交/NAV；双账本；补齐研究证据 | BLOCKED |
| `tiered_liquidity_then_bs_v2` | 先解决 -94.20% 灾难性回撤；再补 PIT/OOS/WF、双账本和容量 | BLOCKED |
| `chenyiyun_selected`、`repair_reversal_shadow` | 完整研究包、账户影子记录、双账本和容量 | RESEARCH_ONLY |
| 其余 ADC 注册策略与组件 | exact-version PIT、OOS、WF、组合回测、成本容量、候选到账本链 | RESEARCH_ONLY |

## 30 日验收清单

每个拟晋级策略至少要交付：同一策略版本/配置哈希、同一快照与 PIT 证明、T 日信号/T+1 执行记录、共同成本与成交规则、逐候选/订单/成交/持仓/现金/NAV 链、两套独立账本零或可解释残差、30 日全量影子执行、历史多状态 OOS/WF、容量与集中度压力测试。

即使以上全部完成，30 日样本本身也只支持进入或延长 `SHADOW_ONLY`；只有历史研究证据与账户/执行闭环同时通过委员会门槛，才可另行评估 `CANARY_ELIGIBLE`。
