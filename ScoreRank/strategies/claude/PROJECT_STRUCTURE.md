# 项目结构说明

## 目录结构

```
stock-review-system/
│
├── configs/                    # 配置文件目录
│   ├── config.yaml            # 主配置文件
│   └── config.yaml.example    # 配置示例(自动生成)
│
├── src/                       # 源代码目录
│   ├── utils.py              # 工具函数和数据结构
│   ├── indicators.py         # 技术指标计算模块
│   ├── signals.py            # 买卖点信号生成模块
│   ├── inventory.py          # 库状态机管理模块
│   ├── scoring.py            # 评分引擎模块
│   ├── engine.py             # 主执行引擎
│   └── visualization.py      # 可视化模块
│
├── data/                      # 数据目录
│   ├── raw/                  # 原始数据
│   └── processed/            # 处理后的数据
│
├── outputs/                   # 输出目录
│   ├── daily/                # 每日输出
│   │   ├── trade_YYYYMMDD.csv        # Trade候选列表
│   │   ├── watch_YYYYMMDD.csv        # Watch观察列表
│   │   ├── inventory_YYYYMMDD.xlsx   # 库存状态
│   │   └── summary_YYYYMMDD.txt      # 摘要报告
│   ├── charts/               # 图表输出
│   │   ├── factor_dist_YYYYMMDD.png     # 因子分布图
│   │   ├── factor_corr_YYYYMMDD.png     # 因子相关性图
│   │   ├── score_vs_return_YYYYMMDD.png # 评分收益图
│   │   └── inventory_perf_YYYYMMDD.png  # 库存表现图
│   └── backtest/             # 回测输出
│
├── logs/                      # 日志目录
│
├── example.py                 # 示例运行脚本
├── init_project.py           # 项目初始化脚本
├── requirements.txt          # Python依赖
└── README.md                 # 项目说明文档
```

## 核心模块说明

### 1. utils.py - 工具模块
**功能**: 提供基础工具函数和数据结构

**主要类和函数**:
- `InventoryRecord`: 库存记录数据类
- `SignalRecord`: 信号记录数据类
- `ScoreRecord`: 评分记录数据类
- `normalize_weights()`: 权重归一化
- `rank_to_percentile()`: 百分位数转换
- `calculate_sharpe_ratio()`: Sharpe比率计算
- `calculate_information_coefficient()`: IC计算
- `check_limit_price()`: 涨跌停检测
- `calculate_transaction_cost()`: 交易成本计算
- `PerformanceMetrics`: 绩效指标计算类

### 2. indicators.py - 技术指标模块
**功能**: 计算各类技术指标和六个因子

**主要类**:
- `TechnicalIndicators`: 技术指标计算类
  - `calculate_ma()`: 移动平均
  - `calculate_atr()`: 平均真实波幅
  - `calculate_bollinger_bands()`: 布林带
  - `calculate_donchian_channel()`: 唐奇安通道
  - `calculate_rsi()`: 相对强弱指数
  - `calculate_macd()`: MACD
  - `calculate_adx()`: 平均趋向指数

- `FactorCalculator`: 因子计算器
  - `calculate_breakout_factor()`: 突破因子
  - `calculate_trend_factor()`: 趋势因子
  - `calculate_volume_factor()`: 量能因子
  - `calculate_rs_factor()`: 相对强度因子
  - `calculate_liquidity_factor()`: 流动性因子
  - `calculate_contraction_factor()`: 波动收缩因子
  - `calculate_all_factors()`: 计算所有因子

### 3. signals.py - 信号生成模块
**功能**: 生成买卖点信号

**主要类**:
- `SignalGenerator`: 单股票信号生成器
  - `generate_entry_signals()`: 生成买点信号
  - `generate_exit_signals()`: 生成卖点信号
  - `generate_all_signals()`: 生成所有信号

- `MultiStockSignalGenerator`: 多股票信号生成器
  - `generate_signals_for_all()`: 为所有股票生成信号
  - `get_latest_signals()`: 获取最新信号

### 4. inventory.py - 库状态机模块
**功能**: 管理股票的入库/出库状态

**主要类**:
- `InventoryStateMachine`: 库状态机
  - `add_to_inventory()`: 入库
  - `remove_from_inventory()`: 出库
  - `update_inventory()`: 更新状态
  - `get_inventory_list()`: 获取库存列表
  - `get_history()`: 获取历史记录
  - `process_signals()`: 处理信号
  - `save_state()` / `load_state()`: 保存/加载状态

- `InventoryAnalyzer`: 库存分析器
  - `analyze_inventory_performance()`: 分析库存表现
  - `get_top_performers()`: 获取最佳表现
  - `get_bottom_performers()`: 获取最差表现

### 5. scoring.py - 评分引擎模块
**功能**: 计算因子分数并综合评分

**主要类**:
- `ScoringEngine`: 评分引擎
  - `normalize_factor()`: 单因子标准化
  - `normalize_all_factors()`: 所有因子标准化
  - `calculate_composite_score()`: 计算综合评分
  - `apply_penalties()`: 应用惩罚项
  - `score_inventory()`: 对库存评分

- `TradeWatchClassifier`: Trade/Watch分层分类器
  - `classify()`: 分类
  - `get_trade_list()`: 获取Trade列表
  - `get_watch_list()`: 获取Watch列表
  - `get_all_classified()`: 获取所有分类结果

- `ScoreExplainer`: 评分解释器
  - `explain_score()`: 解释单个评分
  - `batch_explain()`: 批量解释

### 6. engine.py - 主执行引擎模块
**功能**: 整合所有模块,执行每日复盘流程

**主要类**:
- `DailyReviewEngine`: 每日复盘引擎
  - `run_daily_review()`: 运行每日复盘
  - 内部步骤:
    1. 数据预处理
    2. 计算技术指标和因子
    3. 生成买卖点信号
    4. 更新库状态
    5. 对库内股票评分
    6. 分层输出trade/watch
    7. 生成报表和可视化

### 7. visualization.py - 可视化模块
**功能**: 生成各类图表

**主要类**:
- `ChartGenerator`: 图表生成器
  - `plot_factor_distribution()`: 因子分布图
  - `plot_factor_correlation()`: 因子相关性图
  - `plot_score_vs_return()`: 评分vs收益图
  - `plot_inventory_performance()`: 库存表现图
  - `generate_all_charts()`: 生成所有图表

- `create_summary_table()`: 创建汇总表格

## 数据流程

```
原始市场数据 (OHLCV)
    ↓
数据预处理 (复权、校验、填充)
    ↓
技术指标计算 (MA、ATR、BB、Donchian等)
    ↓
因子计算 (6个原始因子值)
    ↓
信号生成 (买点/卖点)
    ↓
库状态更新 (入库/出库/续存)
    ↓
因子标准化 (横截面Rank -> 0-100分)
    ↓
综合评分 (加权合成 + 惩罚项)
    ↓
分层分类 (Trade/Watch/Other)
    ↓
输出结果 (CSV/Excel/图表/报告)
```

## 配置文件说明

配置文件 `configs/config.yaml` 包含:

1. **系统配置**: 输出目录、日志级别等
2. **数据源配置**: 数据来源、复权方式等
3. **样本池配置**: 市场范围、过滤条件等
4. **基准配置**: 基准指数选择
5. **成本参数**: 税费、佣金、滑点等
6. **因子权重**: 六个因子的权重配置
7. **因子参数**: 各因子的计算参数
8. **信号参数**: 买卖点规则参数
9. **阈值配置**: Trade/Watch分层阈值
10. **回测配置**: 回测相关参数
11. **验证配置**: IC分析、统计检验等
12. **输出配置**: 输出内容控制

## 使用流程

### 1. 初始化项目
```bash
python init_project.py
```

### 2. 准备数据
```python
# 方式1: 使用AkShare获取真实数据
import akshare as ak
df = ak.stock_zh_a_hist(symbol="600000", period="daily", 
                        start_date="20230101", adjust="qfq")

# 方式2: 从本地数据库读取
# df = pd.read_sql(...)

# 方式3: 从CSV文件读取
# df = pd.read_csv(...)
```

### 3. 运行复盘
```python
from src.engine import DailyReviewEngine

engine = DailyReviewEngine('configs/config.yaml')
results = engine.run_daily_review(trade_date, market_data)
```

### 4. 查看结果
- CSV文件: `outputs/daily/trade_*.csv`, `watch_*.csv`
- Excel文件: `outputs/daily/inventory_*.xlsx`
- 图表: `outputs/charts/*.png`
- 报告: `outputs/daily/summary_*.txt`

## 扩展开发

### 添加新因子
1. 在 `indicators.py` 的 `FactorCalculator` 类中添加计算方法
2. 在 `config.yaml` 中添加因子权重和参数
3. 更新评分引擎以包含新因子

### 添加新信号
1. 在 `signals.py` 中修改 `generate_entry_signals()` 或 `generate_exit_signals()`
2. 在 `config.yaml` 中添加信号参数
3. 测试信号有效性

### 自定义惩罚项
1. 在 `scoring.py` 的 `ScoringEngine.apply_penalties()` 方法中添加逻辑
2. 在配置文件中添加惩罚参数

## 注意事项

1. **数据质量**: 确保输入数据的完整性和准确性
2. **参数调优**: 根据回测结果调整参数
3. **过拟合控制**: 避免过度优化参数
4. **交易成本**: 实盘需考虑实际交易成本
5. **风险管理**: 设置合理的止损和仓位控制

## 常见问题

请参考 README.md 中的"常见问题"章节
