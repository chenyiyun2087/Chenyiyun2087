# eastmoney 模块说明

`eastmoney/` 是盘后策略与情绪数据模块，主要负责**数据抓取、超跌反弹筛选、结果导出**。

## 组件回顾

- `main.py`：模块主入口（任务编排）。
- `daily_run.py`：日常任务执行封装。
- `post_market_scanner.py`：盘后扫描核心。
- `oversold_bounce_strategy.py`：超跌反弹策略逻辑。
- `run_strategy.py`：策略执行与结果输出。
- `data_controller.py`：数据读取与预处理。
- `backtest_framework.py`：策略回测支持。
- `config/`：股票池与运行配置。

## 快速运行

```bash
python eastmoney/main.py
python eastmoney/run_strategy.py
```

## 输出产物

- 数据库存储：策略结果表（按本地配置）。
- 文件导出：`result/` 下的触发池、交易池、评分表、Excel 报告。

## 可改进点（待统一实施）

- 结果文件命名增加统一时间戳与版本号。
- 数据抓取失败时增加分级重试与告警。
- 策略参数与报告绑定（参数快照落盘）。
