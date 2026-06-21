# 策略治理闭环实施记录

## 已落地

- 新增不可变运行 Release、订单意图、执行事件、每日对账与晋级证据的 MySQL 权威表；证据包写入 `exports/strategy_governance/<release_id>/` 并附 SHA256 清单。
- 严格账本 Gate 按 `production_acceptance.yaml` 的全部硬阈值逐项执行；缺失 Release、数据、指标或证据均为 `BLOCKED`。
- Shadow 晋级按 `strategy_id + release_id + execution_date` 隔离，并要求阶段匹配且绑定 Gate 证据哈希的人工审批。
- runtime 账本定义受控订单状态机、四类每日对账和研究账本兼容适配器；旧账本暂保留供双跑回放。
- 受控候选订单写入必须引用已冻结且与策略、配置、执行日一致的 Release，不再回退到 config SHA。

## 运行约束

当前研究 Shadow 与 Canary 仍关闭，系统只可输出需人工确认的订单包；不存在任何券商 API 自动下单路径。

## 后续运行验收

在真实数据开始双跑后，连续基准期逐日比较旧研究账本与 runtime 账本的现金、持仓、订单状态、公司行为及 NAV；差异未归因前不得切换唯一账本内核。
