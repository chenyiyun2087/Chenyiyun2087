# Chenyiyun2087 项目总览（2026）

Chenyiyun2087 是一个面向 A 股量化研究与执行的多模块仓库，覆盖：

- 数据采集（Sina / Eastmoney）
- 评分选股（ScoreRank）
- 盘后策略与回归优化（M2~M8）
- 实盘跟踪（Live Tracker）
- 本地策略与信号生成（chenyiyunSelected）
- 回测引擎（backtest）
- Web 看板与任务运维（Flask + Admin）
- 定时调度（Web 内置调度，`scheduler.py` 当前未启用）

## 1. 架构总览（按代码实现）

| 层 | 目录/文件 | 说明 | 典型入口 |
|---|---|---|---|
| 调度层 | `web/app.py`、`scripts/ops/`（`scheduler.py` 历史保留） | 交易日判定、定时触发、任务状态记录 | `python web/app.py` |
| 数据采集层 | `sina/bs_detection/`、`eastmoney/` | B/S 信号图片抓取与检测、舆情扫描与落库 | `python sina/bs_detection/main.py config_1` |
| 评分层 | `scoreRank/core/`、`scoreRank/strategies/` | 技术因子打分 + Claude 分 + 优化分（opt_score） | `python -m scoreRank.cli.run_daily` |
| 策略评估层 | `scoreRank/cli/`、`web/strategy_playbook.py` | 事件/KPI 构建、M2~M8 参数回归与评估 | `python -m scoreRank.cli.run_m8_cycle` |
| 实盘与信号层 | `sina/live_tracker/`、`chenyiyunSelected/` | 交易记录、持仓快照、本地策略调调仓信号 | `python scripts/ops/run_chenyiyun_daily.py` |
| 回测层 | `backtest/src/` | 通用回测框架 + 策略集成测试 | `pytest backtest/tests` |
| 展示层 | `web/templates/`、`web/app.py` | 监控看板、股票池管理、任务调度配置 | `http://localhost:5001/admin` |

### 1.1 策略边界说明（重要）

本项目中 **`sina` 策略** 与 **`chenyiyun` 策略** 是两套独立策略体系，不是同一策略的不同别名：

- **sina 策略体系**
  - 主要目录：`sina/`、`scoreRank/`、`web/strategy_playbook.py`
  - 核心能力：B/S 检测、M2~M8 评估、M7 调仓规则、Sina 实盘跟踪
  - 典型任务：`sina_picture`、`sina_analyse`、`sina_score`、`sina_m8`、`sina_m7_sell`、`sina_snapshot`

- **chenyiyun 策略体系**
  - 主要目录：`chenyiyunSelected/`、`scripts/ops/run_chenyiyun_*.py`
  - 核心能力：本地化选股、日/周调仓信号生成、涨停检查、仓位更新
  - 典型任务：`chenyiyun_selected`、`chenyiyun_weekly_rebalance`、`chenyiyun_limitup_check`、`chenyiyun_position_update`

- **共享但不混用的部分**
  - 共享基础设施：MySQL、交易日历、Web 管理台、部分持仓表
  - 不共享策略决策逻辑：信号生成、调仓规则、评估口径分别独立维护

## 2. 调度系统（三阶段自动化）

项目当前以 `web/app.py` 内置调度为唯一生效入口；`scheduler.py` 仅保留作历史参考，不参与生产调度。

### 2.1 三阶段日内调度流水线

| 阶段 | 时间 | 关键任务 | 脚本/命令 |
|---|---|---|---|
| **一阶段：晨间准备** | 08:00 | 交易日历同步 | `scripts/ops/sync_trade_cal.py` |
| | 09:05 | 信号强度检查 | `scripts/ops/run_chenyiyun_signal_check.py` |
| | 09:30 | 周度调仓（周一）| `scripts/ops/run_chenyiyun_weekly_rebalance.py` |
| **二阶段：盘中/收盘后** | 14:00 | 涨停状态检查 | `scripts/ops/run_chenyiyun_limitup_check.py` |
| | 15:20 | Sina 批量截图（`sina_picture`） | `sina/bs_detection/main.py --capture-only` |
| | 16:10 | Sina 买卖点分析（`sina_analyse`） | `sina/bs_detection/main.py --analyze-only` |
| | 16:30 | 舆情扫描 | `eastmoney/main.py` |
| **三阶段：夜间处理** | 21:00 | 全A股评分流水线 | `run_pipeline()` (含 Pipeline 内子任务) |
| | 21:10 | M8 回归与仓位更新 | `run_m8_cycle.py` / `run_chenyiyun_position_update.py` |
| | 21:30 | 实盘快照同步 | `run_live_tracker.py snapshot` |

### 2.2 调度器说明

- **Web 内置调度 (`web/app.py`)**: 当前生产调度入口，支持定时触发、手动重跑、任务锁与历史记录。
- **独立调度器 (`scheduler.py`)**: 当前未启用，仅保留代码与日志结构供排查/回溯。

### 2.3 交易日门禁规则（2026-02-27 更新）

- 所有定时任务在触发执行前，统一查询 `chenyiyun.dim_trade_cal`（`exchange='SSE'`）判断是否交易日。
- 若当日非交易日：任务不执行业务脚本，直接按 `Success` 记账并切日（`switched_day=True`）。
- Web 调度会写入 `app_task_history`，消息包含 `reason=NON_TRADING_DAY`，便于审计。

## 3. 核心模块详解

### 3.1 chenyiyunSelected (本地策略与信号)
- **职责**: 维护“陈依云”系列量化策略，生成每日买卖信号。
- **关键脚本**:
    - `run_chenyiyun_daily.py`: 信号生成入口，自动推断总资产并计算仓位。
    - `run_chenyiyun_signal_check.py`: 早盘信号确认。
    - `run_chenyiyun_limitup_check.py`: 盘中涨停监控。

### 3.2 ScoreRank (评分引擎)
- **评分体系**: 结合技术面因子（trend, breakout, volume等）与 AI 评分（Claude）。
- **M8 回归优化**: 对 M1 (事件) 与 KPI (收益) 进行自动化评估与回测。
    - **增量构建**: `build_b_event_kpi.py` 支持增量更新，大幅提升每日处理性能。
    - **风险量化**: 评估体系引入了 **夏普比率 (Sharpe Ratio)** 与 **最大回撤 (MDD)**，实现风险调整后的收益评估。
    - **参数搜索**: 通过 `run_m8_cycle.py` 自动执行网格搜索并持久化最优策略参数。

#### 3.2.1 M2~M8 逻辑链路（输入/处理/输出）

| 阶段 | 主要输入 | 核心处理 | 主要输出 | 代码入口 |
|---|---|---|---|---|
| M2 预设回归 | `b_event_fact + b_event_kpi`（仅 `is_eligible=1`） | 固定三套策略（Pyramid/Weighted/Quadrant）对比，计算 `avg_ret/hit/mdd/sharpe` | 预设策略效果排名 | `web.strategy_playbook.evaluate_m2_presets` |
| M3 参数优化 | 同 M2 | 对三类策略家族做网格搜索，按 `avg_ret_10 + hit_10` 选每家族冠军参数 | 各家族冠军参数与绩效 | `web.strategy_playbook.evaluate_m3_optimizer` |
| M4 组合分配 | M1 样本 + M2/M3 共识逻辑 | 融合三家族投票构建 `m4_score`，选 TopN 并分配权重 | 目标持仓与权重 | `web.strategy_playbook.evaluate_m4_allocation` |
| M5 滚动验证 | 最近 N 个 `event_date` 样本 | 按滚动窗口重复 M4，观察收益与命中稳定性 | 窗口级统计与离散度 | `web.strategy_playbook.evaluate_m5_rolling` |
| M6 净值回测 | M4 每日选股结果 | 叠加成本/滑点，计算 `gross_nav/net_nav` 与回撤 | 净值曲线、净收益、最大回撤 | `web.strategy_playbook.evaluate_m6_nav` |
| M7 调仓生成 | M4 目标仓位 + 当前实盘仓位 | 生成模拟买卖单（金额/股数/方向/命令） | 调仓指令清单 | `web.strategy_playbook.evaluate_m7_rebalance` |
| M8 周期任务 | 最近 `lookback_dates` 的 M1 样本（默认 60） | 先做上游新鲜度门禁，再执行 M2/M3 并落库 | `strategy_m8_runs` + `strategy_m8_items` | `python -m scoreRank.cli.run_m8_cycle` |

- `sina_m8` 任务的上游门禁：若 `bs_detection_results` 最新日期领先 `score_rank_daily`，会提示“上游任务未执行完成，不能执行 M8”，并以非零退出码中止，避免假成功。
- Web 页面分工：`/sina/strategy/m2`、`/sina/strategy/m3` 主要做在线展示与分析；`sina_m8` 任务负责周期落库与可审计追踪。

#### 3.2.2 M7 模拟调仓执行口径（2026-02-27）

- M7 是“**每日评估（仅交易日）**、**按条件交易**”机制，不是“每日必有交易”机制。
- 交易日内每次运行都会基于 `M4目标仓位 + 当前实盘仓位` 重新计算调仓单；若无触发条件，`orders_total=0`，当天不下单。
- 普通调仓触发条件：`|target_weight - current_weight| >= min_trade_weight`。
- 强制卖出触发条件（优先级最高）：
  - `B/S反转卖出`：`latest_sell_date >= latest_buy_date`；
  - `硬止损`：`current_price <= avg_cost * (1 - stop_loss_pct)`。
- 强制卖出会直接清仓当前持股，不受 `min_trade_weight` 限制，且不做 100 股取整。
- 非强制调仓按金额差换算股数，按 100 股取整；结果按“先卖后买”排序，优先释放资金。
- 卖出信号会同步落库到 `m7_sell_signals`（`FORCED_EXIT` / `REBALANCE`），用于审计与复盘。

### 3.3 Sina & Live Tracker (实盘监控)
- **B/S 扫描**: 全自动截图 + Tesseract OCR 识别新浪财经买卖信号。
- **Live Tracker**: 实盘流水审计、持仓估值与每日建档快照。

### 3.4 Web 控制台 (`web/`)
- **Dashboard**: 策略绩效、信号趋势、持仓分布可视化。
- **Monitor**: B/S 信号最新监控、当日汇总、技术因子热力图。
- **Admin**: 任务锁状态管理、调度配置更新、手动录入成交单。

## 4. 目录结构

```text
Chenyiyun2087/
├── scheduler.py                 # 历史保留（当前未启用）
├── web/                         # Flask Web 看板与调度管控
├── scripts/ops/                  # 业务运维脚本 (陈依云信号、日历同步等)
├── sina/                        # Sina B/S 数据流与实盘同步
├── scoreRank/                   # 评分核心、KPI构建与 M8 管道
├── chenyiyunSelected/           # 核心策略实现与信号生成器
├── eastmoney/                   # 舆情分析与盘后辅助策略
├── backtest/                    # 统一回测引擎
└── logs/                        # 各模块执行日志
```

## 5. 常用命令

```bash
# 1. 启动 Web 管理台
python web/app.py

# 2. 运行陈依云每日信号生成 (Web 友好封装)
python scripts/ops/run_chenyiyun_daily.py

# 3. 手动触发全 A 股评分
python -m scoreRank.cli.run_daily

# 4. 执行 M8 策略回归优化 (支持增量)
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60

# 5. 手动重建 B-Event KPI 基础数据 (增量模式下跳过已有数据)
python -m scoreRank.cli.build_b_event_kpi --all  # 使用 --all 强制全量重建

# 5. 实盘持仓同步
python sina/live_tracker/run_live_tracker.py sync
```

## 6. 注意事项
- 修改调度时间以 `web/app.py` 的任务字典为准（`scheduler.py` 当前未启用）。
- 实盘同步（Snapshot）前需确保行情数据已在 ODS 层落库完成。

## 7. 任务完成通知系统（新增）

### 7.1 Web Console 配置入口

- 入口页面：`/admin`（后台任务中心）
- 配置区域：`消息通知渠道`
- 支持渠道：
  - 飞书（Feishu）
  - 企业微信（Wechat）
  - 钉钉（Dingtalk）
  - 自定义 Webhook
- 生效规则：仅对“已启用 + URL 合法（http/https）”的渠道发送通知。

> 测试环境默认预填飞书 Webhook（可在后台覆盖）：
> `https://open.feishu.cn/open-apis/bot/v2/hook/a8374c19-3620-4891-8c7a-df6885229607`

### 7.2 调用链（任务完成后通知）

统一调用链如下（位于 `web/app.py`）：

1. 任务执行线程：`_execute_locked_task(...)`
2. 写入任务历史：`_insert_task_history(...)`
3. 触发通知总入口：`_send_task_completion_notification(...)`
4. 构建任务摘要：`_build_task_completion_notification(...)`
5. 多渠道分发：`_dispatch_task_notification(...)`
6. 单渠道发送：`_post_channel_webhook(...)`

### 7.3 触发条件

- 仅在任务 **执行成功**（`history_status == "Success"`）后触发通知。
- 当前仅对以下任务启用通知：
  - `sina_analyse`
  - `sina_m8`
  - `sina_snapshot`

### 7.4 业务功能（按任务）

#### A) `sina_analyse` 完成通知

- 目标：通知“分析完成”，并给出当日 B/S 统计结果。
- 摘要字段：
  - 分析日期（`batch_date`）
  - 覆盖股票数
  - 买点信号数 / 卖点信号数 / 双向信号数
  - 数据更新时间
  - 买点示例代码（最多 8 条）
  - 卖点示例代码（最多 8 条）
- 数据来源：`bs_detection_results`

#### B) `sina_m8` 完成通知

- 目标：通知“M8 已完成”，并给出“今天调仓结果”（卖出侧）。
- 摘要字段（M8 本体）：
  - `run_id`、`as_of_date`、`status`
  - `lookback_dates`、样本行数、可交易样本、搜索组合数、结果条目数
  - M3 冠军参数摘要（最多 3 条）
- 调仓结果字段（卖出侧）：
  - 今日卖出单总数
  - 强制卖出数
  - 再平衡卖出数
  - 挂起单数（`pending`）
  - 预计卖出总金额
- 数据来源：`strategy_m8_runs`、`strategy_m8_items`、`m7_sell_signals`

#### C) `sina_snapshot` 完成通知

- 目标：通知“实盘快照已完成”，并给出当日实盘总结。
- 摘要字段：
  - 快照日期
  - 总权益、现金、持仓市值
  - 当日盈亏、当日收益率、沪深300收益率、超额收益率
  - 当前持仓数量
  - 当日成交汇总（买入笔数/金额、卖出笔数/金额）
- 数据来源：`live_daily_snapshots`、`live_positions`、`live_trades`

### 7.5 消息样式（模板）

统一消息头：

```text
【任务完成】<任务显示名>
任务ID：<task_name>
触发方式：manual/schedule
开始时间：YYYY-MM-DD HH:MM:SS
完成时间：YYYY-MM-DD HH:MM:SS
```

任务摘要正文按任务类型拼接（见 7.4）。

渠道适配：

- 飞书：`msg_type=text`
- 企业微信：`msgtype=markdown`
- 钉钉：`msgtype=markdown`（含标题）
- 自定义：默认 `{text, content}` JSON
