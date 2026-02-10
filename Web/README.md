# Quant Strategy Dashboard

基于 Flask 的量化策略看板，集成多维度数据展示与后台管理功能。

## 功能特性 (v2)

### 1. 数据看板
*   **Sina 实盘持仓** (Live Positions):
    *   展示持仓明细：买入价格、当前数量、持仓成本、浮动盈亏、收益率。
    *   自动计算市值与盈亏。
*   **Eastmoney 策略** (Oversold Bounce):
    *   展示超跌反弹策略筛选结果。
    *   **附带评分公式说明**：详细解释综合得分的计算逻辑。
*   **Sina 每日评分** (Daily Scores):
    *   展示每日个股评分排行 (Top 20)。
    *   **附带评分公式说明**：解释基础分、趋势分、量能分等构成。

### 2. 后台管理 (Admin)
*   **任务调度**:
    *   手动触发 Sina / Eastmoney 定时任务。
    *   查看任务执行状态、上次执行时间。
*   **手动补单**:
    *   支持手动录入或修正 Sina 实盘持仓数据 (买入操作)。

## 环境准备
确保已安装 `flask`:
```bash
pip install -r Web/requirements.txt
```

## 运行看板
```bash
python Web/app.py
```
默认访问地址: [http://localhost:5001](http://localhost:5001)

## 目录结构
*   `app.py`: Flask 主程序 (包含路由、分页、数据库逻辑)
*   `templates/`:
    *   `layout.html`: 基础布局 (侧边栏导航)
    *   `positions.html`: 持仓页面
    *   `eastmoney.html`: 策略页面
    *   `scores.html`: 评分页面
    *   `admin.html`: 管理页面
*   `static/`: CSS/JS 静态文件
