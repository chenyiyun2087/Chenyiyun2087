# 策略研究索引

| 主题 | 目录/文件 | 当前状态 | 最新结论 |
|---|---|---|---|
| 可信生产闭环 | `docs/01_strategy_research/2026-07-20_可信生产闭环升级.md` | 工程实现，正式证据阻断 | 统一身份、PIT、双账本、NAV 风险、人工执行和 12/3/3 赛马；未产生新收益结论，保持 `BLOCKED / NO_SCALE`。 |
| 三个月冠军策略轮动 | `docs/01_strategy_research/2026-07-13_三个月冠军策略轮动研究实施.md` | v1.1研究实现，数据源阻断 | 63日冠军、126日确认、财报季加严、失效/成本/置信度保护及顺序晋级状态机；数据库凭证、严格账本或真实影子证据缺失时 fail closed。 |
| 全策略统一赛马与生产门禁 | `docs/01_strategy_research/2026-07-12_全策略统一赛马与生产门禁.md` | 已实现，正式证据阻断 | 单策略与组合使用同一 fail-closed 赛马契约；无人通过 20%净年化、20%回撤、统计、账本、成本和容量硬门槛时不晋级。 |
| Full Strategy V3 PR16 单一经济路径 | `docs/01_strategy_research/2026-07-10_Full_Strategy_V3_PR16_单一经济路径.md` | PR16验证通过，待合并 | P0/C0 已由显式 runtime 贯通；真实60交易日复制候选、Top5、权重、仓位和退出均为0差异。Alpha/Risk/Exit仍保持研究阻断，待PR17三窗口证据。 |
| 可信全量池流动性策略 | `docs/tasks/20260512-full-pool-liquidity-strategy.md` | 持续迭代 | 当前生产默认底座为 `production_governed_vol_position`，底层选股引擎为 `baseline_full_liquidity_detail_vol_position`，`adaptive_market_style` 保留为高效率挑战者。 |
| 生产操作手册 | `docs/production_trusted_strategy_usage.md` | 可用 | 日终候选导出、订单草案、飞书通知和影子盘监控已接入。 |
| 行业研究 | `docs/01_strategy_research/industry/` | 待迁入 | 半导体、机器人、农业、互联网基金等主题待整理。 |
| 资产配置 | `docs/01_strategy_research/portfolio/` | 待迁入 | 个人资产配置、基金风险收益等主题待整理。 |
| 筛选框架 | `docs/01_strategy_research/screening_framework/` | 待迁入 | 高弹性、低估修复、估值指标等框架待整理。 |

## 重点策略池

| 策略 | 类型 | 用途 | 备注 |
|---|---|---|---|
| `production_governed_vol_position` | 生产默认底座 | vol_position 进攻引擎 + 生产风险总闸 | 2023-01-04 至 2026-06-17 三年收益 +19.94%、年化 +7.75%、最大回撤 -24.81%，`missed_risk_events=0`，已固化为当前生产默认。 |
| P0 可信度修复 | 发布与报告治理 | 冻结 dynamic-score Champion、策略身份 fail-closed、严格配置和 provenance | 当前生产路由不变；所有核心策略在重新验证前禁止晋级和扩容。详见 `docs/tasks/2026-07-10_p0可信度修复.md`。 |
| `production_governed_vol_position_v1_1_recovery` | 强观察候选 | v1 底座 + selective recovery | 三年收益 +41.89%、年化 +15.44%、最大回撤 -25.65%，但 `missed_risk_events=8`、误降仓只从 132 天降至 118 天，未达上线门槛。 |
| `production_governed_vol_position_v1_1_recovery_pattern_veto` | 强观察候选 | v1.1 + 图形风险否决 | 当前三年结果与 v1.1 相同，说明本轮图形 veto 未形成增量风险过滤；继续研究，不进生产默认。 |
| `production_governed_vol_position_v2` | 研究失败候选 | soft/hard reduce 分层 governor | 三年收益 -1.80%、年化 -0.74%、最大回撤 -29.29%，`missed_risk_events=20`；误降仓仅从 132 天降至 122 天，不满足生产候选门槛。 |
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
