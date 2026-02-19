# backtest 引擎说明

`backtest/` 是可复用的通用回测引擎，服务于本仓库多个策略模块。

## 结构回顾

- `src/backtest_engine/core/`
  - `engine.py`：回测主引擎
  - `broker.py`：撮合与成交逻辑
  - `portfolio.py`：组合与仓位管理
  - `clock.py` / `types.py` / `strategy.py`：基础抽象
- `src/backtest_engine/datafeed/`
  - `tushare_feed.py` / `warehouse_feed.py` / `mock_feed.py`
- `src/backtest_engine/metrics/`
  - 收益、回撤、胜率等绩效指标
- `src/backtest_engine/reporting/`
  - 回测结果结构定义与导出
- `src/backtest_engine/examples/`
  - demo 策略与运行示例
- `tests/`
  - `test_engine_smoke.py`、`test_metrics.py`

## 安装与测试

```bash
cd backtest
pytest -q
```

## 可改进点（待统一实施）

- 扩展交易成本模型（滑点、冲击成本分层）。
- 增加多资产 / 多频率统一时钟支持。
- 提供标准化报告模板（JSON + HTML）。
