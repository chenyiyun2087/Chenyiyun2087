# 动态评分冠军 PR-A 验收记录

日期：2026-07-27  
范围：Release 身份、经济等价性、CNY 与统一风控配置

## 结论

- 工程状态：`PASS`
- 经济等价证明：`BLOCKED`
- 动态冠军资金状态：`NO_GO / 0 元`
- Broker API：关闭
- 生产路由：未改变

PR-A 保留生产 Release `prod-fixed-v2-20260720-01` 及其原始经济身份
SHA `4a4a961f46e6141aa059681c510f874b2d14dbfe8ef49deb9baac5feef5d44c3`。
当前物化运行配置使用独立 SHA
`7d62eeaf31160365d11681e9f0fb91c4877c3b753f1456180aea8b6514c89540`；
二者只能通过经济等价证明关联，不能用单元测试或人工推断替代。
原始冻结清单自身完整性继续为 `PASS`；当前检出与原始冻结文件不同，
所以当前 Release 身份校验为 `BLOCKED`，与证明结论一致。

## 已完成

- 建立 `ea535ebd → c37b2eb7 → aaea8a95` 提交证明链。
- `config/production_acceptance.yaml` 成为 CNY、15/30/40/45 风控及准入阈值的唯一事实源。
- `production_strategy.yaml` 只保留对中心风控配置的引用；旧运行时字典接口继续返回物化值。
- 报告金额字段改为显式“人民币元”数值，不再使用会被站点本地化为美元的通用 currency 类型。
- 新增双层回放和三轮 Web p95 的失败关闭证明工具。

## 当前阻塞

以下原始证据没有在当前环境中提供，因此
`economic_equivalence_attestation.json` 正确保持 `BLOCKED`：

- 615 个冻结交易日的基线与候选代码同输入回放；
- 最近 10 个完整生产交易日的同输入回放；
- 每个核心端点 3 轮、每轮 20 次的预热后 Web 基线与候选样本。

证据文件：
[economic_equivalence_attestation.json](../../exports/economic_equivalence/20260727_pr_a/economic_equivalence_attestation.json)

## 测试

- PR-A 定向与生产配置兼容测试：82 项通过，0 失败。
- 证明工具表征测试覆盖：完整等价通过、缺失证据阻塞、CNY 与 15/30/40/45 物化。
