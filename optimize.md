# M7 调仓卖出策略优化方案（合并版）

更新时间：2026-02-27

实现状态标记（2026-03-02）：
- ✅ 已实现
- ⚠️ 部分实现（已上线主链路，但与方案细节有差异）
- ⏳ 未实现/待验证

## 1. 目标与范围

本方案仅针对 `M7` 的卖出链路优化，目标是：

- 减少误触发和过度交易；
- 提升“盈利保护”和“资金周转效率”；
- 保持 Web 端可配置、可审计、可回滚。

涉及模块：

- `web/strategy_playbook.py`（卖出规则引擎）
- `web/app.py`（参数接收与数据准备）
- `web/templates/sina_strategy_m7.html`（参数面板）
- `m7_sell_signals`（卖出信号落库增强）

## 2. 当前卖出逻辑（现状基线）

在当前的 `evaluate_m7_rebalance` 函数中，关于“卖出(Sell)”或“减仓”的处理机制主要分为两大类：强制清仓（Forced Exit）与常规权重下调（Rebalance Sell）。系统遍历所有目标标的与现有持仓标的，针对每一只股票进行卖出评估。

### 2.1 强制清仓（Forced Exit）

一旦触发以下任意条件，系统将无视该股票在 M4 系统中的目标权重，直接将其目标权重（`target_weight`）强制设定为 0，并下达卖出当前所有持股数量（`shares_delta = -current_shares`）的指令。

- `B/S 反转卖出`
  - 判定方式：通过 `_fetch_latest_bs_signal_state` 比对最近买入/卖出信号日期。
  - 判定条件：若 `latest_sell_date >= latest_buy_date`（或只有卖出无买入），判定上涨趋势破坏。
  - 执行动作：生成强制卖出，原因 `B/S反转卖出`。
- `硬止损（Hard Stop Loss）`
  - 判定方式：比较 `current_price` 相对 `avg_cost` 的跌幅。
  - 判定条件：`current_price <= avg_cost * (1 - stop_loss_pct)`（默认 `stop_loss_pct = 0.07`）。
  - 执行动作：生成强制卖出，原因 `硬止损(7.0%)`。

### 2.2 常规权重下调（Rebalance Sell）

若未触发强制清仓，则进入目标权重再平衡：

- 判定方式：比较目标权重 `target_weight` 与当前权重 `current_weight`。
- 计算差值：`delta_w = target_weight - current_weight`。
- 判定条件：
  - `delta_w < 0`（目标权重低于当前权重）；
  - 且 `abs(delta_w) >= min_trade_weight`（默认 1%）。
- 执行动作：按应调金额折算股数后减仓，原因 `目标权重下调`。

## 3. 现状问题

1. `B/S反转` 缺少“新鲜度门禁”，历史旧信号可能长期触发。
2. 普通调仓股数在现有实现中可能与“向下取整”约定不一致，存在过买/过卖风险。
3. 尚无“移动止盈”“时间止损”“评分恶化快速斩仓”。
4. 持仓数据未统一使用 `entry_date`，时间相关规则缺少稳定起点。
5. 卖出原因虽有文本，但结构化审计字段不足。

状态回看（2026-03-02）：
- ✅ 问题1已解决：已加 `B/S` 新鲜度门禁（`bs_fresh_trade_days`）。
- ⚠️ 问题2部分解决：普通卖出“向上取整”已实现；但买入仍是 `round`，非“向下取整”。
- ✅ 问题3已解决：已上线 `TRAILING_STOP / TIME_STOP / SCORE_EXIT`。
- ✅ 问题4已解决：已统一接入 `entry_date` 并结合交易日历计算 `holding_trade_days`。
- ✅ 问题5已解决：`m7_sell_signals` 已补充结构化审计字段（`reason_code/reason_detail_json/rule_version` 等）。

## 4. 优化总原则

- 原则1：强制卖出优先于权重调仓；
- 原则2：同一标的当日只生成一条主卖出原因（避免冲突）；
- 原则3：交易阈值采用“权重 + 金额”双门禁；
- 原则4：默认参数保守，先降低误杀，再逐步激进。

## 5. 卖出规则引擎（优先级） ✅

对每个持仓标的按以下顺序评估，命中即停止后续判断：

1. `BS_REVERSAL`：B/S 反转（含新鲜度门禁）
2. `HARD_STOP`：硬止损
3. `TRAILING_STOP`：移动止盈
4. `TIME_STOP`：时间止损
5. `SCORE_EXIT`：评分恶化快速斩仓
6. `REBALANCE_SELL`：目标权重下调

补充：当前实现额外包含 `LIMIT_DOWN_EXIT`（跌停/停牌挂起与重试）。

执行动作：

- `BS_REVERSAL/HARD_STOP/TRAILING_STOP/TIME_STOP/SCORE_EXIT` -> 强制清仓（`target_weight=0`）
- `REBALANCE_SELL` -> 按目标权重减仓

## 6. 拟优化算法与逻辑

### 6.1 B/S 反转（增强：新鲜度门禁） ✅

- 触发条件：`latest_sell_date >= latest_buy_date`
- 新鲜度门禁：`today - latest_sell_date <= bs_fresh_days`
- 目的：避免历史旧卖点持续触发。

### 6.2 硬止损（参数化） ✅

- 条件：`current_price <= avg_cost * (1 - stop_loss_pct)`
- 说明：`stop_loss_pct` 从固定值改为 Web 可配置参数。

### 6.3 移动止盈（TRAILING_STOP） ✅

- 状态提取：获取 `entry_date` 以来区间最高价 `highest_since_entry`。
- 激活条件：`(highest_since_entry / avg_cost - 1) >= trail_activate_pct`
- 触发条件：`(highest_since_entry - current_price) / highest_since_entry >= trail_drawdown_pct`
- 动作：强制清仓，原因 `移动止损卖出`（实现文案）。

### 6.4 时间止损（TIME_STOP） ✅

- 持仓天数：按交易日计数（`chenyiyun.dim_trade_cal`）。
- 触发条件：
  - `holding_trade_days >= time_stop_days`
  - 且 `current_return_pct < time_stop_min_return_pct`
- 动作：强制清仓，原因 `时间止损卖出`（实现文案）。

### 6.5 评分恶化快速斩仓（SCORE_EXIT） ✅

- 数据：持仓股当日评分（`score_rank_daily` + M4 上下文）
- 触发条件（建议双阈值）：
  - `claude_score < claude_floor` 或
  - `m4_score < score_floor`
- 动作：强制清仓，原因 `评分退场卖出`（实现文案）。

## 7. 交易执行细节修正

### 7.1 股数取整策略 ⚠️

- 买入：向下取整到 100 股；
- 卖出：向上取整到 100 股，但不得超过当前持股；
- 强制卖出：直接清仓，不做 100 股约束。

实现状态：
- ✅ 普通卖出向上取整到 100 股（且不超过持仓）已实现；
- ✅ 强制卖出直接清仓已实现；
- ⚠️ 买入当前为 `round(.../100)*100`，并非严格“向下取整”。

### 7.2 双门槛过滤 ⚠️

- `min_trade_weight`：权重差门槛；
- `min_trade_notional`：最小交易金额门槛；
- 普通调仓需同时满足两者；强制卖出不受限制。

实现状态：
- ✅ 普通卖出同时校验 `min_trade_weight + min_trade_notional`；
- ✅ 强制卖出不受双门槛限制；
- ⚠️ 普通买入未使用 `min_trade_notional`（仅权重门槛 + 基础金额约束）。

## 8. 涉及修改文件 ✅

- `web/strategy_playbook.py`
  - 修改 `evaluate_m7_rebalance`：引入 Rule Engine 与新增参数；补充强制卖出判定链路。
- `web/app.py`
  - 在 `@app.route('/sina/strategy/m7')` 增加参数解析与透传；准备历史辅助数据（如区间最高价、持仓天数）。
- `web/templates/sina_strategy_m7.html`
  - 在策略控制面板增加高级参数输入项。

## 9. Web 参数面板新增项 ⚠️

- ✅ `bs_fresh_days`（实现名：`bs_fresh_trade_days`）
- ⏳ `enable_trailing_stop`（未单独开关，当前通过参数直接生效）
- ✅ `trail_activate_pct`
- ✅ `trail_drawdown_pct`
- ⏳ `enable_time_stop`（未单独开关，当前通过参数直接生效）
- ✅ `time_stop_days`
- ✅ `time_stop_min_return_pct`
- ⏳ `enable_score_exit`（未单独开关，当前通过参数直接生效）
- ✅ `claude_floor`
- ✅ `score_floor`
- ✅ `min_trade_notional`

## 10. 默认参数建议（首版） ⚠️（与当前实现默认值存在差异）

- ⚠️ `stop_loss_pct = 7`（当前默认 `6.0`）
- ⚠️ `bs_fresh_days = 5`（当前默认 `3`）
- ⚠️ `trail_activate_pct = 10`（当前默认 `12.0`）
- ⚠️ `trail_drawdown_pct = 5`（当前默认 `4.0`）
- ⚠️ `time_stop_days = 10`（当前默认 `8`）
- ⚠️ `time_stop_min_return_pct = 2`（当前默认 `1.0`）
- ⚠️ `claude_floor = 40`（当前默认 `45.0`）
- ✅ `score_floor = 60`（当前默认一致）
- ✅ `min_trade_weight = 1.0`（当前默认一致）
- ⚠️ `min_trade_notional = 1000`（当前默认 `5000.0`）

## 11. 落库与审计增强 ✅

扩展 `m7_sell_signals`（兼容老字段）：

- `reason_code`（`BS_REVERSAL/HARD_STOP/TRAILING_STOP/TIME_STOP/SCORE_EXIT/REBALANCE`）
- `reason_detail_json`（阈值、触发值、持仓天数、最高价等）
- `rule_version`（如 `m7_sell_v2`）

补充：当前还已扩展 `pending_flag/pending_reason/exec_status/protect_window_hit/market_risk_gate_hit`。

## 12. 实施步骤（两阶段） ⚠️

### 阶段一（最小可用） ⚠️（主体完成，细节有差异）

1. 在 `evaluate_m7_rebalance` 引入统一 Rule Engine；
2. 上线 `B/S新鲜度 + 硬止损 + 双门槛 + 取整修正`；
3. Web 增加对应参数并透传；
4. 补充日志和返回字段，便于验证。

状态：
- ✅ 1 已实现
- ⚠️ 2 部分实现（“买入向下取整”未完全一致）
- ✅ 3 已实现
- ✅ 4 已实现

### 阶段二（增强） ⚠️（已实现核心能力）

1. 接入 `entry_date` 与持仓区间最高价查询；
2. 上线 `移动止盈 + 时间止损 + 评分斩仓`；
3. 升级卖出信号落库结构化字段；
4. 做参数回测与阈值微调。

状态：
- ✅ 1 已实现
- ✅ 2 已实现
- ✅ 3 已实现
- ⏳ 4 待独立回测与参数固化记录（文档未见回测结论沉淀）

## 13. 回滚策略 ✅

- 通过 `rule_version` 或开关参数回退到 `v1`（仅 B/S 反转 + 硬止损 + 权重调仓）；
- 新增字段保持向后兼容，不删除旧字段。

## 14. 验收指标 ⏳

上线后按周评估：增加如下指标的统计功能
- 强制卖出占比（避免异常飙升）；
- 平均持仓天数变化；
- 单周换手率变化；
- 卖出后 3/5/10 日表现是否改善；
- 误杀率（卖出后短期快速反弹比例）。

## 15. 待确认决策

1. 强制卖出是否一律全清仓，还是分级减仓（如先卖 50%）？
答：坚决全清仓。 A 股市场“君子不立危墙之下”。既然触发了硬止损或趋势反转，说明原始买入逻辑已遭到破坏，分级减仓只会拉长痛苦周期，降低资金周转效率。
2. 评分斩仓使用单阈值（Claude）还是双阈值（Claude + M4）？
答：必须双阈值（Claude + M4）。 LLM（Claude）对舆情的解读有时会反应过度（幻觉或情绪放大）。只有当技术面（M4 score）和基本面/情绪面（Claude）同时恶化时才执行斩仓，能最大程度避免被 AI “误杀”。
3. 阶段一/阶段二是否按上述顺序分批上线？
答：是的，坚决分批。 阶段一的“新鲜度 + 硬止损 + 门槛防磨损” 是救命的底线，必须最快速度上线。阶段二的“移动止盈与时间止损” 属于锦上添花，需要一定的参数回测来调优，避免上线后因参数不当导致乱卖飞。
