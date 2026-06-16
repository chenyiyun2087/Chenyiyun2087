# 2026-06-16 可信策略收益评估批量任务

## 目标

- 每个交易日自动生成当前生产策略收益评估。
- 在候选订单推送和影子盘监控之后，单独向飞书推送策略收益详情。
- 输出审计文件到 `exports/production_strategy_reviews/`，不新增数据库表，不重跑长耗时回测。

## 入口

- 调度器：`scheduler.py` 的 `daily_pipeline`。
- Web 任务中心：`trusted_strategy_performance_review`。
- 命令行：`python3 scripts/ops/run_strategy_performance_review.py --date <YYYYMMDD> --notify-feishu`。

## 验收

- 生成 `strategy_performance_review.json`、`strategy_performance_review.md`、`strategy_performance_review_feishu.txt`。
- 飞书消息包含主策略收益/回撤、候选订单、影子盘、实盘同步状态和运行判断。
- Web 任务校验能识别当次生成的 JSON/Markdown 文件。
