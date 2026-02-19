# web 看板模块说明

`web/` 是项目统一可视化入口，基于 Flask 提供策略结果查看与基础运维页面。

## 页面与功能回顾

- `dashboard.html`：总体看板
- `positions.html`：实盘持仓视图
- `eastmoney.html`：Eastmoney 策略结果
- `scores.html`：Sina/ScoreRank 评分结果
- `admin.html`：任务触发、状态查看、人工操作入口
- `stock_pool.html`：股票池管理（新增、筛选、删除）
- `sina_strategy_*.html`：M2~M7 等策略页面

## 运行

```bash
pip install -r web/requirements.txt
python web/app.py
```

默认地址：`http://localhost:5001`

## 可改进点（待统一实施）

- 页面公共表格组件抽象，减少模板重复。
- 后端查询分页与过滤统一封装。
- 增加健康页（`/healthz`）与关键依赖状态展示。


- 新增页面：`/chenyiyun/selected`（陈依云精选策略），展示每日信号与当前持仓。

- 新增页面：`/stock_pool`（股票池管理），支持按池类型/状态过滤并维护自选池。

- 股票池管理规则：初始池为“自选股池（可手动增删）”和“最近有买点股票池（只读，由定时任务同步）”；支持新增股票池、修改股票池名称、管理池内股票。
