# 动态评分冠军 PR-C 验收记录

日期：2026-07-27  
范围：不可变 Formal Runner、权威日历与双账本验收

## 结论

- 工程状态：`PASS`
- Formal Run 状态：`BLOCKED`
- Formal Run 是否启动：否
- 严格账本状态：未运行，不能标记为 `VERIFIED`
- 动态冠军资金状态：`NO_GO / 0 元`

PR-B 预检没有达到 `READY_FOR_FORMAL_RUN`，因此 PR-C 编排器在创建
Formal Run ID、回测输出或账本结果之前失败关闭。

## 已完成

- Formal Run ID 由 PIT 证据 SHA 和 Git SHA 共同生成。
- Formal Run 目录只允许创建一次，禁止覆盖或复用。
- 固定五策略同窗；动态评分冠军是唯一准入候选，其余策略只作匹配比较。
- 固定 2013 年起点、7.5bp 单边成本、10bp 单边滑点和 50 万元初始账户。
- 正式模式强制读取 `tushare_stock.dim_trade_cal` 冻结 SSE 日历。
- 公司行动和证券生命周期使用严格快照及其独立清单。
- 回测成功后必须为五策略逐一运行主账本与独立账本；任一包缺失或不一致均
  得到 `LEDGER_BLOCKED`，不能降级为成功。
- Manifest 保留命令、Git SHA、预检 SHA、来源清单 SHA、成本和策略身份。

## 证据

- [Formal Run 预检查](../../exports/formal_runs/20260727_pr_c/formal_run_precheck.json)
- [本地 CI 证明](../../exports/local_ci/pr_c_2e362f90.json)

## 测试

- 快速门禁：76 项通过。
- PR-C、预检和严格账本定向测试：26 项通过。
- Python：3.11.15。
