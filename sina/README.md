# sina 模块说明

`sina/` 负责 **B/S 信号检测 + 实盘跟踪 + 模块内回测**，是仓库里最贴近“交易信号生产与落地”的子系统。

## 组件回顾

- `bs_detection/`
  - 图像抓取与 B/S 点识别。
  - 主要入口：`python -m sina.bs_detection.main`
- `live_tracker/`
  - 持仓状态维护、账户快照、日报输出。
  - 主要入口：`python -m sina.live_tracker.run_live_tracker`
- `backtest/`
  - Sina 场景下的回测执行器、评分器与结果导出。
  - 主要入口：`python -m sina.backtest.run_backtest`
- `schemas/`
  - MySQL 表结构文件（检测与跟踪相关）。
- `config/`
  - 股票池与策略配置 JSON / Excel。

## 推荐使用流程

1. 先跑 `bs_detection` 生成候选信号。  
2. 再由 `live_tracker` 同步持仓与价格。  
3. 用 `backtest` 做历史验证，比较参数方案。

## 可改进点（待统一实施）

- 配置项来源统一（环境变量 + config 文件分层）。
- `bs_detection` 与 `live_tracker` 的日志字段统一（symbol / trade_date / run_id）。
- 回测输出目录规范化，避免结果散落。
