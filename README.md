# Chenyiyun2087 项目总览（2026 版）

Chenyiyun2087 是一个面向 A 股策略研究与执行的多模块仓库，覆盖了**信号抓取、评分选股、盘后策略、回测验证、可视化看板、任务调度**的完整链路。

---

## 1) 项目组件全景（按当前代码校对）

| 组件 | 目录 | 主要职责 | 典型入口 |
|---|---|---|---|
| 调度中枢 | `scheduler.py`、`scripts/ops/` | 串联交易日任务（Sina / Eastmoney / 夜间流程） | `python scheduler.py` |
| Sina B/S 策略中心 | `sina/` | **B/S 监控**、实盘跟踪、M8评分策略、持仓管理 | `python -m sina.bs_detection.main` |
| ScoreRank 评分 | `scoreRank/` | 多策略打分（M1~M8、Technical/Claude） | `python -m scoreRank.cli.run_daily` |
| M8 回归与参数搜索 | `scoreRank/cli/run_m8_cycle.py` | M8 回归、参数搜索、结果落库（支持按股票池过滤） | `python -m scoreRank.cli.run_m8_cycle --lookback-dates 60 [--pool-id <id>]` |
| Eastmoney 盘后策略 | `eastmoney/` | 盘后扫描、触发池与交易池生成 | `python eastmoney/main.py` |
| 回测引擎 | `backtest/src/backtest_engine/` | 数据源、撮合、组合、指标与报告导出 | `pytest backtest/tests -q` |
| 陈依云精选 (Local) | `chenyiyunSelected/` | 聚宽策略本地化、信号落库、本地回测对接 | `python chenyiyunSelected/strategy/run_local_backtest.py` |
| InvestingPro 数据处理 | `investingPro/` | InvestingPro 导出文件清洗、解析、入库 | 各 `InvestingPro*.py` |
| Web 看板 | `web/` | Flask 可视化、策略页、管理页、手工操作入口 | `python web/app.py` |

---

## 2) Web 看板关键功能（已对齐当前实现）

### 2.1 左侧核心栏目（三层结构）
- **Sina B/S策略中心**：
  - B/S监控：`/sina/monitor`（B点股票[最新] / 当日汇总 / 信号统计）
  - 股票池评分：`/sina/scores`
  - M8评分策略：`/sina/strategy/*`（金字塔、加权、象限、M2~M7）
  - Sina策略实时持仓：`/positions`
- **陈依云精选**：
  - 核心精选：`/chenyiyun/selected`（每日信号与持仓）
  - 回测中心：`/backtest/results`
  - 技术评分：`/sina/tech_score`
- **系统设置**：
  - 股票池管理：`/stock_pool`
  - 管理台：`/admin`

### 2.2 新增/更新能力
- **B/S 监控增强**（`/sina/monitor`）：
  - **B点股票（最新）**：实时展示最新发出 B 点信号的股票（修正数据加载逻辑）；
  - **信号统计**：集成近 30 日买卖信号趋势图（Chart.js）。
- **技术评分**（`/sina/tech_score`）：
  - 可按“指定股票 + 指定股票池（并集）”筛选样本；
  - 抽取总分/因子分/Claude分；
  - 以策略1金字塔、策略2加权、策略3象限展示结果。
- **股票池管理**（`/stock_pool`）：
  - 支持批量录入股票代码；
  - 支持代码/名称双向反查并校验存在性；
  - 支持分页与翻页后滚动位置保持；
  - 仅只读池禁止增删改（当前默认“最近有买点股票池”为只读）。
- **M8 任务按股票池执行**（`/admin`）：
  - 管理台可为 `sina_m8` 任务选择股票池；
  - 运行时透传 `--pool-id` 到 `run_m8_cycle.py`。

---

## 3) 推荐运行顺序（最小闭环）

1. **采集信号**：运行 `sina` / `eastmoney` 数据任务。  
2. **评分选股**：运行 `scoreRank` 日频打分。  
3. **M8 优化（可选）**：运行 `scoreRank/cli/run_m8_cycle.py`（可按股票池过滤）。  
4. **盘后回放**：运行 `backtest` 或 `chenyiyunSelected` 本地回测。  
5. **可视化复盘**：在 `web` 看板查看持仓、评分、策略与监控结果。  
6. **自动化执行**：通过 `scheduler.py` 与 `scripts/ops/dry_run_scheduler.py` 固化节奏。

---

## 4) 常用命令速查

```bash
# Web 看板
python web/app.py

# 每日评分
python -m scoreRank.cli.run_daily

# M8（全量样本）
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60

# M8（指定股票池）
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60 --pool-id 1

# 调度器
python scheduler.py
```

---

## 5) 目录建议（按职责阅读）

```text
Chenyiyun2087/
├── README.md
├── scheduler.py
├── scripts/                  # 运维、数据库、调度辅助脚本
├── sina/                     # B/S 检测 + live tracker + sina backtest
├── scoreRank/                # 多模型评分与日常执行 CLI
├── eastmoney/                # 盘后扫描与策略执行
├── backtest/                 # 通用回测引擎（可复用）
├── chenyiyunSelected/        # 策略迁移与研究产物
├── investingPro/             # InvestingPro 数据管道
└── web/                      # Flask 看板
```

---

## 6) 说明

- 当前仓库同时包含“研究脚本 + 线上化入口 + 运维脚本”，建议先通过本 README 快速定位模块，再进入各子目录 README 获取参数细节。
- 本文档已按当前代码能力（技术评分、M8 按池执行、监控信号统计、股票池增强）做修订与补充。
