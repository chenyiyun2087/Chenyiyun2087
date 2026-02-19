# chenyiyunSelected 模块说明

`chenyiyunSelected/` 用于承接“聚宽策略本地化迁移”，并连接回测与研究工具链。

## 组件回顾

- `strategy/`
  - `chenyiyun1.py`：原始策略实现。
  - `local_strategy_adapter.py`：本地数据仓适配与信号生成。
  - `run_local_backtest.py`：本地回测入口。
- `research/`
  - `StockScores.py`：评分与报告。
  - 其他研究脚本：用于输入清洗、Prompt 生成、可视化。
- `docs/`
  - 迁移记录、评估文档、说明文档。

## 推荐命令

```bash
python chenyiyunSelected/strategy/local_strategy_adapter.py --help
python chenyiyunSelected/strategy/run_local_backtest.py --help
```

## 可改进点（待统一实施）

- `strategy` 与 `research` 的输入输出协议文档化。
- 回测结果与信号落库共用统一 schema。
- 为迁移策略补充最小回归样例数据。


## 每日实盘信号（新增）

新增 `strategy/daily_signal_runner.py`，用于日常实盘执行：

```bash
python -m chenyiyunSelected.strategy.daily_signal_runner \
  --date 2026-02-20 \
  --host 127.0.0.1 --port 3306 --user root --password '***' --database tushare_stock \
  --top 10 --total-equity 1000000 \
  --emit-signals
```

能力说明：
- 读取当日目标股票池并归一化目标权重。
- 对比 `live_positions` 当前持仓，生成 `BUY/SELL` 调仓指令。
- 指令落库到 `ads_local_strategy_orders`，可选 webhook 推送。


信号快照表（供 Web 展示）：`ads_chenyiyun_selected_signals`，包含：
- `signal_time`（信号时间点）
- `ts_code` / `stock_name`（股票代码/名称）
- `open_price`（开仓参考价）
- `allocated_shares`（分配购买数量）
