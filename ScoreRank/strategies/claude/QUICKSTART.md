# 快速开始指南

## 5分钟快速体验

### 步骤1: 安装依赖 (1分钟)

```bash
pip install pandas numpy pyyaml openpyxl matplotlib
```

### 步骤2: 初始化项目 (30秒)

```bash
python init_project.py
```

这将创建必要的目录结构:
- `outputs/daily/` - 每日输出目录
- `outputs/charts/` - 图表目录
- `data/` - 数据目录
- `logs/` - 日志目录

### 步骤3: 运行示例 (3分钟)

```bash
python example.py
```

这将:
1. 生成20只模拟股票的数据
2. 运行一次完整的每日复盘
3. 输出Trade候选和Watch观察列表
4. 生成摘要报告

### 步骤4: 查看结果 (30秒)

复盘完成后,查看以下文件:

```bash
# 查看Trade候选
cat outputs/daily/trade_*.csv

# 查看库存状态
# 用Excel打开: outputs/daily/inventory_*.xlsx

# 查看摘要报告
cat outputs/daily/summary_*.txt
```

## 使用真实数据

如果要使用A股真实数据,需要安装AkShare:

```bash
pip install akshare
```

然后修改 `example.py` 中的代码,取消注释真实数据部分:

```python
# 在example.py的main函数中,取消这一行的注释:
engine3, results3 = example_with_real_data()
```

## 下一步

1. **阅读完整文档**: `README.md`
2. **了解项目结构**: `PROJECT_STRUCTURE.md`
3. **调整配置**: 编辑 `configs/config.yaml`
4. **自定义因子**: 修改 `src/indicators.py`
5. **调整信号**: 修改 `src/signals.py`

## 核心配置说明

### 因子权重 (可调整)

在 `configs/config.yaml` 中:

```yaml
factor_weights:
  breakout: 0.22      # 突破因子权重
  trend: 0.12         # 趋势因子权重
  volume: 0.12        # 量能因子权重
  rs: 0.12            # 相对强度权重
  liquidity: 0.10     # 流动性权重
  contraction: 0.10   # 波动收缩权重
```

### Trade/Watch阈值 (可调整)

```yaml
thresholds:
  use_percentile: true
  percentile:
    trade: 0.90       # Trade: Top 10%
    watch_high: 0.90  
    watch_low: 0.60   # Watch: 10%-40%
```

### 买卖点规则 (可调整)

```yaml
signals:
  entry:
    price_above_pivot: true
    volume_confirm: true
    volume_ratio: 1.5
    price_above_ma20: true
    
  exit:
    break_ma20: true
    trail_stop_pct: 0.08    # 8%跟踪止损
    max_hold_days: 60       # 最长持有60天
```

## 输出文件说明

### Trade候选列表 (`trade_YYYYMMDD.csv`)

包含可直接交易的高分股票,按评分降序排列。

主要字段:
- `symbol`: 股票代码
- `score_adjusted`: 调整后评分
- `ret_since_in`: 入库后收益率
- `score_explanation`: 评分解释

### Watch观察列表 (`watch_YYYYMMDD.csv`)

包含中等分数的股票,需继续观察。

格式同Trade列表。

### 库存状态 (`inventory_YYYYMMDD.xlsx`)

包含两个sheet:
- **inventory**: 当前所有在库股票
- **history**: 所有入库/出库历史

### 摘要报告 (`summary_YYYYMMDD.txt`)

文本格式的摘要,包含:
- 库存统计(总数、平均收益、胜率)
- Top 10 Trade候选
- Top 10 Watch观察

## 常见使用场景

### 场景1: 每日复盘

```python
from src.engine import DailyReviewEngine
from datetime import datetime

# 准备当日数据
market_data = get_today_market_data()  # 你的数据获取函数

# 运行复盘
engine = DailyReviewEngine('configs/config.yaml')
results = engine.run_daily_review(
    trade_date=datetime.now().strftime('%Y-%m-%d'),
    market_data=market_data
)

# 查看结果
print(f"Trade候选: {len(results['trade'])} 只")
print(results['trade'][['symbol', 'score_adjusted']].head())
```

### 场景2: 历史回溯

```python
from datetime import datetime, timedelta

engine = DailyReviewEngine('configs/config.yaml')

# 回溯最近30天
for i in range(30, 0, -1):
    date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
    
    # 获取该日期的数据
    market_data = get_historical_data(date)
    
    # 运行复盘
    results = engine.run_daily_review(date, market_data)
```

### 场景3: 实时监控

```python
import time

engine = DailyReviewEngine('configs/config.yaml')

while True:
    # 等待收盘
    if is_market_closed():
        # 获取今日数据
        market_data = get_today_market_data()
        
        # 运行复盘
        results = engine.run_daily_review(
            datetime.now().strftime('%Y-%m-%d'),
            market_data
        )
        
        # 发送通知
        send_notification(results)
        
    # 每小时检查一次
    time.sleep(3600)
```

## 性能优化建议

### 1. 数据预加载

```python
# 一次性加载所有股票数据,避免重复IO
market_data = batch_load_data(symbols, start_date, end_date)
```

### 2. 并行计算

```python
from multiprocessing import Pool

def calculate_factors_parallel(data_dict):
    with Pool(processes=4) as pool:
        results = pool.map(calculate_single_stock, data_dict.items())
    return dict(results)
```

### 3. 缓存中间结果

```python
# 缓存技术指标计算结果
@lru_cache(maxsize=1000)
def get_indicators(symbol, date):
    # 计算逻辑
    pass
```

## 故障排除

### 问题1: ModuleNotFoundError

```bash
# 解决方案:
pip install -r requirements.txt
```

### 问题2: 数据格式错误

```python
# 确保DataFrame包含必需的列:
required_columns = ['open', 'high', 'low', 'close', 'volume', 'amount']

# 确保索引是日期格式:
df.index = pd.to_datetime(df.index)
df.index = df.index.strftime('%Y-%m-%d')
```

### 问题3: 配置文件错误

```bash
# 检查YAML语法
python -c "import yaml; yaml.safe_load(open('configs/config.yaml'))"
```

## 获取帮助

1. 查看详细文档: `README.md`
2. 查看项目结构: `PROJECT_STRUCTURE.md`
3. 查看代码注释: 每个模块都有详细的docstring

## 下一步学习

1. **因子研究**: 研究各因子的有效性,调整权重
2. **信号优化**: 优化买卖点规则,提高准确率
3. **回测验证**: 用历史数据验证策略有效性
4. **风险管理**: 添加仓位管理和止损机制
5. **实盘应用**: 接入实盘数据,自动化执行

祝使用愉快! 🚀
