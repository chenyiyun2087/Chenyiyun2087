# post_close_review

“每日收盘后复盘入库/出库评分系统”的 Python 可执行实现。

## 能力覆盖
- 日频状态机：`buy_signal` 入库、`sell_signal` 出库、跟踪 `ret_since_in/max_ret/max_dd`。
- 六因子：Breakout / Trend / Volume / RS / Liquidity / Contraction。
- 横截面 Rank 标准化到 0-100 + 权重归一化线性合成。
- 分层输出：`trade/watch/keep`，并附带可交易性过滤（成交额、涨停不可买）。
- 验证工具：RankIC、分组收益、Bootstrap Sharpe 区间。
- 回测骨架：t 日收盘信号、t+1 执行的简化回测。

## 快速运行
```bash
python -m post_close_review.example_run
```

## 输入数据格式
`price_df` 必须包含：
- `trade_date, symbol, open, high, low, close, prev_close, volume, amount`

`benchmark_df` 必须包含：
- `trade_date, close`

## 核心模块
- `factors.py`：原始因子计算 + 买卖信号。
- `inventory.py`：入库/出库状态机。
- `scoring.py`：标准化、总分、trade/watch 分层。
- `pipeline.py`：日终批处理主流程。
- `validation.py`：IC/分组收益/bootstrap。
- `backtest.py`：t+1 简化交易模拟。
