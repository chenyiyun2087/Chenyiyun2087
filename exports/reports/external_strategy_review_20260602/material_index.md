# 外部专家评估材料索引

材料目录：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/reports/external_strategy_review_20260602`

## 本目录文件

| 文件 | 用途 |
|---|---|
| `EXPERT_REVIEW_BRIEF.md` | 主说明文档，建议先读。 |
| `strategy_risk_return_summary.csv` | 四个窗口、五个策略的收益风险汇总。 |
| `current_production_candidates.csv` | 当前生产策略 Top5 候选。 |
| `current_production_orders.csv` | 当前生产策略订单草案。 |
| `current_dynamic_weights.csv` | 当前信号日动态因子权重。 |
| `current_market_environment.csv` | 当前信号日市场环境字段。 |
| `current_production_candidates.md/json` | 当前生产候选原始报告。 |

## 回测包

| 窗口 | 路径 | 主要文件 |
|---|---|---|
| 最近一年 | `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/signal_research/20260602_023147_523603_trusted_account_backtest` | summary/nav/trades/positions/candidates/dynamic_weights/market_environment/adaptive_decisions/report |
| 最近半年 | `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/signal_research/20260602_023311_627724_trusted_account_backtest` | summary/nav/trades/positions/candidates/dynamic_weights/market_environment/adaptive_decisions/report |
| 最近三个月 | `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/signal_research/20260602_023359_774892_trusted_account_backtest` | summary/nav/trades/positions/candidates/dynamic_weights/market_environment/adaptive_decisions/report |
| 2025年初至今 | `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/signal_research/20260602_021904_135639_trusted_account_backtest` | summary/nav/trades/positions/candidates/dynamic_weights/market_environment/adaptive_decisions/report |

## 关键代码入口

| 文件 | 用途 |
|---|---|
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/research_full_pool_liquidity_strategies.py` | 策略定义、选股逻辑、候选选择。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/research_trusted_strategy_account_backtest.py` | 账户级 T+1 回测和自适应策略逻辑。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/export_trusted_strategy_candidates.py` | 生产候选、订单草案、飞书通知。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/web/app.py` | Web 调度、核心精选页面和任务验证。 |

## 项目说明文档

| 文件 | 用途 |
|---|---|
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/README.md` | 项目总览和主链路。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/AGENTS.md` | 项目管理和策略研究安全规则。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/docs/00_project_overview/RUNBOOK.md` | 运行手册和未来函数红线。 |
| `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/docs/03_backtest_reports/BACKTEST_INDEX.md` | 回测报告索引。 |

## 当前生产状态

- 当前生产策略：`tiered_liquidity_then_bs_v2`
- 当前生产候选目录：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/production_candidates/20260602_112141_tiered_liquidity_then_bs_v2`
- 核心精选页面：`http://192.168.50.88:5001/chenyiyun/selected`
- 当前订单：5 条 BUY，0 条 SELL
