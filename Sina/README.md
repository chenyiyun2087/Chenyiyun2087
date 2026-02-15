# Sina B/S 策略系统

Sina 项目使用了基于 **B/S 点位检测 (图像识别)** 与 **技术面量化评分** 相结合的混合策略。本系统包含数据采集、策略回测与实盘跟踪三个核心模块。

## 1. 系统架构与数据依赖

### 1.1 数据依赖 (Prerequisites)

本系统依赖以下上游数据，请确保在运行前数据已准备就绪：

1.  **基础行情数据 (`tushare_stock.dwd_stock_daily_standard`)**:
    *   **来源**: 上游 ETL 任务 (通常由 `ScoreRank` 或 `Eastmoney` 相关流程触发，或是独立的 Tushare 数据同步任务)。
    *   **内容**: 包含全市场股票的日线行情（Open, High, Low, Close, Volume），必须包含 **前复权 (qfq)** 数据。
    *   **用途**: 用于计算技术面评分因子（趋势、突破、量能等）以及回测中的撮合交易。
    *   **检查**: 运行前请确认数据库中当日数据已入库。

2.  **股票列表 (`stock_codes.xlsx` / Database)**:
    *   **用途**: B/S 检测的目标股票池。

### 1.2 执行流程 (Execution Flow)

根据 `scheduler.py` 的自动化配置，每日的标准执行顺序如下：

1.  **[15:20] B/S 信号检测**: 
    *   运行 `Sina/bs_detection/main.py`。
    *   此任务在收盘后立即运行，抓取网页信号。**不依赖**行情数据库的更新。
2.  **[21:00+] 数据就绪检查**: 
    *   系统等待 `tushare_stock.dwd_stock_daily_standard` 数据同步完成（通常在 21:00 后）。
3.  **[21:00+] 策略评分与同步**:
    *   **评分**: 运行 `ScoreRank/run_daily.py`，结合 B/S 信号与当日行情进行量化分配，生成交易池。
    *   **同步**: 运行 `Sina/live_tracker/run_live_tracker.py sync`，更新实盘追踪器的现价。
4.  **[次日开盘] 执行交易**:
    *   根据前一晚生成的 `signals` 在集合竞价或开盘时进行实盘操作。

---

## 2. 模块使用指南

### 2.1 B/S 点位检测 (BS Detection)

负责自动化下载个股行情截图并识别 B/S 点位。

*   **脚本路径**: `Sina/bs_detection/main.py`
*   **功能**:
    1.  模拟浏览器访问新浪财经，截取 K 线图。
    2.  识别图片中的 B (买入) / S (卖出) 信号点。
    3.  将结果存入 `bs_detection_results` 数据库。

**使用示例**:

```bash
# 格式: python Sina/bs_detection/main.py <配置文件名> <日期YYYYMMDD>

# 1. 标准运行 (使用 config/config_1.json 配置)
.venv/bin/python Sina/bs_detection/main.py config_1 20260210

# 2. 仅运行检测 (跳过截图，适用于截图已完成的情况)
.venv/bin/python Sina/bs_detection/main.py config_1 20260210 --skip-capture

# 3. 自定义并发数
.venv/bin/python Sina/bs_detection/main.py config_1 20260210 --screenshot-workers 5 --detect-workers 10
```

### 2.2 如何确认程序状态 (Verification)

当 `bs_detection/main.py` 运行完成后，可以通过以下三种方式确认程序执行状态与结果：

1.  **检查日志文件**:
    *   **路径**: `Sina/sina_bs_capture.log`
    *   **关注内容**: 搜索 "处理完成" 或 "检测阶段耗时"，日志末尾会输出成功与失败的统计信息。

2.  **检查 Excel 结果表**:
    *   **路径**: `Sina/SinaAppBS/result/`
    *   **文件名**: `<配置名>_<日期>.xlsx` (如 `config_1_20260210.xlsx`)
    *   **内容**: 该表汇总了所有股票的 B/S 信号检测结果。如果文件存在且包含当日数据，说明检测逻辑已完成。

3.  **检查数据库内容**:
    *   **表名**: `chenyiyun.bs_detection_results`
    *   **查询语句**:
        ```sql
        SELECT * FROM bs_detection_results WHERE batch_date = '20260210' LIMIT 10;
        ```
    *   **意义**: 这是后端评分系统真正的取数来源。确保 `has_buy_signal` 和 `has_sell_signal` 字段有数据且符合预期。

---

### 2.3 回测框架 (Backtest)

用于验证策略在历史数据上的表现。

*   **脚本路径**: `Sina/backtest/run_backtest.py`
*   **逻辑**:
    1.  **筛选**: 仅选择当日出现 **B点** 且无后续 S点的股票。
    2.  **评分**: 基于技术因子 (Breakout, Trend, Volume 等) 对筛选后的股票打分。
    3.  **交易**: 买入 Top N，并持有直到出现卖出信号或掉出 Top 榜单。

**使用示例**:

```bash
# 1. 运行指定时间段的回测
.venv/bin/python Sina/backtest/run_backtest.py --start-date 2024-01-01 --end-date 2024-12-31

# 2. 指定 Top N (持仓只数)
.venv/bin/python Sina/backtest/run_backtest.py --start-date 2024-01-01 --end-date 2024-12-31 --top-n 5

# 3. 指定初始资金
.venv/bin/python Sina/backtest/run_backtest.py --start-date 2024-01-01 --end-date 2024-12-31 --capital 1000000
```

### 2.3 实盘跟踪 (Live Tracker)

用于日常交易记录、持仓管理及信号生成。

*   **脚本路径**: `Sina/live_tracker/run_live_tracker.py`
*   **功能**:
    *   **signals**: 联动评分系统，输出当日推荐买入/卖出清单。
    *   **positions**: 查看当前持仓盈亏。
    *   **buy/sell**: 记录交易。
    *   **report**: 生成 HTML 资产报告。

**使用示例**:

```bash
# 1. 获取当日交易信号 (最常用)
# 系统会自动拉取最新的 B/S 结果和行情数据进行评分
.venv/bin/python Sina/live_tracker/run_live_tracker.py signals

# 2. 查看当前持仓
.venv/bin/python Sina/live_tracker/run_live_tracker.py positions

# 3. 记录买入 (价格填0表示使用收盘价)
.venv/bin/python Sina/live_tracker/run_live_tracker.py buy -s 000001 -n 1000 -p 0 -r "B点突破"

# 4. 生成 HTML 报告
.venv/bin/python Sina/live_tracker/run_live_tracker.py report --html
```

---

## 3. 策略评分逻辑详情

策略采用 **"门票-评分"** 两段式机制。

### 第一阶段：B/S 筛选 (The Filter)
只有满足以下条件的股票才能进入评分池：
1.  检测到最新的 **B点 (买入信号)**。
2.  该 B 点之后，**没有出现 S 点 (卖出信号)**。
    *   即：`latest_buy_date > latest_sell_date` (或无 sell date)。

### 第二阶段：技术面评分 (The Score)
对通过筛选的股票进行打分 (0-100分)，核心因子如下：

| 因子名称 | 代码 | 权重 | 逻辑说明 |
| :--- | :--- | :--- | :--- |
| **Breakout (突破)** | `s_breakout` | **0.22** | **核心**。股价接近或突破近20日新高。 |
| **Trend (趋势)** | `s_trend` | 0.12 | 均线多头排列且斜率向上。 |
| **Volume (量能)** | `s_volume` | 0.12 | 成交量活跃。 |
| **RS (相对强度)** | `s_rs` | 0.12 | 股价走势强于大盘 (Relative Strength)。 |
| **Liquidity (流动性)** | `s_liquidity` | 0.10 | 优选流动性好、非微盘股。 |
| **Contraction (收敛)** | `s_contraction` | 0.10 | 波动率收敛，寻找蓄势待发形态。 |

*注：详细因子定义请参考 `Sina/backtest/bs_scorer.py`*

### 风险控制 (Risk Penalty)
触发以下条件将扣分：
*   **停牌**: -40分
*   **ST股**: -25分
*   **一字涨停**: -20分 (无法买入)

---

## 4. 因子分 (Factor Score) 说明

本系统集成了基于 `factor_optimizer` 的全维度因子评分，作为 B/S 策略的补充参考。

### 4.1 评分标准
*   **满分**: **10分**。
*   **分值分布**: 
    *   **8.0 - 10.0**: 因子质量极佳，属于全市场因子表现前列。
    *   **6.0 - 8.0**: 因子表现良好。
    *   **< 6.0**: 某项或多项因子（如估值过高、质量较差、市值过大）存在拖累。

### 4.2 评分逻辑
该评分综合了 7 个大类因子的**加权平均值** (Weighted Average)。

**当前权重分配**:
1.  **技术 (Technical)**: **0.25** (侧重指标形态)
2.  **资金 (Capital)**: **0.25** (侧重主力动向)
3.  **动量 (Momentum)**: 0.15
4.  **筹码 (Chip)**: 0.15
5.  **市值 (Size)**: 0.10
6.  **价值 (Value)**: 0.05 (已调低)
7.  **质量 (Quality)**: 0.05 (已调低)

**计算公式**: 
`Factor Score = Σ (因子分_i * 权重_i)`

---

### 4.3 与总分 (Score) 的区别
*   **总分 (Score)**: 100 分制。侧重于 **B/S 信号触发时的技术面爆发力** 和 **短线博弈空间**。
*   **因子分 (Factor Score)**: 10 分制。侧重于 **个股的综合基本面与动能质量**。


---

## 5. 自选股监控 (Self-selected Monitor)

该功能允许对非 B/S 信号触发的自选股进行同样的策略评分。

### 5.1 逻辑与隔离
*   **数据隔离**: 
    *   **Sina 每日排行**: 仅展示满足 B/S 策略（B点+无S点）且评分合格的股票。
    *   **自选股监控**: 展示所有被标记为 **自选 (`is_self_selected=1`)** 的股票，无论是否出现 B/S 信号。
*   **评分逻辑**: 自选股采用与 B/S 策略完全相同的评分算法（包含技术面打分 + 因子分）。

### 5.2 如何添加自选股
目前的自选股列表来源于数据库 `chenyiyun.a_share_stock_list` 中的 `is_self_selected` 字段。
通常通过 `Eastmoney` 相关的工具或直接操作数据库进行标记。

### 5.3 运行方式
自选股的评分会在每日 `ScoreRank/run_daily.py` 运行时自动计算并更新。


