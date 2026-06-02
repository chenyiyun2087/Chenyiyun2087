# 策略研究索引

| 主题 | 目录/文件 | 当前状态 | 最新结论 |
|---|---|---|---|
| 可信全量池流动性策略 | `docs/tasks/20260512-full-pool-liquidity-strategy.md` | 持续迭代 | 最近一年固定 `tiered_liquidity_then_bs_v2` 表现最好；自适应切换暂不替换生产默认。 |
| 生产操作手册 | `docs/production_trusted_strategy_usage.md` | 可用 | 日终候选导出、订单草案、飞书通知和影子盘监控已接入。 |
| 行业研究 | `docs/01_strategy_research/industry/` | 待迁入 | 半导体、机器人、农业、互联网基金等主题待整理。 |
| 资产配置 | `docs/01_strategy_research/portfolio/` | 待迁入 | 个人资产配置、基金风险收益等主题待整理。 |
| 筛选框架 | `docs/01_strategy_research/screening_framework/` | 待迁入 | 高弹性、低估修复、估值指标等框架待整理。 |

## 重点策略池

| 策略 | 类型 | 用途 | 备注 |
|---|---|---|---|
| `tiered_liquidity_then_bs_v2` | 进攻 | 最近一年账户级回测第一 | 适合作为重点研究和生产候选对照。 |
| `baseline_full_dynamic_factor_industry_cap2` | 均衡 | 当前生产默认候选策略之一 | 使用已完成样本估计动态因子权重。 |
| `baseline_full_liquidity_detail` | 防守 | 低回撤/流动性质量对照 | 可作为风险偏好下降时的备选。 |
| `baseline_full_score` | 兜底 | 综合分基准 | 历史样本不足或增强字段异常时使用。 |
| `adaptive_style_switch` | 研究 | 市场风格硬切换 | 最近一年未跑赢固定进攻策略，暂不生产化。 |

## 后续整理项

- 将行业研究迁入 `industry/`。
- 将资产配置和筛选方法迁入 `portfolio/`、`screening_framework/`。
- 将每次重要回测摘要同步到 `docs/03_backtest_reports/BACKTEST_INDEX.md`。
