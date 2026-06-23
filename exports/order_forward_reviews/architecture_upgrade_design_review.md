# 架构升级方案 — 设计审查

**审查日期**: 2026-06-23  
**基于**: 当前代码库完整调研 + 用户架构升级方案  
**状态**: 待讨论确认后进入实现

---

## 一、现状与方案对照

### 1.1 调度入口冲突 — 已经比方案认为的更干净

方案提到"README 写 Web 是唯一入口，但生产文档仍把 scheduler.py 作为主入口"。实际调研结果：

| 文件 | 状态 | 任务数 |
|------|------|--------|
| `web/app.py` TASKS | **生产运行中** | 7个启用 / 19个定义 |
| `scheduler.py` TASKS | **已废弃，未启用** | 5个定义 |
| README.md | **明确写 scheduler.py 未启用** | — |

**结论**：调度入口冲突没有方案描述的严重。`scheduler.py` 已有清晰的"未启用"标记。但 `scheduler.py` 仍在仓库中且近期有人修改（crash recovery/disk health），容易造成混淆。

**建议**：直接删除 `scheduler.py` 或移入 `archive/`，而非再维护一套 pipeline.yaml 来替代它。Web TASKS 字典已经是事实上的 pipeline 定义。

> ❓ **确认**：是否同意将 `scheduler.py` 移入 `archive/scheduler.py`，只保留 `web/app.py` 作为唯一调度入口？

### 1.2 AShareDataCenter 接入 — 对接方式比方案想的更简单

方案建议建 `integration/legacy_direct_adapter.py` 包装现有直接导入。实际上现有对接路径是：

```
run_daily.py
  → from score.factor_optimizer.data_loader import load_category_scores  # 直接 sys.path hack
  → 回退: /Users/chenyiyun/PycharmProjects/AShareDataCenter

export_trusted_strategy_candidates.py
  → from scripts.research_trusted_strategy_account_backtest import _load_ashare_strategy_candidates
  → 通过 build_sqlalchemy_url() 读 AShare 的 MySQL 表（同实例）
```

实际上 AShareDataCenter 和 Chenyiyun2087 **已经在同一台 MySQL 上**，共享 `tushare_stock` 数据库。`run_daily.py` 的直接 Python 导入才是真正的耦合点——它通过硬编码路径导入 Python 模块。

**建议**：
- `integration/ashare_client.py` 只包装 **数据读取**（feature/factor/signal/risk_gate），不包装 Python 函数调用
- 把 `run_daily.py` 中 `from score.factor_optimizer...` 改为走 `integration/ashare_client.py` 
- `export_trusted_strategy_candidates.py` 中的 `_load_ashare_strategy_candidates` 也改为走同一接口

> ❓ **确认**：是否同意第一版 adapter 只做 SQL 查询封装（不引入 HTTP），等 AShareDataCenter 独立服务化后再切 HTTP？

### 1.3 策略身份 — 当前状态

当前策略身份分散在三处：
1. `config/production_strategy.yaml` — 主策略名、风控参数
2. `scripts/research_trusted_strategy_account_backtest.py` — 所有 AShare 策略的常量定义和版本映射
3. `scripts/ops/export_trusted_strategy_candidates.py` — `RISK_PROFILE_DEFAULTS`、`ORDER_DETAIL_CONFIGS` 硬编码多策略配置

订单表 v2 已支持的字段：`account_id`, `release_id`, `strategy`, `execution_date`, `ts_code`, `side` — 这是方案要求的唯一键。

**建议**：`strategy_cards/` 每张卡一个 YAML，第一版建这些：

| 文件 | 策略 | 当前状态 | 建议初始状态 |
|------|------|---------|------------|
| `baseline_full_liquidity_detail_vol_position.yaml` | 生产主策略 | 生产运行中 | PRODUCTION |
| `adaptive_market_style_shadow.yaml` | 影子风控锚 | 影子验证中 | SHADOW |
| `tiered_liquidity_then_bs_v2.yaml` | B点增强 | 影子验证中 | SHADOW |
| `ashare_auto_shadow.yaml` | ADC自动 | 影子观察 | SHADOW |
| `ashare_trend_breakout_shadow.yaml` | ADC趋势突破 | 影子观察 | SHADOW |
| `ashare_hybrid_conservative_shadow.yaml` | ADC保守融合 | 影子观察 | SHADOW |
| `dual_system_adaptive_route.yaml` | 双系统自适应 | 影子观察 | SHADOW |
| `chenyiyun_selected_legacy.yaml` | 旧JoinQuant策略 | 已停用 | LEGACY |

> ❓ **确认**：这8张策略卡清单是否正确？是否有遗漏的策略需要建卡？

### 1.4 快照表设计 — 与现有表的关系

方案提出新增四张快照表。需要明确它们与现有表的关系：

| 新表 | 对应现有数据 | 迁移策略 |
|------|------------|---------|
| `ads_research_snapshots` | 无对应 — 需要新建 | `run_daily.py` 每次跑完生成一条 |
| `ads_snapshot_signal_items` | `score_rank_daily` 的当日子集 | 从 score_rank_daily 投影，固化快照ID |
| `ads_snapshot_feature_explanations` | `score_rank_daily` 的因子列 | 从 score_rank_daily 投影 |
| `ads_snapshot_risk_gates` | 无对应 — 需要新建 | `run_daily.py` 产出 |

**关键问题**：`score_rank_daily` 是否继续保留？建议：
- 第一阶段：保留 `score_rank_daily` 作为"评分工作台"（run_daily 直接写入）
- 同时双写到快照表
- 第二阶段：候选导出只读快照表，`score_rank_daily` 降级为临时中间表
- 第三阶段：`score_rank_daily` 标记为兼容表，不再写入新字段

> ❓ **确认**：是否同意这个渐进式迁移路径？还是希望一步到位废除 score_rank_daily？

---

## 二、五层拆分 — 最大的工程挑战

### 2.1 当前 score_rank_daily 的列分类

| 层 | 现有字段 | 数量 |
|----|---------|------|
| Layer 1 事实层 | trade_date, symbol, name, industry, fund_pe_ttm, fund_pb, fund_roe, fund_netprofit_yoy, close_price, price_change_ratio, market_hs300_*, market_* | ~15列 |
| Layer 2 规则特征层 | score, base_score, s_trend, s_breakout, s_volume, s_rs, s_contraction, s_liquidity, s_bias, s_chip, s_bull_align, pattern_* | ~30列 |
| Layer 3 B/S事件层 | bs_score, bs_score_v2, bs_entry_score, bs_gate_score, bs_consensus_score, bs_model_prob, bs_model_*, bs_research_score, total_b_points, buy_point_close, buy_signal_description, sell_signal_description | ~20列 |
| Layer 4 LLM语义层 | claude_score, score_momentum, score_value, score_quality, score_technical, score_capital, score_chip | ~7列 |
| Layer 5 组合层 | opt_score, opt_momentum, opt_value, opt_quality, opt_technical, opt_capital, opt_chip, opt_size, pool_type, pool_type_shadow, is_bs_candidate | ~12列 |
| 其他 | id, created_at, event_seq_for_symbol, dynamic_trade_threshold, dynamic_watch_threshold, bs_threshold_*, is_self_selected, is_limit_up | ~15列 |

总共约 99 列。建议的拆分方式：

```
Layer 1 → tushare_stock（已存在，只读）
Layer 2 → chenyiyun.ads_rule_features（新建，从 score_rank_daily 迁移 s_* 列）
Layer 3 → chenyiyun.ads_bs_events（新建，从 score_rank_daily 迁移 bs_* 列）  
Layer 4 → chenyiyun.ads_llm_insights（新建，从 score_rank_daily 迁移 claude_* 列）
Layer 5 → chenyiyun.ads_signal_decisions（新建，signal_id + p_up_5d + decision 等）
```

> ❓ **确认**：
> - 是否接受 5 张新表（而非维持 score_rank_daily 宽表）？
> - 短期（第一阶段）是否先在 score_rank_daily 上加 `layer_1_snapshot_id` 等外键，而非立即拆表？
> - `claude_score` 方案说"保留做兼容展示但不进入排名公式"——确认？

---

## 三、M8 → Champion–Challenger — 需要明确与现有 M8 的关系

现有 M8 数据流：
```
run_m8_cycle.py → strategy_m8_runs (元信息) + strategy_m8_items (每策略窗口收益)
report_research_shadow_promotion_status.py → 读取影子盘状态 → 给出晋级建议
```

现有 `strategy_m8_items` 已有：avg_ret_3/5/10, hit_3/5/10, mdd, sharpe。

方案需要的额外指标（样本内外分离、Calmar、成本/滑点敏感性、行业集中度）需要新增计算。

**建议**：
- 第一阶段：在现有 `report_research_shadow_promotion_status.py` 基础上扩展，让它写入 `strategy_promotion_evidence` 表
- 不要重写 M8，而是在 M8 输出上增加 Champion–Challenger 评估层

> ❓ **确认**：M8 改造是"扩展现有脚本"还是"新建独立评估模块"？

---

## 四、订单账本 — 当前 v2 订单表已覆盖大部分需求

现有 `ads_local_strategy_orders` v2 schema 已有：
- `account_id`, `release_id`, `strategy`, `execution_date`, `ts_code`, `side`
- `order_status` (planned/submitted/partial/filled/cancelled/rejected/superseded/expired)
- `health_grade`, `health_substatus`, `config_sha`

分表建议：

| 方案建议表 | 现有对应 | 需要新建？ |
|-----------|---------|----------|
| ads_signal_decisions | 无 → 新建 | ✅ 需要 |
| ads_order_intents | ads_local_strategy_orders (status=planned) | ❌ 可复用，改 status 流转 |
| ads_pretrade_risk_checks | 无 → 新建 | ✅ 需要 |
| ads_broker_orders | 无 → 新建 | ✅ 需要 |
| ads_fills | 无，但 live_trades 有类似数据 | ❌ 可复用 |
| ads_position_lots | live_positions | ❌ 可复用 |
| ads_position_snapshots | live_daily_snapshots | ❌ 可复用 |
| ads_execution_feedback | 无 → 新建 | ✅ 需要 |
| ads_attribution_runs | 无 → 新建 | ✅ 需要 |

**建议**：第一阶段只新建 `ads_signal_decisions` + `ads_pretrade_risk_checks`，其余复用现有表并扩展字段。

> ❓ **确认**：是否接受"先复用现有表，逐步拆分"的渐进式路径？还是要求一次性建齐所有9张表？

---

## 五、chenyiyunSelected Legacy 处理

当前 chenyiyunSelected 有4个独立 ops 脚本，均标记为禁用但仍然存在：
- `run_chenyiyun_signal_check.py`
- `run_chenyiyun_weekly_rebalance.py`
- `run_chenyiyun_limitup_check.py`
- `run_chenyiyun_position_update.py`

它们写入 `ads_local_strategy_orders`、`ads_chenyiyun_selected_signals`，与生产订单共享同一张表。

**建议**：
1. 创建 `strategy_cards/chenyiyun_selected_legacy.yaml`，状态=LEGACY
2. 在订单写入处加入策略状态检查：LEGACY 状态拒绝写入
3. 四个 ops 脚本移入 `archive/`
4. `ads_chenyiyun_selected_signals` 表保留但标记为历史只读

> ❓ **确认**：是否同意将 chenyiyunSelected 整条链路归档？

---

## 六、迁移顺序建议（修正版）

基于实际代码库状态，建议调整为：

### 第一阶段（不改交易结果，建基础设施）

| 步骤 | 内容 | 预计改动 |
|------|------|---------|
| 1a | 删除 scheduler.py → archive/ | 0行新代码 |
| 1b | 创建 `integration/ashare_client.py`（SQL封装版） | ~80行 |
| 1c | 创建 `strategy_cards/` 8张卡 | 8个YAML文件 |
| 1d | 创建 `strategy_registry.py`（读卡、状态检查、门禁） | ~120行 |
| 1e | 新建 `ads_research_snapshots` + `ads_snapshot_signal_items` | 2张表 + 双写逻辑 |
| 1f | 清理文档冲突（删除 RUNBOOK 中的旧策略描述，生成 CURRENT_PRODUCTION_STATE.md） | 文档改动 |
| 1g | chenyiyunSelected 归档 → archive/ | 移4个文件 |
| **验收** | 100% 新候选有 snapshot_id；新订单有 strategy_version+release_id；LEGACY 策略无法生成订单 | — |

### 第二阶段（重构评分，不改变订单输出）

| 步骤 | 内容 |
|------|------|
| 2a | 新建 Layer 2-5 表，从 score_rank_daily 迁移数据 |
| 2b | run_daily 双写旧表+新表 |
| 2c | 候选导出改为读新表 |
| 2d | claude_score 降级为展示字段 |
| 2e | M8 扩展为 Champion–Challenger |
| **验收** | 生产排序不依赖 claude_score；bs_model_* 有 PIT 证据才可用 |

### 第三阶段（订单账本 + QMT）

| 步骤 | 内容 |
|------|------|
| 3a | 新建 ads_signal_decisions + ads_pretrade_risk_checks |
| 3b | 导出脚本生成 OrderIntent（非直接写订单） |
| 3c | PreTradeRiskCheck 在 OrderIntent → BrokerOrder 之间拦截 |
| 3d | QMT Adapter |

---

## 七、汇总待确认事项

| # | 问题 | 紧急度 |
|---|------|--------|
| 1 | 删除 scheduler.py → archive/，只保留 web/app.py？ | P0 |
| 2 | ashare_client 第一版用 SQL 封装（非 HTTP），同意？ | P0 |
| 3 | 策略卡清单（8张）是否正确？有遗漏？ | P0 |
| 4 | score_rank_daily 渐进式迁移（双写→降级→废除），同意？ | P0 |
| 5 | 五层拆分：新建5张表 vs 先在 score_rank_daily 上加外键？ | P1 |
| 6 | claude_score 降级为展示字段（不参与排名），确认？ | P1 |
| 7 | M8 改造：扩展现有脚本 vs 新建模块？ | P1 |
| 8 | 订单账本：先建2张新表复用其余 vs 一次性建9张？ | P1 |
| 9 | chenyiyunSelected 整条链路归档，确认？ | P0 |
| 10 | 第一阶段是否立刻执行？还是等今晚评分跑完后再动手？ | P0 |
