# 双项目不可比事项

## Executive Summary

- **所有跨项目策略均为 `NON_COMPARABLE`。** 两个审计包没有共同策略版本、共同快照 ID、共同配置摘要或逐笔跨项目关联键。
- **名称相近不构成身份映射。** `AUTO↔ashare_auto_shadow`、`hybrid_conservative_v1↔ashare_hybrid_conservative_shadow`、`trend_breakout_v1↔ashare_trend_breakout_shadow` 仅是候选语义映射；同源、共享信号、共享特征均为 `NOT_VERIFIABLE`。
- **研究收益与账户收益不能串接。** ADC 的 selector OOS、年度组合摘要和 walk-forward 不能解释 Chenyiyun2087 的历史保存账户 NAV；后者也不能补足 ADC 的 PIT、OOS、WF 与容量缺口。

## 阻断可比性的实际差异

| 维度 | ADC 审计证据 | Chenyiyun2087 审计证据 | 判定 |
|---|---|---|---|
| 数据快照 | 参考快照 `dv-20260417...`，但历史工件不一定使用同一快照 | 权威快照日期 `NOT_VERIFIABLE` | `NON_COMPARABLE` |
| PIT | 无策略通过；历史股票池严格性为 false，财务可见日等缺逐行证明 | 公司行为、证券生命周期与权威日历快照缺失 | `NON_COMPARABLE` |
| 策略身份 | ADC 注册表/历史实验对象 | Cheny 策略卡与账户回测名称 | 无共同版本键或哈希 |
| 配置 | ADC exact v3 与 v8_locked 已明确不是同一版本 | 策略卡有独立 SHA-256 | 无跨项目一致性证明 |
| 执行时点 | v8_locked 为 `open_auction`；其他层缺完整时点账本 | `T_15_30` 信号、`T+1_OPEN` 执行 | 除表面相似外不可核验 |
| 持仓规则 | v8_locked Top 3、持有 20 日；hybrid 治理描述最多 2 仓、单仓 25% | 多数策略 5 仓、10 日、单仓上限 20% | 已知不一致或不可核验 |
| 成本 | v8_locked 有 `cn_a_share_fee_schedule_v20260326`；其他层不完整 | 保存运行单边费率 0.00075、滑点 0；最低佣金等不可核验 | 无共同成本合同 |
| 账户模型 | ADC 无可重算连续 NAV/逐笔账本 | 历史保存 NAV/交易存在，但新鲜严格回放失败 | 不能闭环 |
| 严格账本 | 无账户级纸盘账本 | 严格账本没有第二套独立可执行账本可对账 | 不能声称一致 |
| 容量 | v8_locked 有 27–37 次容量突破、平均成交率约 50%–54% | 无权威 ADV/冲击成本快照 | 不能横向比较 |
| 市场覆盖 | v8_locked 2018–2023 有交易，2024–2025 零交易 | 长样本最多约 2023–2026；多策略仅 61 日 | 共同市场状态未建立 |

## 不能用于晋级的表象

- ADC `market_regime_timing_formal` 的 9 日、各折 IS/OOS Sharpe 完全相同且成本摘要缺失，属于 fixture 风险，不是有效 WF。
- ADC `plate_enhanced_v3_v8_locked` 六个活跃年度收益全负，2024–2025 零交易，且容量压力显著；它不是“研究表现好”的晋级候选。
- Cheny 的一年、半年、三个月年化指标均被源文件标注“样本不足”，不得用作长期能力证明。
- Cheny 的 `ashare_auto_shadow` 单股/行业最大权重约 99.85%，不能用 61 日正收益支持晋级。
- Cheny 的 `tiered_liquidity_then_bs_v2` 全历史回撤约 94.20%，近期短窗正收益不能覆盖该风险。

## 证据边界

本报告只使用两个指定审计目录内的文件。审计文件提及的原始仓库路径、数据库、策略卡和历史运行目录未被打开；其中的结论仅按审计文件已有陈述引用。
