# Chenyiyun2087 项目总览（2026）

Chenyiyun2087 是一个面向 A 股量化研究与执行的多模块仓库，覆盖：

- 数据采集（Sina / Eastmoney）
- 评分选股（ScoreRank）
- 盘后策略与回归优化（M2~M8）
- 实盘跟踪（Live Tracker）
- 回测引擎（backtest）
- Web 看板与任务运维（Flask + Admin）
- 定时调度（独立调度器 + Web 内置调度）

## 1. 架构总览（按代码实现）

| 层 | 目录/文件 | 说明 | 典型入口 |
|---|---|---|---|
| 调度层 | `scheduler.py`、`web/app.py`、`start_scheduler.sh`、`scripts/ops/` | 交易日判定、定时触发、任务串联、任务状态记录 | `python scheduler.py` / `python web/app.py` |
| 数据采集层 | `sina/bs_detection/`、`eastmoney/main.py`、`eastmoney/data_controller.py` | B/S 信号图片抓取与检测、舆情扫描与落库 | `python sina/bs_detection/main.py config_1 20260220` |
| 评分层 | `scoreRank/core/`、`scoreRank/strategies/`、`scoreRank/cli/run_daily.py` | 技术因子打分 + Claude 分 + 优化分（opt_score） | `python -m scoreRank.cli.run_daily` |
| 策略评估层 | `scoreRank/cli/build_b_event_kpi.py`、`scoreRank/cli/run_m8_cycle.py`、`web/strategy_playbook.py` | 事件表/KPI 表构建、M2/M3 评估、M8 结果落库 | `python -m scoreRank.cli.run_m8_cycle --lookback-dates 60` |
| 实盘跟踪层 | `sina/live_tracker/` | 交易记录、持仓、净值快照、报告导出、信号联动 | `python sina/live_tracker/run_live_tracker.py sync` |
| 回测层 | `backtest/src/backtest_engine/`、`chenyiyunSelected/strategy/` | 通用回测框架 + 本地策略迁移与再平衡指令 | `pytest backtest/tests -q` |
| 展示与运维层 | `web/templates/`、`web/app.py` | 看板页面、股票池管理、任务管理、历史执行记录 | `http://localhost:5001/admin` |

## 2. 组件详细说明

### 2.1 调度系统（重点）

项目当前存在两套调度机制：

1. 独立进程调度：`scheduler.py`
2. Web 内置调度：`web/app.py` 中 `_run_scheduled_tasks_loop()`

#### A) 独立调度器 `scheduler.py`

固定任务窗口（按交易日）：

- `15:20`：`sina/bs_detection/main.py config_1 <YYYYMMDD>`
- `16:30`：`eastmoney/main.py config_1 <YYYYMMDD>`
- `21:00`：日终流水线 `run_pipeline()`，顺序执行：
1. `eastmoney/run_strategy.py --export result`
2. `scoreRank/run_daily.py`
3. `scoreRank/cli/build_b_event_kpi.py`
4. `scoreRank/cli/run_m8_cycle.py --lookback-dates 60`
5. `sina/live_tracker/run_live_tracker.py sync`

关键控制逻辑：

- 交易日检查：`is_trade_day()` 查询 `dim_trade_cal`
- 数据就绪检查：`is_data_ready()` 检查 `dwd_stock_daily_standard`
- 子任务执行：`run_script()`，每次单独日志文件写入 `logs/scheduler/`

#### B) Web 内置任务调度 `web/app.py`

管理台任务定义在 `TASKS`（含 `schedule_enabled`、`schedule_time`、`trading_day_only`）：

- `sina_bs`
- `sina_score`
- `sina_m8`
- `sina_snapshot`
- `eastmoney`
- `sync_trade_cal`

数据库状态表：

- `app_task_status`：当前状态、上次运行时间、下次运行时间
- `app_task_history`：执行历史（manual/schedule、exit_code、duration、message）

调度线程：

- 启动时由 `start_task_scheduler_loop()` 拉起后台线程
- 每 20 秒扫描一次是否命中 `schedule_time`
- 满足条件后异步线程调用 `run_script()`

### 2.2 Sina 模块（`sina/`）

- `bs_detection/`：B/S 图片抓取、检测、归档、落库
- `live_tracker/`：实盘交易流水、持仓、净值、报告、CSV 导出
- `backtest/`：Sina 侧回测与评估辅助

典型入口：

- B/S 调度入口：`python sina/bs_detection/main.py config_1 20260220`
- 实盘入口：`python sina/live_tracker/run_live_tracker.py <subcommand>`

### 2.3 ScoreRank 模块（`scoreRank/`）

职责：

- 从行情表批量取数（`core/db_io.py`）
- 构建技术特征并评分（`core/scorer.py`、`strategies/technical.py`、`strategies/claude.py`）
- 产出日频评分结果到 `score_rank_daily`
- 构建事件事实表与 KPI 表（`build_b_event_kpi.py`）
- 做 M8 参数评估并写入 `strategy_m8_runs` / `strategy_m8_items`

说明：

- `scoreRank/run_daily.py` 是 wrapper，实际执行在 `scoreRank/cli/run_daily.py`
- `run_m8_cycle.py` 支持 `--pool-id`（按股票池过滤样本）

### 2.4 Eastmoney 模块（`eastmoney/`）

- `main.py`：批量舆情扫描入口（自选或数据库股票池）
- `data_controller.py`：并发抓取 + 结果保存
- `run_strategy.py`：盘后超跌反弹策略执行
- `post_market_scanner.py`：候选筛选、技术条件验证、结果落库

### 2.5 Web 模块（`web/`）

主要页面：

- `/dashboard`
- `/sina/monitor`
- `/sina/scores`
- `/sina/strategy/*`（M2~M7 可视化）
- `/positions`
- `/chenyiyun/selected`
- `/backtest/results`
- `/stock_pool`
- `/admin`

管理台支持：

- 手工触发任务
- 配置任务调度时间
- 查看历史执行结果与错误信息

### 2.6 回测模块

- `backtest/src/backtest_engine/`：通用引擎（clock/broker/portfolio/metrics/reporting）
- `chenyiyunSelected/strategy/`：本地策略适配 + 每日调仓信号输出

## 3. 推荐运行链路（最小闭环）

1. 盘中/盘后采集：Sina、Eastmoney
2. 日终评分：`scoreRank.cli.run_daily`
3. 事件与KPI：`build_b_event_kpi.py`
4. M8 回归与优化：`run_m8_cycle.py`
5. 实盘同步：`run_live_tracker.py sync/snapshot`
6. Web 看板复盘：`/admin` + 策略页面

## 4. 常用命令

```bash
# Web 看板
python web/app.py

# 独立调度器
python scheduler.py

# 启动调度器（后台）
bash start_scheduler.sh

# 每日评分
python -m scoreRank.cli.run_daily

# B事件表 + KPI表
python -m scoreRank.cli.build_b_event_kpi

# M8 回归（全量）
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60

# M8 回归（按股票池）
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60 --pool-id 1

# 实盘同步
python sina/live_tracker/run_live_tracker.py sync
```

## 5. 目录导航（建议阅读顺序）

```text
Chenyiyun2087/
├── scheduler.py
├── start_scheduler.sh
├── scripts/ops/                   # 调度辅助、数据同步、维护脚本
├── sina/                          # B/S 检测 + 实盘跟踪
├── scoreRank/                     # 评分引擎 + M8 管道
├── eastmoney/                     # 舆情扫描 + 盘后策略
├── web/                           # Flask 看板 + 任务管理
├── backtest/                      # 通用回测引擎
├── chenyiyunSelected/             # 本地化策略与每日信号
└── investingPro/                  # 外部数据处理脚本
```

## 6. 说明

- 本仓库兼具“研究代码 + 线上调度 + 运维脚本”，优先从入口文件理解调用链。
- 调度相关变更建议同时更新：`scheduler.py`、`web/app.py`、`start_scheduler.sh` 与本 README。

## 7. Web Console 五个调度任务执行详解

本节只覆盖 Web 控制台当前配置的 5 个任务：

- `sina_bs`
- `sina_score`
- `sina_m8`
- `sina_snapshot`
- `sync_trade_cal`

### 7.1 统一调度调用链（所有任务共用）

#### A) 触发路径

1. 手动触发：`POST /admin/run_task/<task_name>`
2. 定时触发：`web/app.py::_run_scheduled_tasks_loop()` 每 20 秒扫描一次 `schedule_time`

#### B) 执行链路

`run_task/_run_scheduled_tasks_loop`  
→ `_trigger_task_execution(task_name, trigger_type, run_options)`  
→ `_try_acquire_task_lock(task_name)`（数据库行锁，`SELECT ... FOR UPDATE`）  
→ 成功后启动后台线程 `_execute_locked_task(...)`  
→ `_build_task_script_parts(...)` 组装脚本与参数  
→ `subprocess.Popen([python, script, args...])` 真正执行任务脚本  
→ 运行中每 20 秒写心跳 `_touch_task_lock_heartbeat()`  
→ 结束后写入 `_insert_task_history()` + `_mark_task_lock_finished()`

#### C) 状态表与状态机

1. `app_task_lock`（同任务单实例锁）
- `IDLE` → `RUNNING` → `COMPLETE | FAILED | ERROR`
- 若 `RUNNING` 且心跳超时（`TASK_STALE_TIMEOUT_SECONDS`），会被重置为 `FAILED` 后允许重新触发。

2. `app_task_status`（控制台展示状态）
- `Idle` → `Running...` → `Success | Failed (Code N) | Error: ...`
- `/admin` 打开时会调用 `_reconcile_stale_task_states()`，把“锁非 RUNNING 但状态仍 Running...”的脏状态自动修正。

3. `app_task_history`（审计历史）
- 每次执行最终落一条记录：`task_name/trigger_type/started_at/finished_at/status/exit_code/duration/message`。

4. `app_task_queue`（历史遗留）
- 当前执行链路已不依赖队列，但 stale 修复逻辑会自动关闭卡住的 `RUNNING` 记录。

### 7.2 Web 控制台任务配置（当前代码）

| 任务ID | 默认时间 | 交易日限制 | Web 调起命令（脚本参数） |
|---|---:|---:|---|
| `sina_bs` | 15:20 | 是 | `python sina/bs_detection/main.py config_1 <datestr>` |
| `sina_score` | 21:00 | 是 | `python scoreRank/run_daily.py [--date <datestr> --force]` |
| `sina_m8` | 21:10 | 是 | `python scoreRank/cli/run_m8_cycle.py --lookback-dates 60` |
| `sina_snapshot` | 21:30 | 是 | `python sina/live_tracker/live_tracker.py` |
| `sync_trade_cal` | 08:00 | 否 | `python scripts/ops/sync_trade_cal.py` |

说明：`datestr` 仅对 `sina_bs`、`sina_score` 在 Web 入口里有显式参数透传。

### 7.3 任务逐项说明

#### 7.3.1 `sina_bs`（Sina B/S 扫描）

调用链：

`web/app.py::_execute_locked_task`  
→ `sina/bs_detection/main.py::run_pipeline`  
→ `BSpointChecker.main`（截图阶段）  
→ `SinaBSDetector.batch_process_images`（OCR/点位检测阶段）  
→ `SinaBSDetector.save_results_to_mysql`（落库）  
→ `archive_old_folders`（归档清理）

数据流（输入 → 中间 → 最终）：

| 阶段 | 数据 | 位置/表 |
|---|---|---|
| 输入 | 任务参数：`config_1` + `YYYYMMDD` | Web 触发参数 |
| 输入 | 股票池 Excel（`stock_code` 列） | `sina/config/*.json` 指向的 `excel_file` |
| 输入 | 新浪行情页面（B/S点页） | `finance.sina.com.cn` |
| 中间 | 截图 PNG（每股票一张） | `sina/bs_detection/SinaAppBS/<config>/<date>/` |
| 中间 | OCR 检测结果（内存列表） | `DETECTION_RESULTS` |
| 中间 | 汇总 Excel | `sina/bs_detection/SinaAppBS/result/<config>_<date>.xlsx` |
| 最终 | B/S 检测结果（幂等 upsert） | `bs_detection_results` |
| 最终 | 历史目录归档 zip | `sina/bs_detection/SinaAppBS/archive/...` |

上游依赖：

- Chrome/ChromeDriver（`selenium` + `webdriver_manager`）
- Tesseract OCR（`pytesseract`）
- 新浪网页可访问性

下游依赖：

- `sina_score` 从 `bs_detection_results` 读取候选股票与目标日期
- Web 页面 `/sina/monitor`、`/sina/strategy/*` 查询该表

状态变化重点：

- 运行期间常见耗时长（约 1 小时量级），依赖心跳维持 `RUNNING`。
- 退出码 `0` → `Success`；非 0 → `Failed (Code N)`。

#### 7.3.2 `sina_score`（全 A 股评分）

调用链：

`web/app.py::_execute_locked_task`  
→ `scoreRank/run_daily.py`（wrapper）  
→ `scoreRank/cli/run_daily.py::main`  
→ `TechnicalScorer/ClaudeScorer` 打分  
→ `enrich_scored_with_market_metrics`  
→ `save_scores_to_db` 写入 `score_rank_daily`

数据流（输入 → 中间 → 最终）：

| 阶段 | 数据 | 位置/表 |
|---|---|---|
| 输入 | `--date <YYYYMMDD>`（可选） | Web 透传（手动触发时） |
| 输入 | B/S 候选集合 | `bs_detection_results` |
| 输入 | 自选股与全市场股票清单 | `a_share_stock_list` + `sina/stock_codes.xlsx` |
| 输入 | 行情/复权价格/成交量 | `tushare_stock.dwd_stock_daily_standard` |
| 输入 | 股票名称 | `tushare_stock.dim_stock` |
| 中间 | 多个 DataFrame：`df_bs/df_ss/df_all/scored/features` | 进程内 |
| 中间 | 可选因子优化分 | `score.factor_optimizer.*`（若可用） |
| 最终 | 日频评分结果（先删后插） | `score_rank_daily` |

上游依赖：

- `sina_bs` 的 `bs_detection_results`（无此表时会无候选）
- Tushare 行情库的最新交易日数据是否可用

下游依赖：

- Web 评分页：`/sina/scores`、`/sina/scores/all`
- `build_b_event_kpi.py` 用 `score_rank_daily(is_bs_candidate=1)` 生成 M1 事件/KPI
- `sina_m8` 依赖 M1 结果（见下一节）

状态变化重点：

- 若未指定 `--date` 且非 `--force`，16:30 前会直接退出（时间闸门）。
- Web 在传入 `datestr` 时会自动补 `--force`。

#### 7.3.3 `sina_m8`（M8 回归落库）

调用链：

`web/app.py::_execute_locked_task`  
→ `scoreRank/cli/run_m8_cycle.py --lookback-dates 60`  
→ `fetch_recent_m1_rows`（读取 M1 事件+KPI）  
→ `evaluate_m2_presets` + `evaluate_m3_optimizer`（`web/strategy_playbook.py`）  
→ `persist_results` 写入 `strategy_m8_runs/strategy_m8_items`

数据流（输入 → 中间 → 最终）：

| 阶段 | 数据 | 位置/表 |
|---|---|---|
| 输入 | 近 `lookback_dates` 个事件日样本 | `b_event_fact` + `b_event_kpi` |
| 输入（可选） | 股票池过滤 | `stock_pool_items` |
| 中间 | M2 预设评估结果（收益/命中率） | 内存字典 |
| 中间 | M3 参数网格搜索 winner | 内存字典 |
| 最终 | 本次运行摘要 | `strategy_m8_runs` |
| 最终 | 明细条目（M2/M3） | `strategy_m8_items` |

上游依赖：

- 强依赖 `b_event_fact`、`b_event_kpi`（通常由 `scoreRank/cli/build_b_event_kpi.py` 先构建）
- 间接依赖 `sina_score`（因为 M1 来源是 `score_rank_daily`）

下游依赖：

- Web 策略评估页面（M2/M3/M4/M5/M6/M7）读取最近评估结果与候选池

状态变化重点：

- 若 `b_event_fact/b_event_kpi` 无数据，脚本会打印 skip，任务可能快速结束。

#### 7.3.4 `sina_snapshot`（实盘快照）

当前 Web 配置调用链（现状）：

`web/app.py::_execute_locked_task`  
→ `python sina/live_tracker/live_tracker.py`

关键说明（当前实现差异）：

- `sina/live_tracker/live_tracker.py` 是类定义模块，不是 CLI 入口。
- 直接执行该文件不会触发 `sync/snapshot` 子命令流程；并且其 `live_tracker_db.py` 仍依赖 `sqlalchemy` 包。
- 因此，Web 这个任务的“预期语义（生成快照）”与“当前实际入口”并不完全一致。

预期快照流程（真实业务逻辑）应为：

`python sina/live_tracker/run_live_tracker.py snapshot [--date YYYY-MM-DD]`  
→ `LiveTracker.sync_prices()`（按持仓同步最新价）  
→ `LiveTracker.calculate_daily_pnl()`  
→ `db.upsert_daily_snapshot(...)` 写入 `live_daily_snapshots`

预期数据流（输入 → 中间 → 最终）：

| 阶段 | 数据 | 位置/表 |
|---|---|---|
| 输入 | 当前持仓 | `live_positions` |
| 输入 | 最新行情价格 | `tushare_stock.dwd_stock_daily_standard` + `tushare_stock.dim_stock` |
| 中间 | 更新后的持仓现价 | `live_positions.current_price` |
| 最终 | 每日权益快照 | `live_daily_snapshots` |

上游依赖：

- 已存在的持仓与交易流水（`live_positions`、`live_trades`）
- 行情表当日/最近交易日数据

下游依赖：

- Web 持仓页 `/sina/positions`、账户曲线展示
- 报表导出与回顾分析

#### 7.3.5 `sync_trade_cal`（交易日历同步）

调用链：

`web/app.py::_execute_locked_task`  
→ `scripts/ops/sync_trade_cal.py::sync_trade_cal`

数据流（输入 → 中间 → 最终）：

| 阶段 | 数据 | 位置/表 |
|---|---|---|
| 输入 | 源交易日历 | `tushare_stock.dim_trade_cal` |
| 中间 | 批量拉取结果（每批 1000） | 脚本内存 |
| 最终 | 本地交易日历 upsert | `chenyiyun.dim_trade_cal` |

上游依赖：

- `tushare_stock` 数据库连接可用

下游依赖：

- `web/app.py::_is_trading_day()` 先查 `chenyiyun.dim_trade_cal`
- 所有 `trading_day_only=True` 任务（`sina_bs/sina_score/sina_m8/sina_snapshot`）都受其影响

### 7.4 五个任务之间的上下游关系（业务依赖图）

1. `sync_trade_cal`  
作用：提供交易日判定基础数据。  
影响：决定交易日任务是否允许触发。

2. `sina_bs`  
产出：`bs_detection_results`。  
被 `sina_score` 消费。

3. `sina_score`  
产出：`score_rank_daily`。  
被 M1 构建脚本 `build_b_event_kpi.py` 消费（非本 5 任务之一，但是 `sina_m8` 的直接上游）。

4. `sina_m8`  
直接上游不是 `sina_score` 表本身，而是 `b_event_fact/b_event_kpi`。  
因此链路是：`sina_score` → `build_b_event_kpi` → `sina_m8`。

5. `sina_snapshot`  
与评分链相对独立，主要维护实盘账户状态与净值快照，供持仓和绩效页面展示。

### 7.5 典型日内执行顺序（按默认时间）

1. 08:00 `sync_trade_cal`
2. 15:20 `sina_bs`
3. 21:00 `sina_score`
4. 21:10 `sina_m8`
5. 21:30 `sina_snapshot`

建议：

- 若要保证 `sina_m8` 数据有效，需在 21:00 与 21:10 之间确保 `build_b_event_kpi.py` 已执行并成功。
- 若要让 `sina_snapshot` 真实生成快照，建议将 Web 任务入口改为 `sina/live_tracker/run_live_tracker.py snapshot`（而不是直接执行 `live_tracker.py` 模块文件）。
