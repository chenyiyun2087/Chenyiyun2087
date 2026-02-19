# chenyiyunSelected 模块说明

`chenyiyunSelected/` 用于承接“聚宽策略本地化迁移”，并连接回测与研究工具链。

## 组件回顾

- `strategy/`
  - `chenyiyun1.py`：原始策略实现。
  - `local_strategy_adapter.py`：本地数据仓适配与信号生成。
  - `run_local_backtest.py`：本地回测入口。
- `research/`
  - `StockScores.py`：评分与报告。
  - 其他研究脚本：用于输入清洗、Prompt 生成、可视化。
- `docs/`
  - 迁移记录、评估文档、说明文档。

## 推荐命令

```bash
python chenyiyunSelected/strategy/local_strategy_adapter.py --help
python chenyiyunSelected/strategy/run_local_backtest.py --help
```

## 可改进点（待统一实施）

- `strategy` 与 `research` 的输入输出协议文档化。
- 回测结果与信号落库共用统一 schema。
- 为迁移策略补充最小回归样例数据。
