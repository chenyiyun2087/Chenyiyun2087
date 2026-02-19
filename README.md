# Chenyiyun2087 项目总览（2026 版）

Chenyiyun2087 是一个面向 A 股策略研究与执行的多模块仓库，覆盖了**信号抓取、评分选股、盘后策略、回测验证、可视化看板、任务调度**的完整链路。

---

## 1) 项目组件全景（Review）

| 组件 | 目录 | 主要职责 | 典型入口 |
|---|---|---|---|
| 调度中枢 | `scheduler.py`、`scripts/ops/` | 串联交易日任务（Sina / Eastmoney / 夜间流程） | `python scheduler.py` |
| Sina B/S 检测 | `sina/bs_detection/` | 识别图像中的 B/S 点，输出候选信号 | `python -m sina.bs_detection.main` |
| Sina 实盘跟踪 | `sina/live_tracker/` | 管理持仓、同步价格、生成日报 | `python -m sina.live_tracker.run_live_tracker` |
| ScoreRank 评分 | `scoreRank/` | 多策略打分（M1~M8、Claude/Fama/Technical） | `python -m scoreRank.cli.run_daily` |
| Eastmoney 盘后策略 | `eastmoney/` | 盘后扫描、触发池与交易池生成 | `python eastmoney/main.py` |
| 回测引擎 | `backtest/src/backtest_engine/` | 数据源、撮合、组合、指标与报告导出 | `pytest backtest/tests -q` |
| chenyiyunSelected 策略迁移 | `chenyiyunSelected/` | 聚宽策略本地化、信号落库、本地回测对接 | `python chenyiyunSelected/strategy/run_local_backtest.py` |
| InvestingPro 数据处理 | `investingPro/` | InvestingPro 导出文件清洗、解析、入库 | 各 `InvestingPro*.py` |
| Web 看板 | `web/` | Flask 可视化、策略页、管理页、手工操作入口 | `python web/app.py` |

---

## 2) 目录建议（按职责阅读）

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

## 3) 推荐运行顺序（最小闭环）

1. **采集信号**：运行 `sina` / `eastmoney` 数据任务。  
2. **评分选股**：运行 `scoreRank` 日频打分。  
3. **盘后回放**：运行 `backtest` 或 `chenyiyunSelected` 本地回测。  
4. **可视化复盘**：在 `web` 看板中查看持仓、评分、策略结果。  
5. **自动化执行**：通过 `scheduler.py` 与 `scripts/ops/dry_run_scheduler.py` 固化节奏。

---

## 4) 本次文档更新范围

- 已统一更新以下 README：
  - 根目录 `README.md`
  - `sina/README.md`
  - `eastmoney/README.md`
  - `scoreRank/README.md`
  - `backtest/README.md`
  - `investingPro/README.md`
  - `chenyiyunSelected/README.md`
  - `chenyiyunSelected/docs/README.md`
  - `web/README.md`

---

## 5) 项目优化建议（待你确认后再实施）

> 下面先给“优化建议清单”，不直接改代码。你确认后我再按优先级实施。

### A. 架构与目录
1. **统一包命名风格**：将历史目录命名差异（如大小写、别名入口）规范为一致风格，保留兼容层。  
2. **抽离公共配置层**：集中管理 MySQL / 路径 / 交易日参数，避免每个模块单独维护。  
3. **结果产物归档规范化**：统一 `result/`、`logs/`、`live_result/` 的命名规则与保留周期。

### B. 稳定性与可维护性
4. **引入模块级健康检查命令**：例如 `python -m <module>.healthcheck`，便于调度前快速验活。  
5. **日志标准化**：统一日志格式（JSON 或固定字段），加 run_id 便于跨模块串联。  
6. **异常策略分级**：区分可重试错误（网络/抓取）与不可重试错误（配置/数据结构）。

### C. 数据质量与回测一致性
7. **数据契约（Schema Contract）**：对关键输入输出表字段建立校验器。  
8. **离线回放样本集**：为 sina/eastmoney/scheduler 增加最小可回放样本，提升回归稳定性。  
9. **策略参数版本化**：把关键参数（阈值、权重）与产出关联保存，支持结果可追溯。

### D. 研发效率
10. **统一 CLI 入口**：建议增加顶层 `python -m appctl <subcommand>`，降低使用门槛。  
11. **README 与脚本参数自动同步**：通过脚本抽取 `argparse` 生成文档片段，减少文档漂移。  
12. **CI 最小检查**：先从 `pytest` smoke + `python -m compileall` + `ruff`（可选）起步。

---

## 6) 下一步

如果你确认，我会按“**先低风险、再结构性改造**”的顺序，先实施：
1) 日志与配置统一，2) 健康检查与最小回归，3) CLI 统一入口与目录重构兼容层。
