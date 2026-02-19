# web 看板模块说明

`web/` 是项目统一可视化入口，基于 Flask 提供策略结果查看与基础运维页面。

## 页面与功能回顾

- `dashboard.html`：总体看板
- `positions.html`：**Sina策略实时持仓**（`/positions`）
- `sina_monitor.html`：**B/S监控**（`/sina/monitor`，含 Chart.js 信号统计）
- `chenyiyun_selected.html`：**陈依云精选**（`/chenyiyun/selected`）
- `backtest_results.html`：回测结果中心（`/backtest/results`）
- `tech_score.html`：**技术评分**（`/sina/tech_score`）
  - 支持按“股票池 + 多个代码”筛选；
  - 集成 Claude 评分与技术指标分。
- `stock_pool.html`：**股票池管理**（`/stock_pool`）
  - 批量导入、分页查询、只读池保护。
- `admin.html`：系统任务管理（支持 M8 按池执行）。
- `sina_strategy_*.html`：策略详情页。

## 运行

```bash
pip install -r web/requirements.txt
python web/app.py
```

默认地址：`http://localhost:5001`
