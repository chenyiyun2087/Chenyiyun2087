# chenyiyun1.py 本地化迁移（A完成 + B完成）

## 1. 现状与字段确认

你已确认：

- `dwd_fina_indicator.mlev` 稳定
- `dwd_stock_label_daily.is_st/list_days` 稳定

因此本地适配按稳定字段实现，不再做主路径降级假设（仅保留容错）。

---

## 2. A 已完成：接入本地回测引擎（优先）

已接入仓库内 `backtest/src/backtest_engine`，完成“聚宽策略 -> 本地回测”主链路：

1. `chenyiyunSelected/local_strategy_adapter.py`
   - 从 `tushare_stock` 读取因子并生成每周选股候选（高股息 -> 高波动 -> 低杠杆 -> 小市值）。
2. `backtest/src/backtest_engine/datafeed/tushare_feed.py`
   - 新增 MySQL 日频数据喂入 `TushareDailyFeed`，读取 `dwd_stock_daily_standard`（失败时尝试 `dwd_daily`）。
3. `backtest/src/backtest_engine/strategies/high_dividend_local.py`
   - 新增本地执行策略：每周一按目标池等权调仓（非目标卖出、空仓目标买入）。
4. `chenyiyunSelected/run_local_backtest.py`
   - 新增回测入口：自动构建“每周目标池计划”并运行回测，最终导出 JSON 报告。

### A 运行方式

```bash
python chenyiyunSelected/run_local_backtest.py \
  --start 2025-01-01 \
  --end 2025-12-31 \
  --host 127.0.0.1 --port 3306 --user root --password '***' \
  --database tushare_stock \
  --output backtest/results/chenyiyun_local_result.json
```

---

## 3. B 已完成：先做离线信号服务（只出信号不下单）

在 `local_strategy_adapter.py` 增加：

- `build_daily_signals()`：基于当日 topN 候选生成 `BUY` 信号与等权目标仓位。
- `save_daily_signals()`：写入 ADS 表（默认 `ads_local_strategy_signals`，自动建表与 upsert）。
- CLI 参数：
  - `--emit-signals`
  - `--signal-table ads_local_strategy_signals`

### B 运行方式

```bash
python chenyiyunSelected/local_strategy_adapter.py \
  --date 2026-02-17 \
  --host 127.0.0.1 --port 3306 --user root --password '***' \
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

