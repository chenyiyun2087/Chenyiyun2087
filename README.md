# Chenyiyun2087 项目总览

本仓库是一个围绕 **A 股日内/日频策略执行** 的多模块系统，整合了：

- **Sina B/S 图像信号检测**（抓图 + 买卖点识别）
- **ScoreRank 多因子评分**（结合 B/S 信号与行情数据）
- **Eastmoney 超跌反弹扫描**（情绪/技术/筹码）
- **Live Tracker & Web 看板**（持仓、信号、任务状态可视化）
- **Scheduler 自动调度**（按交易日时点串联任务）

---

## 1. 项目功能与模块职责

| 模块 | 入口文件 | 核心职责 | 主要产出 |
|---|---|---|---|
| 调度层 | `scheduler.py` | 按时间触发 Sina/Eastmoney/夜间流水线；检查交易日与数据就绪状态 | 调度日志、各任务执行日志 |
| Sina B/S 检测 | `Sina/bs_detection/main.py` | 抓取 K 线截图并识别 B/S 点，结果入库 | `bs_detection_results` |
| ScoreRank 评分 | `ScoreRank/run_daily.py` | 读取 B/S 候选 + 自选股 + 日线数据，进行多因子评分并分池 | `score_rank_daily`、CSV（按脚本配置） |
| Eastmoney 策略 | `Eastmoney/main.py` + `Eastmoney/run_strategy.py` | 抓取多空情绪数据并在盘后执行超跌反弹筛选 | `em_strategy_results`、Excel 报告 |
| 实盘跟踪 | `Sina/live_tracker/run_live_tracker.py` | 生成交易信号、同步价格、维护持仓、产出日报 | `live_positions`、`live_daily_snapshots`、HTML 报告 |
| Web 看板 | `Web/app.py` | 展示持仓、Eastmoney 结果、Sina 评分；提供任务触发入口 | Web 页面（Flask） |

---

## 2. 项目目录结构（重点）

```text
Chenyiyun2087/
├── scheduler.py                  # 全局定时调度入口
├── start_scheduler.sh            # 后台启动脚本（本地路径示例）
├── web_schema.sql                # Web 相关 MySQL 表结构
├── scripts/
│   ├── db/                       # 数据库运维脚本
│   │   ├── init_db.py
│   │   └── debug_bs_table.py
│   ├── ops/                      # 任务与账户运维脚本
│   │   ├── dry_run_scheduler.py
│   │   └── reconcile_account.py
│   └── *.py                      # 向后兼容入口脚本
├── Sina/
│   ├── bs_detection/             # B/S 点检测（抓图+识别）
│   ├── live_tracker/             # 实盘持仓与信号管理
│   ├── backtest/                 # 回测模块
│   ├── config/                   # 检测配置与股票池
│   └── schemas/
├── ScoreRank/                    # 多因子评分模块
├── Eastmoney/                    # 超跌反弹策略与数据采集
├── Web/                          # Flask 看板
├── logs/scheduler/               # 调度日志目录
└── result/                       # Eastmoney 导出结果目录（默认之一）
```

---

## 3. 端到端数据流向（核心）

> 下图为系统主链路（按调度设计）：

```text
交易日 15:20
  └─ scheduler -> Sina/bs_detection/main.py
      └─ 生成并写入 bs_detection_results（买卖点）

交易日 16:30
  └─ scheduler -> Eastmoney/main.py
      └─ 抓取并入库多空情绪/相关指标

交易日 21:00（pipeline）
  ├─ 等待 tushare_stock.dwd_stock_daily_standard 当日数据就绪
  ├─ Eastmoney/run_strategy.py --export result
  │   └─ 读取行情+情绪，写 em_strategy_results，导出 Excel
  ├─ ScoreRank/run_daily.py
  │   ├─ 读取 bs_detection_results 中“最新买入且未被卖出覆盖”的股票
  │   ├─ 合并自选股池（DB + Sina/stock_codes.xlsx）
  │   ├─ 拉取 dwd_stock_daily_standard 日线（qfq/raw）
  │   ├─ 计算因子分 + 风险扣分 + 可选 opt_score
  │   └─ 写入 score_rank_daily（TRADE/WATCH/自选标记）
  └─ Sina/live_tracker/run_live_tracker.py sync
      └─ 同步实盘跟踪价格与状态

次日交易
  └─ Live Tracker signals / Web 看板读取 score_rank_daily + 持仓表做执行支持
```

---

## 4. 数据表与“生产者-消费者”关系

| 数据表 | 生产模块 | 消费模块 |
|---|---|---|
| `bs_detection_results` | `Sina/bs_detection/main.py` | `ScoreRank/run_daily.py`、Sina Live Tracker |
| `tushare_stock.dwd_stock_daily_standard` | 上游行情 ETL（外部） | `scheduler.py`（就绪检查）、`ScoreRank/run_daily.py` |
| `em_strategy_results` | `Eastmoney/run_strategy.py` | `Web/app.py`（/eastmoney 页面） |
| `score_rank_daily` | `ScoreRank/run_daily.py` | `Web/app.py`（/sina/scores、/sina/self_selected） |
| `live_positions` / `live_daily_snapshots` | Sina Live Tracker | `Web/app.py`（/sina/positions） |
| `app_task_status` | Web 后台任务状态更新逻辑 | `Web/app.py` 初始化时加载 |

---

## 5. 快速开始

### 5.1 环境准备

1. Python 3.10+（建议虚拟环境）
2. MySQL（并创建 `chenyiyun` 库，且可访问 `tushare_stock`）
3. 安装依赖（按子模块分别安装）

```bash
pip install -r Web/requirements.txt
pip install -r Eastmoney/requirements.txt
```

> `Sina`、`ScoreRank` 还依赖 `pandas/sqlalchemy/pymysql/openpyxl` 等，请根据实际执行脚本补齐。

### 5.2 初始化 Web 侧数据库表

```bash
python scripts/init_db.py
```

### 5.3 手动运行核心任务（推荐先逐个验证）

```bash
# 1) Sina B/S 检测
python Sina/bs_detection/main.py config_1 20260210

# 2) Eastmoney 数据抓取
python Eastmoney/main.py config_1 20260210

# 3) Eastmoney 盘后策略扫描
python Eastmoney/run_strategy.py --date 2026-02-10 --threshold 70 --export result

# 4) ScoreRank 每日评分
python ScoreRank/run_daily.py --force

# 5) Live Tracker 同步
python Sina/live_tracker/run_live_tracker.py sync

# 6) 启动看板
python Web/app.py
```

---

## 6. 调度运行说明

### 6.1 调度时间点

- `15:20`：Sina B/S 检测
- `16:30`：Eastmoney 数据抓取
- `21:00`：夜间流水线（等待日线数据 -> Eastmoney 策略 -> ScoreRank -> Live Sync）

### 6.2 启动方式

```bash
python scheduler.py
```

> `start_scheduler.sh` 中包含本机绝对路径示例（`/Users/chenyiyun/...`），迁移环境时请先修改。

---

## 7. 常见问题（排查顺序）

1. **调度未触发**：先看 `logs/scheduler/scheduler.log`。
2. **21:00 后无评分结果**：检查 `dwd_stock_daily_standard` 当日记录数是否达到阈值（`scheduler.py` 默认 >1000 才视为就绪）。
3. **Web 页面无数据**：分别检查 `score_rank_daily`、`em_strategy_results`、`live_positions` 最新日期。
4. **脚本路径错误**：优先使用项目根目录执行，避免相对路径错位。

---

## 8. 建议的日常运行节奏

- **盘后**：先确认 B/S 检测与 Eastmoney 数据采集完成。
- **晚间**：确认 `ScoreRank` 成功写入当日评分、`Live Tracker` 完成同步。
- **次日盘前**：通过 Web 看板查看 `TRADE/WATCH`、持仓盈亏与策略结果，再做交易决策。

