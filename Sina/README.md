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

每日的标准执行顺序如下：

1.  **[15:30+] 数据同步**: 确保 `dwd_stock_daily_standard` 表已更新当日收盘数据。
2.  **[16:00+] B/S 信号检测**: 运行 `Sina/bs_detection/main.py`，抓取新浪财经网页数据，识别 B/S 信号并存入 `bs_detection_results` 表。
3.  **[17:00+] 策略评分与选股**: (通常集成在 Backtest 或 Live Tracker 中) 读取 B/S 信号与行情数据，生成评分与交易信号。
4.  **[盘后/盘前] 实盘/回测**: 运行回测验证策略，或使用实盘工具记录交易计划。

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

### 2.2 回测框架 (Backtest)

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
