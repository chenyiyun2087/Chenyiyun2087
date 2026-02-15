# 每日收盘后复盘系统 - Python实现

## 项目简介

这是一个完整的股票量化交易系统,实现了每日收盘后的复盘、入库/出库管理、六因子评分和trade/watch分层输出功能。

## 系统架构

```
项目结构:
├── configs/
│   └── config.yaml          # 系统配置文件
├── src/
│   ├── utils.py            # 工具函数
│   ├── indicators.py       # 技术指标计算
│   ├── signals.py          # 买卖点信号生成
│   ├── inventory.py        # 库状态机管理
│   ├── scoring.py          # 评分引擎
│   └── engine.py           # 主执行引擎
├── outputs/
│   ├── daily/              # 每日输出
│   └── charts/             # 图表输出
├── example.py              # 示例运行脚本
└── README.md               # 本文档
```

## 核心功能

### 1. 六因子评分体系

系统基于以下六个因子进行综合评分:

- **Breakout (突破因子, 权重22%)**: 价格突破Donchian通道的强度
- **Trend (趋势因子, 权重12%)**: 均线排列和趋势强度
- **Volume (量能因子, 权重12%)**: 成交量和成交额的变化
- **RS (相对强度因子, 权重12%)**: 相对基准的收益率
- **Liquidity (流动性因子, 权重10%)**: 基于Amihud指标
- **Contraction (波动收缩因子, 权重10%)**: 布林带宽度和ATR

### 2. 入库/出库机制

- **入库条件**: 触发买点信号(价格突破 + 成交量确认 + 技术指标确认)
- **出库条件**: 触发卖点信号(跌破MA20 / 跟踪止损 / 时间止损)
- **状态跟踪**: 记录入库价格、当前收益、最大收益、最大回撤

### 3. Trade/Watch分层

使用分位数阈值(推荐)或绝对分数阈值将库内股票分为:
- **Trade**: Top 10% (可直接交易的高分候选)
- **Watch**: 10%-40% (需继续观察)
- **Other**: 40%以下 (低分股票)

## 安装依赖

```bash
pip install -r requirements.txt
```

必需的包:
- pandas >= 1.3.0
- numpy >= 1.20.0
- pyyaml >= 5.4.0
- openpyxl >= 3.0.0 (用于Excel输出)

可选的包:
- akshare (获取A股数据)
- matplotlib (数据可视化)
- scipy (统计分析)

## 快速开始

### 1. 配置系统

编辑 `configs/config.yaml` 设置参数:

```yaml
# 因子权重
factor_weights:
  breakout: 0.22
  trend: 0.12
  volume: 0.12
  rs: 0.12
  liquidity: 0.10
  contraction: 0.10

# 分层阈值
thresholds:
  use_percentile: true
  percentile:
    trade: 0.90      # Top 10%
    watch_high: 0.90
    watch_low: 0.60  # Top 40%
```

### 2. 准备数据

数据格式要求:
```python
market_data = {
    'symbol1': DataFrame with columns [open, high, low, close, volume, amount],
    'symbol2': DataFrame with columns [open, high, low, close, volume, amount],
    ...
}
```

DataFrame的index应为日期(字符串格式 'YYYY-MM-DD')

### 3. 运行复盘

```python
from src.engine import DailyReviewEngine

# 初始化引擎
engine = DailyReviewEngine('configs/config.yaml')

# 运行每日复盘
results = engine.run_daily_review(trade_date='2024-01-15', market_data=market_data)

# 查看结果
trade_list = results['trade']      # Trade候选列表
watch_list = results['watch']      # Watch观察列表
inventory = results['inventory']   # 当前库存
```

### 4. 查看输出

系统会自动生成以下文件:

```
outputs/daily/
├── trade_20240115.csv          # Trade候选列表
├── watch_20240115.csv          # Watch观察列表
├── inventory_20240115.xlsx     # 库存状态(含历史)
└── summary_20240115.txt        # 摘要报告
```

## 使用示例

### 示例1: 单日复盘

```python
python example.py
```

运行模拟数据的单日复盘示例

### 示例2: 使用真实数据

```python
import akshare as ak
from src.engine import DailyReviewEngine
from datetime import datetime

# 获取股票列表
stock_list = ak.index_stock_cons(symbol="000300")  # 沪深300
symbols = stock_list['品种代码'].head(50).tolist()

# 获取历史数据
market_data = {}
for symbol in symbols:
    df = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date="20230101",
        end_date=datetime.now().strftime('%Y%m%d'),
        adjust="qfq"
    )
    # ... 数据处理 ...
    market_data[symbol] = df

# 运行复盘
engine = DailyReviewEngine('configs/config.yaml')
results = engine.run_daily_review(
    trade_date=datetime.now().strftime('%Y-%m-%d'),
    market_data=market_data
)
```

### 示例3: 多日连续复盘

```python
from datetime import datetime, timedelta

engine = DailyReviewEngine('configs/config.yaml')

# 连续5天复盘
for i in range(5, 0, -1):
    trade_date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
    results = engine.run_daily_review(trade_date, market_data)
```

## 输出说明

### Trade候选列表 (trade_YYYYMMDD.csv)

包含以下字段:
- `symbol`: 股票代码
- `score_adjusted`: 调整后评分(考虑惩罚项)
- `score_total`: 原始综合评分
- `rank`: 排名
- `s_breakout`, `s_trend`, `s_volume`, `s_rs`, `s_liquidity`, `s_contraction`: 各因子得分
- `in_date`: 入库日期
- `in_price`: 入库价格
- `ret_since_in`: 入库后收益率
- `score_explanation`: 评分解释

### Watch观察列表 (watch_YYYYMMDD.csv)

格式同Trade候选列表,但分数区间在10%-40%

### 库存状态 (inventory_YYYYMMDD.xlsx)

包含两个sheet:
- `inventory`: 当前库存状态
- `history`: 所有入库/出库历史记录

### 摘要报告 (summary_YYYYMMDD.txt)

包含:
- 库存统计(总数、平均收益、胜率、最大回撤)
- Trade候选Top 10
- Watch观察Top 10

## 参数调优建议

### 1. 因子权重调整

根据回测结果调整权重:
```yaml
factor_weights:
  breakout: 0.25  # 如果突破因子表现好,可以提高权重
  trend: 0.15
  volume: 0.12
  rs: 0.12
  liquidity: 0.08
  contraction: 0.08
```

### 2. 阈值调整

根据风险偏好调整:
```yaml
thresholds:
  percentile:
    trade: 0.95      # 更严格,只选Top 5%
    watch_high: 0.95
    watch_low: 0.70  # 更严格,只看Top 30%
```

### 3. 信号参数调整

调整买卖点敏感度:
```yaml
signals:
  entry:
    volume_ratio: 2.0  # 提高成交量要求
  exit:
    trail_stop_pct: 0.10  # 放宽止损范围
```

## 常见问题

### Q1: 如何处理停牌股票?

系统会自动跳过缺少数据的股票。可以在配置中设置:
```yaml
universe:
  filters:
    exclude_suspended: true
```

### Q2: 如何处理涨停股票?

系统会自动检测涨停并在评分中给予惩罚,避免次日无法买入的情况。

### Q3: 如何添加自定义因子?

在 `src/indicators.py` 中的 `FactorCalculator` 类添加新方法:
```python
def calculate_my_factor(self, window: int = 20) -> pd.Series:
    # 自定义因子计算逻辑
    return factor_values
```

然后在配置文件中添加权重。

### Q4: 如何进行回测?

系统主要用于日终复盘,如需完整回测功能,建议:
1. 保存每日的trade列表
2. 用历史数据模拟交易
3. 计算绩效指标

## 注意事项

1. **数据质量**: 系统依赖高质量的OHLCV数据,建议使用前复权数据
2. **交易成本**: 实盘交易需考虑佣金、滑点、冲击成本
3. **A股制度**: 系统考虑了T+1和涨跌停限制,但需根据实际情况调整
4. **过拟合风险**: 参数优化时务必使用样本外数据验证

## 扩展功能

系统可以进一步扩展:

1. **回测模块**: 添加完整的历史回测和绩效分析
2. **可视化**: 添加图表生成(因子分布、净值曲线等)
3. **实时监控**: 接入实时行情,盘中监控
4. **风险管理**: 添加仓位管理、组合优化
5. **机器学习**: 使用ML模型融合多个评分

## 技术支持

如有问题或建议,请查看文档或提交issue。

## 许可证

本项目仅供学习和研究使用。

## 版本历史

- v1.0.0 (2024-01): 初始版本,实现核心功能
  - 六因子评分
  - 入库/出库管理
  - Trade/Watch分层
  - 日终批处理流程
