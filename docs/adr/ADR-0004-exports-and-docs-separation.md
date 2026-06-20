# ADR-0004: 自动化输出与人工文档分离

## 状态
已接受（Accepted）

## 日期
2026-06-21

## 背景
项目产出包含两类内容：
- **自动化输出**：候选导出 CSV/JSON/Markdown、回测报告、绩效复盘、影子成交记录。
- **人工文档**：运行手册、项目结构、策略索引、回测索引、发布流程。

如果混放在同一目录下，会导致文档搜索困难、自动化清理误删文档、Git 提交噪音大。

## 决定
1. **自动化输出**统一落在 `exports/`：
   - `exports/production_candidates/`：候选导出
   - `exports/production_strategy_reviews/`：绩效复盘
   - `exports/signal_research/`：回测输出
   - `exports/rolling_oos_matrix/`：滚动 OOS 结果
   - `exports/bs_signal_cycles/`：B 信号模型数据

2. **人工文档**统一落在 `docs/`：
   - `docs/00_project_overview/`：项目结构、运行拓扑、运行手册
   - `docs/01_strategy_research/`：策略研究笔记
   - `docs/03_backtest_reports/`：回测索引与结论
   - `docs/04_live_trading/`：实盘运行记录
   - `docs/adr/`：架构决策记录
   - `docs/tasks/`：开发任务

3. **禁止**将人工撰写的文档放入 `exports/`。
4. **禁止**将自动化输出放入 `docs/`（除非作为索引链接）。

## 后果
- **正向**：自动化清理不影响人工文档；文档检索路径固定。
- **负向**：需要持续自律维护分类。
- **合规**：新产出必须在正确的目录下。
