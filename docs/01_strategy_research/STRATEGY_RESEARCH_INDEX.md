# 策略研究索引

| 主题 | 目录/文件 | 当前状态 | 最新结论 |
|---|---|---|---|
| 可信全量池流动性策略 | `docs/tasks/20260512-full-pool-liquidity-strategy.md` | 持续迭代 | 当前生产默认底座为 `production_governed_vol_position`，底层选股引擎为 `baseline_full_liquidity_detail_vol_position`，`adaptive_market_style` 保留为高效率挑战者。 |
| 生产操作手册 | `docs/production_trusted_strategy_usage.md` | 可用 | 日终候选导出、订单草案、飞书通知和影子盘监控已接入。 |
| 行业研究 | `docs/01_strategy_research/industry/` | 待迁入 | 半导体、机器人、农业、互联网基金等主题待整理。 |
| 资产配置 | `docs/01_strategy_research/portfolio/` | 待迁入 | 个人资产配置、基金风险收益等主题待整理。 |
| 筛选框架 | `docs/01_strategy_research/screening_framework/` | 待迁入 | 高弹性、低估修复、估值指标等框架待整理。 |

## 重点策略池

| 策略 | 类型 | 用途 | 备注 |
|---|---|---|---|
| `production_governed_vol_position` | 生产默认底座 | vol_position 进攻引擎 + 生产风险总闸 | 2023-01-04 至 2026-06-17 三年收益 +19.94%、年化 +7.75%、最大回撤 -24.81%，`missed_risk_events=0`，已固化为当前生产默认。 |
| `baseline_full_liquidity_detail_vol_position` | 底层选股引擎 | 收益优先近期冠军策略 | 继续作为 production governed 的主选股引擎；裸跑不再作为生产默认。 |
| `adaptive_market_style` | 挑战者/风控影子 | 市场风格自适应生产策略 | 最新三年矩阵回测收益 +44.91%、年化 +16.44%、最大回撤 -26.68%，平均仓位 37.80%，资本效率明显高于 governed；先保留为每日对照和归因对象，不直接替换生产默认。 |
| `production_governed_adaptive` | 研究失败候选 | adaptive 路由 + 生产风险总闸 | 三年收益 -11.79%、最大回撤 -52.06%，说明把 adaptive 路由直接包进当前 governor 规则会破坏原 adaptive 的低仓位优势，不进入生产候选。 |
| `production_governed_adaptive_pattern_guard` | 研究失败候选 | adaptive 路由 + governor + 图形 high-risk guard | 三年收益 -11.79%、最大回撤 -52.06%、`missed_risk_events=6`，未达下一代候选门槛；图形识别继续只做研究和影子风控验证。 |
| `tiered_liquidity_then_bs_v2` | 进攻 | 最近一年账户级回测第一 | 适合作为重点研究和生产候选对照。 |
| `baseline_full_liquidity_detail_market_gate` | 均衡 | adaptive 的 balanced 底层策略 | 普通市场环境默认使用，目标仓位约 80%。 |
| `baseline_full_liquidity` | 防守 | adaptive 的 defensive / fallback 底层策略 | 弱市场、缩量或数据不足时使用，目标仓位约 50%。 |
| `baseline_full_liquidity_detail` | 防守对照 | 流动性质量对照 | 可作为风险偏好下降时的备选和回测对照。 |
| `baseline_full_score` | 兜底 | 综合分基准 | 历史样本不足或增强字段异常时使用。 |
| `adaptive_style_switch` | 历史研究 | 市场风格硬切换 | 旧硬切换版本，三年和最近一年均不作为生产默认。 |

## 后续整理项

- 将行业研究迁入 `industry/`。
- 将资产配置和筛选方法迁入 `portfolio/`、`screening_framework/`。
- 将每次重要回测摘要同步到 `docs/03_backtest_reports/BACKTEST_INDEX.md`。
