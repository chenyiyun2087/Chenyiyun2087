# Eastmoney 多空看盘扫描

## 功能
- 扫描东方财富股吧多空看盘比例（看涨/看跌）。
- 可选解析股票最新价与涨跌幅（页面结构变化时可能为空）。
- 将结果批量写入 MySQL。

## 配置
在 `easymoney/config` 下准备配置文件，例如 `config_1.json`：

```json
{
  "excel_file": "stock_codes.xlsx",
  "mysql": {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "chenyiyun"
  }
}
```

Excel 文件需包含 `stock_code` 列（与 `Sina/config` 方式一致）。

## DDL
表结构见 `easymoney/ddl.sql`。程序会自动建库建表。

## 使用方式
### 单只扫描
```bash
python -m easymoney.duokong_scanner 688158
```

### 批量扫描入库
```bash
python -m easymoney.duokong_batch config_1
```

可覆盖 MySQL 参数：
```bash
python -m easymoney.duokong_batch config_1 \
  --mysql-host localhost \
  --mysql-port 3306 \
  --mysql-user root \
  --mysql-password 123456 \
  --mysql-db chenyiyun
```

## 测试建议
1. 准备 `easymoney/config/stock_codes.xlsx`（包含 `stock_code` 列）。
2. 保证 MySQL 可连接。
3. 运行批量扫描命令，检查数据库表 `eastmoney_duokong_results` 是否有记录。
