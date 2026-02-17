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

### 核心功能操作指南 (Operation Guide)

本系统支持两种主要的交易模式：**自选辅助**与**量化组合**。

#### 模式一：自选交易 (Self-selected Trading)
> 适用：主观交易者，希望利用系统算分辅助决策。

1.  **浏览评分**：访问 Web 端 **"Sina B点股票评分"** (`/sina/scores`)。
    *   查看当日触发 **B 点信号** 的股票。
    *   参考综合评分 (`Score`) 和 LLM 评分 (`Claude`)。
2.  **辅助研判**：
    *   勾选感兴趣的股票，点击 **"生成分析 Prompt"** 按钮。
    *   复制生成的 Prompt 发送给 LLM (如 Claude/GPT)，获取深度基本面与技术面分析。
3.  **手动下单**：根据分析结果，自主决定买卖。

#### 模式二：组合交易 (Portfolio Trading)
> 适用：量化交易者，追求系统化的稳健收益，依赖策略演进体系 (M2-M7)。

本模式是一个闭环流程，建议每日盘后或次日盘前按顺序检视：

1.  **M2 回归 (验证)** (`/sina/strategy/m2`)
    *   查看预设策略（金字塔、加权、四象限）在最近 20 日的基准表现，确认市场环境是否适合既定策略。
2.  **M3 寻优 (定参)** (`/sina/strategy/m3`)
    *   系统会自动搜索过去 60 日的最佳参数组合（如 Claude 分数阈值、买入分门槛）。
    *   关注 **"冠军方案"** 的胜率与盈亏比，确认当前最有效的参数配置。
3.  **M4 落地 (选股)** (`/sina/strategy/m4`)
    *   系统使用 M3 的冠军参数，对当日所有候选股进行投票与打分。
    *   直接查看 **"建议持仓列表"**，包含选入的股票与**建议仓位 (Weight %)**。
    *   *注：这是系统计算出的最优持仓组合。*
4.  **M7 执行 (下单)** (`/sina/strategy/m7`)
    *   输入账户当前的 **"总资产 (Total Equity)"**。
    *   系统自动对比 **当前实盘持仓** 与 **M4 目标持仓** 的差异。
    *   生成 **"交易指令 (Orders)"**：
        *   **卖出**：不在 M4 目标中或持仓过重的股票。
        *   **买入**：新选入 M4 目标的股票，按建议金额买入。
    *   照此指令完成交易即可。

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
pip install -r web/requirements.txt
pip install -r eastmoney/requirements.txt
```

> `Sina`、`ScoreRank` 还依赖 `pandas/sqlalchemy/pymysql/openpyxl` 等，请根据实际执行脚本补齐。

### 5.2 初始化 Web 侧数据库表

```bash
python scripts/init_db.py
```

### 5.3 手动运行核心任务（推荐先逐个验证）

```bash
# 1) sina B/S 检测
python sina/bs_detection/main.py config_1 20260210

# 2) eastmoney 数据抓取
python eastmoney/main.py config_1 20260210

# 3) eastmoney 盘后策略扫描
python eastmoney/run_strategy.py --date 2026-02-10 --threshold 70 --export result

# 4) scoreRank 每日评分
python scoreRank/run_daily.py --force

# 5) Live Tracker 同步
python sina/live_tracker/run_live_tracker.py sync

# 6) 启动看板
python web/app.py
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


---

## 9. 最新阶段开发补充（M1 → M4）

> 本节用于补充最新开发内容，重点说明新增组件功能、业务逻辑与验证方式。

### 9.1 M1：事件事实表与绩效表（B 事件复盘）

**新增组件**
- `scoreRank/cli/build_b_event_kpi.py`
- `web_schema.sql` 中新增：`b_event_fact`、`b_event_kpi`

**业务逻辑**
1. 从 `score_rank_daily` 提取 `is_bs_candidate=1` 的事件样本（`event_date + symbol`）。
2. 从 `tushare_stock.dwd_stock_daily_standard` 读取复权收盘价/成交量，按事件日对齐。
3. 计算 3/5/10 日：
   - 收益率 `ret_3/ret_5/ret_10`
   - 命中率标签 `hit_*_10pct`（收益率>=10%）
   - 最大回撤 `mdd_3/mdd_5/mdd_10`
4. 计算风控标签：`is_st`、事件日停牌、10 日窗口停牌、`is_high_risk`、`is_eligible`。
5. 写入 `b_event_fact`（事件事实）与 `b_event_kpi`（事件绩效）。

---

### 9.2 M2：预设策略回归页（策略对比）

**新增组件**
- `web/strategy_playbook.py`：`evaluate_m2_presets`
- `web/app.py`：`/sina/strategy/m2`
- `web/templates/sina_strategy_m2.html`

**业务逻辑**
- 使用 M1 可交易样本（`is_eligible=1`）对三类预设方案做横向比较：
  - 金字塔默认参数
  - 加权均衡参数（取前 33%）
  - 四象限明星股
- 输出样本数、3/5/10 日平均收益与命中率，并按 10 日收益排序。
- 作为“阶段回归页面”，用于比较策略家族在统一样本集下的效果差异。

---

### 9.3 M3：参数优化页（家族冠军方案）

**新增组件**
- `web/strategy_playbook.py`：`evaluate_m3_optimizer`
- `web/app.py`：`/sina/strategy/m3`
- `web/templates/sina_strategy_m3.html`

**业务逻辑**
- 对三大策略家族分别做小规模网格搜索：
  - Pyramid：`min_score/top_pct/min_claude`
  - Weighted：多组 `A/B/C` 权重
  - Quadrant：`min_score/opt_cut/claude_cut`
- 每个家族取冠军参数（优先 10 日平均收益，再看 10 日命中率）。
- 页面展示：可交易样本数、搜索组合总数、各家族冠军参数及绩效。

---

### 9.4 M4：组合落地页（持仓建议）

**新增组件**
- `web/strategy_playbook.py`：`evaluate_m4_allocation`
- `web/app.py`：`/sina/strategy/m4`
- `web/templates/sina_strategy_m4.html`

**业务逻辑**
1. 对每个可交易标的计算三家族投票：
   - `vote_pyramid`
   - `vote_weighted`
   - `vote_quadrant`
2. 融合形成 `m4_score`（含共识加分）。
3. 按 `consensus + m4_score` 排序，选出前 `max_positions`。
4. 使用线性递减并归一化到 100% 的方式给出建议权重 `weight_pct`。
5. 页面可通过 `max_positions` 参数调节建议持仓数量。

---

### 9.5 M5：滚动窗口稳定性验证

**新增组件**
- `web/strategy_playbook.py`：`evaluate_m5_rolling`
- `web/app.py`：`/sina/strategy/m5`
- `web/templates/sina_strategy_m5.html`

**业务逻辑**
1. 按最近 N 个事件日拉取 M1 样本，按窗口大小滚动切片。
2. 每个窗口内复用 M4 组合建议逻辑，得到窗口内候选组合。
3. 统计窗口级 5/10 日均收益与命中率，形成窗口序列。
4. 汇总序列的均值/标准差/极值，用于判断稳定性而非单点收益。

---


### 9.6 M6：净值回测（成本/滑点）

**新增组件**
- `web/strategy_playbook.py`：`evaluate_m6_nav`
- `web/app.py`：`/sina/strategy/m6`
- `web/templates/sina_strategy_m6.html`

**业务逻辑**
1. 按事件日进行组合收益串联，生成毛净值序列（Gross NAV）。
2. 按参数注入交易成本与滑点（bps，按买卖往返近似），得到净收益与净净值（Net NAV）。
3. 输出毛收益、净收益、最大回撤、逐事件日净值路径，评估策略可交易性。
4. 支持参数化：`lookback_dates`、`max_positions`、`cost_bps`、`slippage_bps`。

---

### 9.7 Web 策略分栏演进

当前策略分栏（`/sina/strategy/*`）包含：
- `pyramid`：策略一金字塔
- `weighted`：策略二加权
- `quadrant`：策略三四象限
- `m2`：预设策略回归
- `m3`：参数优化冠军
- `m4`：组合落地建议
- `m5`：滚动窗口验证
- `m6`：净值回测（成本/滑点）

> 各页面均支持在 DB 不可用时降级展示（返回空数据但页面可访问），便于本地开发与联调。

---

## 10. 无外部 DB 自动化测试（新增）

新增测试位于 `test/ScoreRank/`：
- `test_m1_regression_no_db.py`
- `test_m1_functional_no_db.py`
- `test_m2_functional_no_db.py`
- `test_m3_functional_no_db.py`
- `test_m4_functional_no_db.py`
- `test_m5_functional_no_db.py`
- `test_m6_functional_no_db.py`
- `test_m7_functional_no_db.py`
- `test_m8_functional_no_db.py`

推荐一次性运行：

```bash
python -m unittest \
  test.ScoreRank.test_m1_regression_no_db \
  test.ScoreRank.test_m1_functional_no_db \
  test.ScoreRank.test_m2_functional_no_db \
  test.ScoreRank.test_m3_functional_no_db \
  test.ScoreRank.test_m4_functional_no_db \
  test.ScoreRank.test_m5_functional_no_db \
  test.ScoreRank.test_m6_functional_no_db \
  test.ScoreRank.test_m7_functional_no_db \
  test.ScoreRank.test_m8_functional_no_db -v
```

关键验证点：
- 策略逻辑纯函数行为（无 DB）
- M2/M3/M4/M5/M6 页面在无 DB 条件下可访问（HTTP 200）
- M1 关键 DDL/路由接线存在性回归

---

## 11. 里程碑建议（后续）

- **M5**：引入滚动窗口验证（避免仅看单日事件）；
- **M6**：加入交易成本/滑点后的净值回测；
- **M7**：把 M4 建议仓位打通到实盘跟踪（模拟下单流水）；
- **M8（已实施）**：将参数搜索（M3）与回归评估（M2）纳入调度器定时执行并落库（`strategy_m8_runs` / `strategy_m8_items`）。


---

## 12. M8 调度落库（新增）

- 新增 CLI：`scoreRank/cli/run_m8_cycle.py`
  - 从 `b_event_fact` + `b_event_kpi` 读取最近 N 个事件日样本
  - 运行 `evaluate_m2_presets`（回归）与 `evaluate_m3_optimizer`（参数搜索）
  - 结果写入 `strategy_m8_runs`（run 级）和 `strategy_m8_items`（条目级）
- 调度器 `scheduler.py` 的 `daily_pipeline` 新增：
  1) `scoreRank/cli/build_b_event_kpi.py`
  2) `scoreRank/cli/run_m8_cycle.py --lookback-dates 60`
- Web 管理台任务新增 `sina_m8`，可手动触发 M8 落库任务。

---

## 13. 组件说明与使用说明（按当前代码校对）

> 本节基于当前仓库入口脚本与路由实现整理，便于按组件独立调试。

### 13.1 Scheduler（任务编排层）

**组件职责**
- 统一调度交易日任务（15:20 / 16:30 / 21:00）。
- 在 21:00 pipeline 中串联：`eastmoney/run_strategy.py` → `scoreRank/run_daily.py` → `scoreRank/cli/build_b_event_kpi.py` → `scoreRank/cli/run_m8_cycle.py` → `sina/live_tracker/run_live_tracker.py sync`。

**使用说明**
```bash
# 前台运行（便于观察日志）
python scheduler.py

# 日志位置
# logs/scheduler/scheduler.log
```

---

### 13.2 Sina B/S 检测组件（`sina/bs_detection`）

**组件职责**
- 识别 B/S 图形信号并产出候选事件，供 ScoreRank 评分与后续策略复盘使用。

**使用说明**
```bash
# 指定配置+交易日执行
python sina/bs_detection/main.py config_1 20260210
```

---

### 13.3 ScoreRank 每日评分组件（`scoreRank/run_daily.py`）

**组件职责**
- 对候选股票执行多因子评分并写入 `score_rank_daily`。
- 输出交易池/观察池分层评分结果，供 Web 看板与策略页读取。

**使用说明**
```bash
# 强制执行当日评分
python scoreRank/run_daily.py --force
```

---

### 13.4 M1 事件复盘构建组件（`scoreRank/cli/build_b_event_kpi.py`）

**组件职责**
- 基于 `score_rank_daily` 构建 `b_event_fact`（事件事实）与 `b_event_kpi`（绩效标签）。
- 提供 M2～M8 的统一历史样本底座。

**使用说明**
```bash
python scoreRank/cli/build_b_event_kpi.py
```

---

### 13.5 M8 参数搜索+回归落库组件（`scoreRank/cli/run_m8_cycle.py`）

**组件职责**
- 读取最近 N 个事件日样本（M1）。
- 运行：
  - `evaluate_m2_presets`（预设策略回归）
  - `evaluate_m3_optimizer`（参数网格搜索）
- 落库：
  - `strategy_m8_runs`（run 维度）
  - `strategy_m8_items`（明细维度）

**使用说明**
```bash
# 默认 lookback=60
python scoreRank/cli/run_m8_cycle.py

# 自定义回看事件日数量
python scoreRank/cli/run_m8_cycle.py --lookback-dates 90
```

---

### 13.6 Eastmoney 组件（数据采集 + 盘后策略）

**组件职责**
- `eastmoney/main.py`：盘后数据拉取。
- `eastmoney/run_strategy.py`：执行超跌反弹策略并可导出结果。

**使用说明**
```bash
# 数据采集
python eastmoney/main.py config_1 20260210

# 策略执行+导出
python eastmoney/run_strategy.py --date 2026-02-10 --threshold 70 --export result
```

---

### 13.7 Live Tracker 组件（`sina/live_tracker`）

**组件职责**
- 同步持仓价格、维护实盘快照与交易状态。
- 为 Web 持仓页和 M7 调仓页提供当前持仓快照数据。

**使用说明**
```bash
python sina/live_tracker/run_live_tracker.py sync
```

---

### 13.8 Web 看板组件（`web/app.py`）

**组件职责**
- 展示持仓、评分、策略、管理台；支持后台任务触发。
- 策略页包含：`/sina/strategy/pyramid|weighted|quadrant|m2|m3|m4|m5|m6|m7`。
- 管理台可手动触发：`sina_bs`、`sina_score`、`sina_m8`、`sina_snapshot`、`eastmoney`。

**使用说明**
```bash
python web/app.py
# 默认: http://127.0.0.1:5001
```

---

### 13.9 常见执行顺序（手工联调）

```bash
# 1) 信号检测
python sina/bs_detection/main.py config_1 20260210

# 2) 评分
python scoreRank/run_daily.py --force

# 3) 事件复盘表构建（M1）
python scoreRank/cli/build_b_event_kpi.py

# 4) 参数搜索与回归落库（M8）
python scoreRank/cli/run_m8_cycle.py --lookback-dates 60

# 5) 实盘同步
python sina/live_tracker/run_live_tracker.py sync

# 6) Web 检查
python web/app.py
```
