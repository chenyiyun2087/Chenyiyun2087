# 动态评分冠军策略实盘准入全面评估

> 评估时间：2026-07-27T13:40:36+08:00；策略：`production_governed_vol_position_v1_2b_dynamic_score`；release：`champion-v1-2b-dynamic-score-20260618`。

## Executive Summary

- **结论：`NO_GO`。** 当前允许新增风险资金为 **0 元**，不得启用Canary。
- **冻结回测累计收益 57.23%、年化 20.41%、最大回撤 -25.52%。** 但样本仅覆盖 2023-11-30 至 2026-06-17，不满足2013年至今的正式长周期门禁。
- **仍有 9 个硬门禁未通过。** 主要缺口是正式快照、长周期回测、Walk-forward、严格账本、统计稳健性、成本容量压力和80个真实Shadow交易日。
- **当前生产路由不变。** 动态评分冠军保持研究/阻塞状态，不继承既有本金例外，券商API保持关闭。

## PR-A至PR-E升级证据

| 阶段 | 范围 | 业务证据状态 | 阻塞或结论 |
|---|---|---|---|
| PR-A | `pr_a_equivalence` | BLOCKED | replay_directory_missing; web_benchmark_evidence_missing |
| PR-B | `pr_b_formal_readiness` | BLOCKED | frozen_package |
| PR-C | `pr_c_formal_run` | BLOCKED | preflight_not_ready |
| PR-D | `pr_d_oos_robustness` | BLOCKED | formal_run_not_verified |
| PR-E | `pr_e_execution_capacity` | BLOCKED | formal_run_not_verified |

代码与本地CI通过只证明失败关闭基础设施可用；业务证据在正式PIT输入缺失时仍保持BLOCKED，不能用于放行资金。

## 绩效与风险概览

| 指标 | 数值 |
|---|---:|
| 样本交易日 | 615 |
| 累计收益 | 57.23% |
| 年化收益 | 20.41% |
| 年化波动率 | 26.66% |
| 最大回撤 | -25.52% |
| 日度VaR 95% | -2.61% |
| 日度CVaR 95% | -3.91% |
| FIFO闭环交易 | 306 |
| 闭环胜率 | 51.31% |
| 盈亏比 | 1.36 |
| 利润因子 | 1.39 |
| 最大连续亏损 | 7 |
| 最佳月份 | 2025-08（17.13%） |
| 最差月份 | 2024-08（-10.39%） |

## 月度收益

| 月份 | 月度收益 | 累计收益 |
|---|---:|---:|
| 2023-11 | 0.78% | 0.78% |
| 2023-12 | -3.66% | -2.91% |
| 2024-01 | -4.27% | -7.06% |
| 2024-02 | 2.59% | -4.66% |
| 2024-03 | 7.03% | 2.05% |
| 2024-04 | -0.38% | 1.65% |
| 2024-05 | -2.29% | -0.67% |
| 2024-06 | -0.02% | -0.70% |
| 2024-07 | -3.99% | -4.66% |
| 2024-08 | -10.39% | -14.57% |
| 2024-09 | 6.63% | -8.91% |
| 2024-10 | 3.00% | -6.18% |
| 2024-11 | -4.60% | -10.50% |
| 2024-12 | -5.40% | -15.33% |
| 2025-01 | -3.20% | -18.04% |
| 2025-02 | 0.73% | -17.44% |
| 2025-03 | 0.73% | -16.84% |
| 2025-04 | -2.05% | -18.55% |
| 2025-05 | -3.59% | -21.47% |
| 2025-06 | 0.01% | -21.46% |
| 2025-07 | 2.78% | -19.28% |
| 2025-08 | 17.13% | -5.46% |
| 2025-09 | 12.32% | 6.18% |
| 2025-10 | -8.01% | -2.32% |
| 2025-11 | -5.97% | -8.15% |
| 2025-12 | 9.37% | 0.45% |
| 2026-01 | 15.49% | 16.01% |
| 2026-02 | 7.94% | 25.23% |
| 2026-03 | -6.32% | 17.31% |
| 2026-04 | 6.01% | 24.35% |
| 2026-05 | 13.65% | 41.32% |
| 2026-06 | 11.25% | 57.23% |

## 实盘准入门禁

| 类别 | 门禁 | 状态 | 当前证据 | 修复动作 |
|---|---|---|---|---|
| 数据与发布身份 | `release_identity` | BLOCKED | registry=FAILED_REVALIDATION; PR-A=BLOCKED; PR-B=BLOCKED | 生成并冻结缺失快照，绑定新的不可变release证据包。 |
| 长周期回测 | `full_history` | BLOCKED | 2023-11-30至2026-06-17，615个交易日；PR-C=BLOCKED | 使用正式PIT快照从2013年重跑全部市场状态和基础/压力场景。 |
| Walk-forward | `rolling_oos` | BLOCKED | registry=FAILED; PR-D=BLOCKED | 在冻结样本上完成带purge/embargo的滚动OOS，测试窗不得继续调参。 |
| 严格账本 | `strict_ledger` | BLOCKED | registry=FAILED_REVALIDATION; PR-C=BLOCKED | 补齐公司行动和生命周期快照，生成逐release对账指标并通过严格账本Gate。 |
| 统计稳健性 | `statistical_robustness` | BLOCKED | PR-D=BLOCKED; technical_complete=False | 对完整OOS收益运行DSR、CPCV-PBO、Block Bootstrap和七因子归因。 |
| 成本、滑点与容量 | `execution_stress` | BLOCKED | 当前冻结回测滑点=0.00%；PR-E=BLOCKED | 按50万至1000万元及全部成本/滑点组合重跑并保存拒单、冲击和回撤扩张。 |
| 技术Shadow | `disabled_shadow` | BLOCKED | 0日；missing_release_scoped_shadow_evidence | 从同日正式PIT快照开始累计，历史回填不得计数。 |
| 经济Shadow | `economic_shadow` | BLOCKED | 0日、0个闭环 | 技术Shadow通过后再累计成本后Alpha、成交偏差和逐日对账证据。 |
| 评估报告 | `comprehensive_report` | PASS | 已生成；Artifact与UTF-8校验结果随证据包归档 | 发布前完成Artifact与UTF-8校验。 |
| 人工审批 | `manual_approval` | BLOCKED | 缺失 | 仅在其余门禁全部通过后记录人工审批。 |
| 执行边界 | `broker_api_boundary` | PASS | broker_api_enabled=false | 保持人工订单草案、成交文件导入和离线对账。 |

## 数据质量与可复现性

- 冻结回测文件均已生成SHA-256清单；同一输入应产生相同的评估包哈希。
- 当前策略身份可精确解析，但发布注册表中的日历、公司行动和生命周期快照仍为PENDING。
- 当前冻结回测滑点参数为0，不能据此判断真实可实现收益或容量。
- 当前仓库中的Shadow报告属于其他策略或其他release，不能计入本策略20+60日门禁。

## 资金分级路径

| 阶段 | 资金 | 最低真实交易日 | 最低闭环数 | 当前状态 |
|---|---:|---:|---:|---|
| CANARY_10 | 50,000元 | 60 | 30 | BLOCKED |
| CANARY_25 | 125,000元 | 60 | 30 | BLOCKED |
| CANARY_50 | 250,000元 | 60 | 30 | BLOCKED |
| CANARY_100 | 500,000元 | 60 | 30 | BLOCKED |

## 下一步

1. 使用正式PIT快照重跑2013年至今的长周期、市场状态和成本容量矩阵。
2. 完成12/3/3 Walk-forward、DSR、CPCV-PBO、Block Bootstrap和七因子归因。
3. 补齐公司行动和证券生命周期快照，使严格账本达到 `VERIFIED`。
4. 从同日正式PIT数据开始累计20日技术Shadow，再累计60日经济Shadow和30个闭环。
5. 仅当本报告更新为 `GO` 并绑定人工审批后，启用5万元人工Canary。

## Caveats and Assumptions

- 本报告是仓库中已保存证据的快照，不是实时数据库或券商账户连接。
- 回测结果不是实盘业绩，不对未来收益作保证。
- 历史模拟和跨策略Shadow证据不会计入本release的真实观察日。
- 缺失证据按失败处理，生产底座和现有资金边界保持不变。
