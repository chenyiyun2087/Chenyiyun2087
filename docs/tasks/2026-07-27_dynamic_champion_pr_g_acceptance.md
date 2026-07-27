# 动态评分冠军 PR-G 验收记录

日期：2026-07-27  
范围：真实 Shadow 生命周期、证据身份与失败关闭

## 结论

- 工程状态：`PASS`
- 当前生命周期：`RESEARCH_BLOCKED`
- 技术Shadow真实交易日：0
- 经济Shadow真实交易日：0
- 当前允许新增风险资金：`0 元`

PR-C Formal Run尚未达到`VERIFIED`，因此系统不能进入Disabled Shadow计数。
没有使用历史回填、模拟日期或其他策略/Release的记录填充门禁。

## 状态机

`RESEARCH_BLOCKED → DISABLED_SHADOW → ECONOMIC_SHADOW → MANUAL_CANARY_ELIGIBLE`

- 进入技术阶段前必须绑定精确策略、Release和已验证Formal证据SHA。
- 技术阶段要求20个真实交易日、2次真实状态切换、30个恢复事件、
  5个事件日、正事件比例不低于55%，且执行代理缺失和新增硬阻塞均为0。
- 经济阶段额外要求60个真实交易日、30个闭环、成本后Alpha为正、
  对账错误为0、双账本VERIFIED且理论/执行偏差门禁通过。
- 错误身份、部分PIT、回填、模拟日期、周末或非权威开市日不计数。
- 即使达到`MANUAL_CANARY_ELIGIBLE`，系统仍不授权资金，只生成待人工审批包。

## 证据

- [空白真实日输入](../../exports/shadow_lifecycle/20260727_pr_g/daily_evidence.json)
- [当前生命周期状态](../../exports/shadow_lifecycle/20260727_pr_g/shadow_lifecycle_status.json)

## 测试

- PR-G、Validation V2与动态冠军相关测试：35项通过。
- Python：3.11.15。
