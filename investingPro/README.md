# investingPro 模块说明

`investingPro/` 负责处理 InvestingPro 导出数据，完成**预处理、解析、入库**流程。

## 组件回顾

- `InvestingProExcelPreprocess.py`：Excel 预处理、字段规范化。
- `InvestingProExcelParser.py`：结构化解析与数据提取。
- `InvestingProExcelProcessor.py`：流程协调与批量处理。
- `InvestingProToDB.py`：入库逻辑与数据库交互。

## 使用建议

1. 先运行预处理脚本，统一原始文件格式。  
2. 再运行解析与处理流程。  
3. 最后执行入库并做数据校验。

## 可改进点（待统一实施）

- 增加字段映射配置文件，避免硬编码列名。
- 增加导入前后行数校验与重复检测。
- 输出统一质量报告（缺失值、异常值、重复键）。
