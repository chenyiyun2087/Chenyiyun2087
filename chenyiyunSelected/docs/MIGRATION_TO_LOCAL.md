# chenyiyun1.py 本地化迁移（A完成 + B完成）

## 1. 现状与字段确认

你已确认：

- `dwd_fina_indicator.mlev` 稳定
- `dwd_stock_label_daily.is_st/list_days` 稳定

因此本地适配按稳定字段实现，不再做主路径降级假设（仅保留容错）。

---

## 2. A 已完成：接入本地回测引擎（优先）

已接入仓库内 `backtest/src/backtest_engine`，完成“聚宽策略 -> 本地回测”主链路：

- **核心逻辑**：
  1. `strategy/local_strategy_adapter.py`: 聚宽接口的本地实现（选股/因子）。
  2. `backtest_engine/`: 通用回测框架（资金/持仓/撮合）。
  3. `strategy/run_local_backtest.py`: 策略运行脚本（组合上述两者）。

### 3.2 执行命令

**运行本地回测（Phase-A）**:

```bash
python chenyiyunSelected/strategy/run_local_backtest.py \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --database tushare_stock
```

**运行每日选股（Phase-B，生产环境）**:

```bash
python chenyiyunSelected/strategy/local_strategy_adapter.py \
  --date 2026-02-18 \
  --database tushare_stock \
  --emit-signals
```

---

## 4. 下一步：开始回测（你要求的顺序已满足）

你要求“先 A 再 B，之后回测”，当前已具备回测入口，可直接开始参数化回测（建议先 1 年窗口，再滚动）。

建议回测维度：

1. 调仓日：周一（当前实现） vs 周二（规避周一效应）
2. 持仓数：6/8/10/12
3. 滑点：10/20/30 bps
4. 手续费：万3 vs 万5
5. 约束：加入 ST/次新/涨跌停不可交易模拟

