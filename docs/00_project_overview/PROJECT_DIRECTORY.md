# 项目目录说明

本文档用于约定 Chenyiyun2087 的目录边界，避免代码、研究文档、回测结果和临时材料混放。

本规范是后续项目管理的默认标准。仓库级执行规则见 `AGENTS.md`；新增文件、研究材料、回测结果和提示词默认按本文档归档，除非有明确迁移任务。

## 1. 代码目录

| 目录 | 用途 |
|---|---|
| `sina/` | Sina B/S 信号体系，包括截图、识别、实盘跟踪。 |
| `scoreRank/` | 评分与 M2~M8 策略研究中台。 |
| `chenyiyunSelected/` | 陈依云本地策略体系。 |
| `backtest/` | 通用回测框架。 |
| `web/` | Web 看板、任务中心和页面模板。 |
| `eastmoney/` | 东方财富舆情与相关数据处理。 |
| `scripts/ops/` | 日常生产、调度、导出、监控脚本。 |
| `scripts/research/` | 研究辅助脚本。新研究脚本优先放这里。 |
| `scripts/maintenance/` | 清理、修复、迁移、历史补数脚本。 |
| `scripts/export/` | 独立导出类脚本。 |
| `test/` | 现有测试目录。后续若统一迁移，可再改名为 `tests/`。 |

## 2. 文档目录

| 目录 | 用途 |
|---|---|
| `docs/00_project_overview/` | 项目架构、目录说明、运行手册。 |
| `docs/01_strategy_research/` | 策略研究、行业框架、资产配置、筛选方法。 |
| `docs/02_stock_research/` | 个股研究和持续跟踪档案。 |
| `docs/03_backtest_reports/` | 回测结果归档和索引。 |
| `docs/04_live_trading/` | 实盘、模拟盘、影子盘记录。 |
| `docs/05_external_reports/` | 外部研报、券商报告解读。 |
| `docs/06_prompt_library/` | 提示词、分析模板、工作流。 |
| `docs/99_archive/` | 历史废弃、旧版本、临时资料。 |
| `docs/tasks/` | 开发任务登记，不放研究正文。 |

## 3. 数据与导出目录

| 目录 | 用途 |
|---|---|
| `data/raw/` | 原始导入数据。 |
| `data/processed/` | 清洗后数据。 |
| `data/external/` | 外部数据包。 |
| `data/samples/` | 小样本测试数据。 |
| `exports/signal_enhancement/` | B 点增强数据包。 |
| `exports/backtest/` | 通用回测导出。 |
| `exports/signal_research/` | 策略研究脚本自动输出。 |
| `exports/score_backfill/` | 历史评分补齐日志和报告。 |
| `exports/production_candidates/` | 生产候选导出。 |
| `exports/reports/` | 人工整理后的报告导出。 |
| `exports/charts/` | 图片、图表、路演图。 |

## 4. 命名规范

- 个股研究：`YYYY-MM-DD_股票代码_主题.md`
- 策略回测：`YYYY-MM-DD_策略名_回测摘要.md`
- 行业研究：`YYYY-MM-DD_行业_产业链梳理.md`
- 基金研究：`YYYY-MM-DD_基金代码_分析.md`

示例：

- `2026-05-31_603667_五洲新春存货与利润影响分析.md`
- `2026-06-01_tiered_liquidity_then_bs_v2_50万最近一年回测摘要.md`
- `2026-05-28_半导体_野村研报产业链整理.md`

## 5. 整理原则

- 不移动生产代码目录，避免破坏脚本路径。
- 自动生成的原始输出留在 `exports/` 原目录，文档归档只做索引和摘要。
- 研究结论写入 `docs/`，交易执行和监控记录写入 `docs/04_live_trading/`。
- 临时材料先进入 `docs/99_archive/`，确认有长期价值后再迁入正式目录。
- 新增持久化文档时，同步更新对应索引文件，例如 `BACKTEST_INDEX.md`、`STOCK_RESEARCH_INDEX.md`、`STRATEGY_RESEARCH_INDEX.md` 或 `PROMPT_INDEX.md`。
- 新增脚本时先判断用途：生产脚本放 `scripts/ops/`，研究脚本放 `scripts/research/`，补数/修复/迁移脚本放 `scripts/maintenance/`，导出脚本放 `scripts/export/`。
- 影响实盘或生产候选的策略研究必须登记未来函数控制、T+1 交易口径、成本滑点和回撤指标。
