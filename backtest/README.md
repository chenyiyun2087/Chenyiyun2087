# 轻量级回测框架（MVP）

本项目提供一个可快速落地的、事件驱动的轻量级回测框架，包含：

- 统一数据喂入接口（`DataFeed`）
- 策略接口（`Strategy`）
- 回测主循环（`Engine`）
- 撮合器（`Broker`）
- 账户与持仓（`Portfolio`）
- 指标计算（收益、夏普、最大回撤、换手）
- 标准化结果导出 JSON（供后台直接渲染）

## 目录结构

```text
backtest/
  README.md
  pyproject.toml
  backtest_engine/
    __init__.py
    config.py
    datafeed/
      __init__.py
      base.py
      warehouse_feed.py
      mock_feed.py
    core/
      __init__.py
      clock.py
      broker.py
      portfolio.py
      engine.py
      strategy.py
      types.py
    metrics/
      __init__.py
      performance.py
    reporting/
      __init__.py
      schema.py
      exporter.py
    examples/
      demo_strategy.py
      run_demo.py
    tests/
      test_metrics.py
      test_engine_smoke.py
```

## 快速开始

```bash
cd backtest
python -m backtest_engine.examples.run_demo
```

执行后将产出：

- `backtest/results/demo_result.json`

## 测试

```bash
cd backtest
pytest
```
