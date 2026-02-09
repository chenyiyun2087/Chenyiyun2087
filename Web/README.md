# Quant Strategy Dashboard

基于 Flask 的量化策略看板，用于展示：
1.  **Sina 实盘持仓** (Live Positions)
2.  **Sina 每日评分排行** (Daily Scores)
3.  **Eastmoney 超跌反弹策略结果** (Oversold Bounce)

## 环境准备
确保已安装 `flask`:
```bash
pip install flask pymysql
```

## 运行看板
```bash
python Web/app.py
```
默认访问地址: [http://localhost:5001](http://localhost:5001)

## 数据更新
看板展示的是数据库中的静态数据，请确保运行以下脚本以更新数据：

1.  **更新 Sina 评分**:
    ```bash
    python ScoreRank/run_daily.py
    ```
2.  **更新 Eastmoney 策略结果**:
    ```bash
    python Eastmoney/run_strategy.py
    ```

## 目录结构
*   `app.py`: Flask 主程序
*   `templates/`: HTML 模板
*   `static/`: CSS/JS 静态文件
