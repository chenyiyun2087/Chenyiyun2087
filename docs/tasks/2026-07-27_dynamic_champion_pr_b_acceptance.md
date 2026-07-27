# 动态评分冠军 PR-B 验收记录

日期：2026-07-27  
范围：Formal Readiness、PIT 覆盖和权威交易日历

## 结论

- 工程状态：`PASS`
- Formal Run 准备状态：`BLOCKED`
- 唯一成功状态：`READY_FOR_FORMAL_RUN`
- 动态冠军资金状态：`NO_GO / 0 元`

当前环境没有 2013 年至最新完整交易日的冻结 PIT 输入包，因此预检没有
启动正式回测。缺失数据不会被空表、历史回填或生命周期推导日历替代。

## 已完成

- 新增 `scripts/research/formal_readiness_preflight.py`。
- 固定五策略共同日期检查。
- 评分覆盖分母改为每日 PIT 可交易证券数，逐日、逐策略均要求不低于 98%。
- 权威交易日历唯一允许使用 `tushare_stock.dim_trade_cal` 的 SSE 日历。
- 检查输入对象、字段、主键重复、PIT 可见时间、文件 SHA、日期范围、
  公司行动、证券生命周期和 CNY 初始账户。
- 新增 Python 3.11 本地可复现 CI 降级证明；远端 Actions 计费阻塞不会
  被冒充为通过。

## 证据

- [Formal Readiness 结果](../../exports/formal_readiness/20260727_pr_b/formal_readiness_preflight.json)
- [本地 CI 证明](../../exports/local_ci/pr_b_fd166367.json)

## 测试

- 快速门禁：76 项通过。
- PR-B 定向测试：21 项通过。
- Python：3.11.15。
- 凭据检查：通过。
