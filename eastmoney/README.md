# Eastmoney 策略模块说明

本模块包含两部分核心功能：
1.  **多空情绪数据采集**：抓取东方财富股吧的多空情绪数据并存入数据库 (`em_duokong_sentiment` 表)。
2.  **超跌反弹策略扫描**：基于情绪数据和每日行情数据，运行超跌反弹策略 (`run_strategy.py`)。

## 1. 环境准备

1.  **数据库**：
    *   需配置本地 MySQL 数据库 `chenyiyun` 和 `tushare_stock`。
    *   确保 `tushare_stock.dwd_stock_daily_standard` 表中有每日行情数据（Open/High/Low/Close/Volume）。
    *   确保 `chenyiyun.a_share_stock_list` 表中有 A 股基本信息。
2.  **依赖**：
    *   Python 库：`selenium`, `pandas`, `pymysql`, `sqlalchemy`。
    *   浏览器驱动：ChromeDriver（用于 Selenium 抓取情绪数据）。
3.  **配置文件**：
    *   `Eastmoney/config/config_1.json`：用于数据采集的配置。

## 2. 数据采集 (Data Collection)

使用 `Eastmoney.main` 抓取当天的多空情绪数据：

### 单只/多只股票检测
```bash
# 单只股票
python -m eastmoney.main config_1 20260209 --stock 688158 --max-workers 1

# 多只股票
python -m eastmoney.main config_1 20260209 --stock-codes 688158 600000 000001 --max-workers 3
```

### 批量检测 (使用配置文件)
```bash
python -m eastmoney.main config_1 20260209
```
*注：脚本会自动将结果 upsert 到 `em_duokong_sentiment` 表。*

## 3. 策略扫描 (Strategy Execution)

在收盘后（确保当天情绪数据和行情数据已入库），运行策略扫描：

### 基本用法
```bash
# 扫描今天的数据，默认空方情绪阈值 70%
python eastmoney/run_strategy.py
```

### 指定日期与阈值
您可以回测历史日期，或调整情绪过滤的阈值：

```bash
# 扫描 2026-02-08 的数据，筛选空方情绪 > 60% 的股票
python eastmoney/run_strategy.py --date 2026-02-08 --threshold 60

# 扫描 2026-02-09 的数据，筛选空方情绪 > 80% 的股票，结果导出到指定目录
python eastmoney/run_strategy.py --date 2026-02-09 --threshold 80 --export /path/to/save
```

### 输出结果
脚本运行后将生成 Excel 报告 (`超跌反弹筛选_YYYY-MM-DD.xlsx`)，包含：
*   **综合得分**：结合技术面、情绪面、筹码面的评分。
*   **技术指标**：RSI, BIAS, KDJ 等。
*   **情绪数据**：空方占比。
*   **入场信号**：如“放量突破单峰密集”、“缩量回调后放量”等。
65: 
66: ## 4. 自动化 Pipeline (Automated Pipeline)
67: 
68: 使用 `daily_run.py` 可以一键串联“数据采集”与“策略扫描”任务。该脚本会自动计算最近一个交易日。
69: 
70: ### 基本用法
71: ```bash
72: # 自动运行最近一个交易日的任务
73: python -m Eastmoney.daily_run
74: ```
75: 
76: ### 指定日期
77: ```bash
78: # 运行指定日期的任务
79: python -m Eastmoney.daily_run 20260206
80: ```
81: 
82: ### 交易日判断逻辑
83: -   **周末运行**：自动回溯至上周五。
84: -   **交易日 16:30 前运行**：自动回溯至前一个交易日（因今日数据未就绪）。
85: -   **交易日 16:30 后运行**：执行当日任务。
