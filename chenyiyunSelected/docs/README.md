# chenyiyunSelected 模块说明（组件与业务逻辑）

本目录当前承载三条主线能力：

1. **聚宽原始策略**（`chenyiyun1.py`）
2. **本地化迁移策略**（`local_strategy_adapter.py` + `run_local_backtest.py`）
3. **选股评分与投研产物**（`StockScores.py` / `StockPrompt.py` / `PrepareInputFile.py` / 可视化文件）

---

## 1. 目录组件总览

### 1.1 策略核心组件 (`strategy/`)

- `strategy/chenyiyun1.py`
  - 聚宽平台版本策略（原始版本）。
  - 核心逻辑：高股息 → 高波动 → 低杠杆 → 小市值，周调仓 + 涨停打开卖出。

- `strategy/local_strategy_adapter.py`
  - 本地化策略适配器（读取 `tushare_stock` 数仓）。
  - 提供：
    - 数据提供层 `TushareWarehouseProvider`
    - 选股策略层 `LocalHighDividendStrategy`
    - 信号输出与落库（`build_daily_signals` / `save_daily_signals`）

- `strategy/run_local_backtest.py`
  - 本地回测入口。
  - 使用 `backtest_engine` + `TushareDailyFeed` + 本地策略执行器进行日频回测并导出报告。

### 1.2 数据处理与投研组件 (`research/`)

- `research/PrepareInputFile.py`
  - 输入文件清洗、代码规范化、字段对齐（用于后续评分/分析流程）。

- `research/StockScores.py`
  - 评分模型执行与报告输出（包含 PDF 报告生成）。

- `research/StockPrompt.py`
  - 将选股数据组织为 LLM 可用分析 Prompt。

- `research/StockImage.py`
  - 图形可视化脚本（股票点位/散点展示类）。

### 1.3 文档与说明 (`docs/`)

- `docs/MIGRATION_TO_LOCAL.md`
  - 策略迁移说明与阶段记录。
- `docs/chenyiyunpingjia.html`
  - 策略评估页面存档。

---

## 2. 业务逻辑分层（按执行链路）

### 2.1 链路A：策略信号生成（本地）

1. **读取交易日与股票池**
   - 从 `dwd_daily` / `dwd_stock_label_daily` 获取交易日与股票标签（ST、上市天数等）。
2. **因子拼接**
   - `dwd_daily_basic`：股息率、流通市值
   - `dwd_fina_indicator`：`mlev`
   - `dws_liquidity_factor`：换手波动
3. **选股过滤**
   - 科创/北交过滤
   - ST / 次新过滤
   - 百分位链式筛选：
     - 高股息（前50%）
     - 高换手波动（保留80%）
     - 低杠杆（保留50%）
4. **排序输出**
   - 依据流通市值升序，输出候选池。
5. **可选：信号落库**
   - 生成 `BUY` + 等权 `target_weight`，写入 `ads_local_strategy_signals`。

对应实现：`strategy/local_strategy_adapter.py`

### 2.2 链路B：本地回测执行

1. 周一为调仓点，调用本地选股器生成目标池。
2. `TushareDailyFeed` 按回测区间读取日频K线。
3. 回测策略执行：
   - 非目标持仓卖出
   - 目标且空仓股票按等权预算买入（整手）
4. 输出回测报告 JSON。

对应实现：`strategy/run_local_backtest.py` + `backtest_engine` 相关组件。

### 2.3 链路C：投研评分与报告

1. 对输入股票列表进行字段标准化。
2. 执行评分模型并输出明细。
3. 产出 PDF 与 LLM 分析 Prompt。

对应实现：`research/PrepareInputFile.py`、`research/StockScores.py`、`research/StockPrompt.py`。

---

## 3. 推荐使用顺序

### 3.1 日常离线选股（仅信号）

```bash
python chenyiyunSelected/strategy/local_strategy_adapter.py \
  --date 2026-02-17 \
  --host 127.0.0.1 --port 3306 --user root --password '***' \
  --database tushare_stock \
  --emit-signals
```

### 3.2 回测

```bash
python chenyiyunSelected/strategy/run_local_backtest.py \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --host 127.0.0.1 --port 3306 --user root --password '***' \
  --database tushare_stock \
  --output backtest/results/chenyiyun_local_result.json
```

