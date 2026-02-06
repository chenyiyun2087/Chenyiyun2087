# Eastmoney 多空扫描使用说明

## 1) 环境准备
1. 安装依赖（至少需要 `selenium`；若使用 `.xlsx` 则还需 `pandas`；入库需 `pymysql`）。
2. 准备浏览器驱动（ChromeDriver）并确保可被 Selenium 调用。
3. 准备配置文件：`Eastmoney/config/config_1.json`。
4. 股票列表文件：默认使用 **文本文件** `Eastmoney/config/stock_codes.txt`（每行一个股票代码，避免二进制文件问题）。

## 2) 配置示例（推荐文本文件）
`config_1.json`：
```json
{
  "stock_codes_file": "stock_codes.txt",
  "max_workers": 4,
  "mysql": {"host": "localhost", "port": 3306, "user": "root", "password": "***", "database": "chenyiyun", "charset": "utf8mb4", "autocommit": true}
}
```

> 兼容：仍支持历史字段 `excel_file`，且可读取 `.xlsx`/`.csv`/`.txt`。

## 3) 单只股票检测
推荐：
```bash
python -m Eastmoney.main config_1 20260205 --stock 688158 --max-workers 1
```
兼容旧方式：
```bash
python Eastmoney/main.py config_1.json 20260205 --stock 688158 --max-workers 1
```

## 4) 多只股票检测（命令行指定）
```bash
python -m Eastmoney.main config_1 20260205 --stock-codes 688158 600000 000001 --max-workers 3
```

## 5) 批量检测（使用配置文件中的股票清单）
```bash
python -m Eastmoney.main config_1 20260205
```

> 说明：脚本会把结果 upsert 到 `em_duokong_sentiment`，可重复运行。
