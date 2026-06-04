# 策略系统优化升级方案（2026-06-04）

本文档根据本轮会话对 `归档.zip` 内研究文件、生产候选、回测产物、Walk-Forward 结果和策略对照报告的分析整理而成。目标是将当前系统从“单一进攻策略 Top5 选股”升级为“市场状态识别 + 策略切换 + 仓位控制 + 风险约束”的生产级策略框架。

---

## 1. 核心结论

当前项目已经形成较完整的策略研究流水线：

```text
评分生成 → B/S 信号增强 → 动态权重 → 生产候选 → 订单草案 → 账户级 T+1 回测 → 三年就绪检查 → 多策略对照
```

但最新研究文件显示，当前生产策略 `tiered_liquidity_then_bs_v2` 具有明显风格依赖：最近一年进攻效果强，但三年严格回测暴露出灾难性回撤。因此，`tiered_liquidity_then_bs_v2` 应继续保留为 `risk_on` 环境下的进攻子策略，但不宜作为无条件满仓主策略。

下一阶段升级重点：

```text
市场状态识别
仓位控制
防守策略切换
行业集中度控制
组合级回撤降仓
个股止损机制
B/S 模型影子过滤
实盘交易闭环
```

---

## 2. 关键回测发现

### 2.1 `tiered_liquidity_then_bs_v2` 收益风险画像

| 口径 | 收益 | 最大回撤 | 判断 |
|---|---:|---:|---|
| 最近一年 | +208.94% | -23.28% | 进攻有效 |
| 最近半年 | -28.16% | -46.57% | 明显失效 |
| 最近三个月 | +17.84% | -15.92% | 短期恢复 |
| 2025 年初至今 | -11.99% | -54.70% | 风格切换失败 |
| 2023-01-04 至 2026-06-02 三年严格回测 | -70.63% | -94.20% | 不适合无条件满仓生产 |

结论：

> 当前策略不是稳定复利策略，而是高 beta、高弹性、强风格暴露策略。盈利期爆发修复，亏损期连续失血。

### 2.2 最近半年防守策略明显更优

| 策略 | 最近半年收益 | 最大回撤 |
|---|---:|---:|
| `baseline_full_liquidity_detail` | +21.67% | -36.55% |
| `baseline_full_dynamic_factor_industry_cap2` | +4.02% | -39.51% |
| `baseline_full_score` | -4.23% | -35.74% |
| `adaptive_style_switch` | -14.45% | -42.99% |
| `tiered_liquidity_then_bs_v2` | -28.16% | -46.57% |

说明系统里已经存在可用的防守候选，但防守策略还没有成为生产主控的一部分。

### 2.3 三年评分数据已经具备可信回测基础

三年评分就绪检查结果：

```text
交易日：824
评分覆盖日：824
就绪日：824
缺评分日：0
核心字段异常日：0
模型字段残留日：0
是否满足三年可信回测：是
```

这意味着后续可以持续使用三年严格回测作为生产策略准入标准。

---

## 3. 当前生产候选暴露的问题

最新生产候选目录：

```text
production_candidates/20260603_234550_tiered_liquidity_then_bs_v2
```

信号日：`2026-06-02`

当前 Top5：

| 排名 | 股票 | 行业 | 权重 | bs_score_v2 |
|---:|---|---|---:|---:|
| 1 | 江海股份 | 元器件 | 20% | 74.53 |
| 2 | 新易盛 | 通信设备 | 20% | 71.65 |
| 3 | 大唐发电 | 火力发电 | 20% | 71.56 |
| 4 | 沃格光电 | 元器件 | 20% | 71.40 |
| 5 | 风华高科 | 元器件 | 20% | 71.35 |

主要问题：

1. **行业集中度偏高**：元器件 3 只，占 60% 仓位；通信设备 20%；整体对 AI/电子链暴露较高。
2. **B/S 语义不清**：候选中 `is_bs_candidate = 0`，但 `bs_gate_label = 可买`，说明当前候选更接近“B 点风格增强排序”，而非严格真实 B/S 事件。

建议增加字段：

```text
bs_event_type:
    true_bs_event
    bs_score_rank_only
    gate_pass_only
```

---

## 4. 动态权重模块评价

最新动态权重结构：

| 因子 | 权重 |
|---|---:|
| `s_rs` | 27.82% |
| `score_liq_breakout_adj` | 19.96% |
| `score` | 19.55% |
| `liquidity_detail_score` | 14.73% |
| `s_breakout` | 9.94% |
| `s_liquidity` | 7.99% |

当前系统最重视相对强弱、流动性突破和综合评分。这适合强势行情，但在震荡或退潮行情中容易追高。

建议引入状态条件权重：

```text
risk_on:
    提高 s_rs / s_breakout / bs_score_v2 权重

neutral:
    均衡使用 dynamic_factor_score / liquidity_detail_score / bs_score_v2

risk_off:
    降低 s_rs / s_breakout
    提高 liquidity_detail_score / low_impact_cost / amount_stability
```

---

## 5. B 点模型 Walk-Forward 评价

`bs_model_walkforward/20260513_030343_walkforward` 显示：

```text
目标：hit_20_10pct
风险目标：mdd_20
特征数：149
预测月份数：2
预测样本数：243
平均 Precision@10：0.30
平均 Precision@20：0.35
平均 ROC AUC：0.7636
```

分月：

| 月份 | ROC AUC | Precision@10 | Precision@20 |
|---|---:|---:|---:|
| 2026-03 | 0.6313 | 0.10 | 0.30 |
| 2026-04 | 0.8958 | 0.50 | 0.40 |

模型概率分箱命中率：

| 模型概率分位 | `hit_20_10pct` 命中率 |
|---|---:|
| 最低 20% | 4.08% |
| 20%-40% | 20.83% |
| 40%-60% | 26.53% |
| 60%-80% | 31.25% |
| 最高 20% | 32.65% |

结论：

> B/S 模型具备一定排序能力，但样本月份太少，不适合直接替代规则策略。当前阶段应作为风控过滤器或降权因子，而不是主排序因子。

建议：

```text
如果 bs_model_prob 低于 30%，即使 bs_score_v2 较高，也降低候选权重。
如果 bs_model_expected_mdd 过大，则从 20% 降到 10% 或剔除。
```

---

## 6. 目标架构

建议升级为四层生产框架：

```text
第一层：数据与评分可信层
    scoreRank.cli.run_daily
    三年评分覆盖检查
    字段完整性检查
    禁用未来字段检查

第二层：市场状态层
    index_bucket
    market_liquidity_bucket
    market_bs_ratio
    market_amount_ratio_20
    market_hs300_ret_20
    market_limit_up_rate

第三层：策略选择层
    risk_on      → tiered_liquidity_then_bs_v2_industry_cap2
    neutral      → baseline_full_dynamic_factor_industry_cap2
    risk_off     → baseline_full_liquidity_detail_market_gate
    panic/off    → cash / 低仓位观察

第四层：交易执行层
    TopN
    行业上限
    单票上限
    总仓位上限
    止损/降仓
    订单草案
    飞书/页面展示
```

建议新主控策略命名：

```text
trusted_regime_switch_v1
```

核心逻辑：

```text
市场状态决定策略；
策略决定股票池；
股票池决定排序；
排序后做行业约束；
行业约束后做模型过滤；
模型过滤后做仓位缩放；
最后生成订单。
```

---

## 7. 分阶段升级路线

### 阶段 1：立即修复，1-3 天

#### 7.1 生产策略切换到行业约束版本

优先使用：

```text
tiered_liquidity_then_bs_v2_industry_cap2
```

规则：

```text
Top5
单行业最多 2 只
单行业最高权重 40%
默认总仓位 50%-70%
risk_on 才允许升到 100%
```

#### 7.2 增加生产候选一致性检查

增加字段：

```text
bs_event_type:
    true_bs_event
    bs_score_rank_only
    gate_pass_only
```

#### 7.3 生产订单增加风险标签

在 `trusted_strategy_orders.csv` 增加：

```text
market_regime
index_bucket
market_liquidity_bucket
industry_weight_after_trade
single_stock_vol_20
hist_mdd_20
is_overheated
suggested_position_scale
```

---

### 阶段 2：短期增强，1-2 周

#### 7.4 增加市场门禁 `market_gate`

建议规则：

```text
risk_on:
    market_hs300_ret_20 > 0
    market_amount_ratio_20 >= 0.9
    market_bs_ratio 正常或扩张
    index_bucket != index_weak

neutral:
    market_hs300_ret_20 between -3% and 3%
    market_amount_ratio_20 >= 0.8

risk_off:
    market_hs300_ret_20 < -3%
    或 market_amount_ratio_20 < 0.8
    或 指数短期大跌
```

策略映射：

| 状态 | 策略 | 仓位 |
|---|---|---:|
| risk_on | `tiered_liquidity_then_bs_v2_industry_cap2` | 80%-100% |
| neutral | `baseline_full_dynamic_factor_industry_cap2` | 50%-70% |
| risk_off | `baseline_full_liquidity_detail_market_gate` | 20%-50% |
| panic | 空仓或观察 | 0%-20% |

#### 7.5 建立组合级回撤降仓

账户回测和订单生成模块增加：

```text
rolling_peak_20
rolling_drawdown_20
rolling_drawdown_60
```

规则：

```text
组合20日回撤 < -8%：总仓位降到50%
组合20日回撤 < -12%：总仓位降到30%
组合60日回撤 < -20%：暂停进攻策略10个交易日
```

#### 7.6 增加个股层止损

规则：

```text
个股亏损超过 -8%：降半仓
个股亏损超过 -12%：清仓
如果该股仍在 Top5 且市场 risk_on：允许重新买入，但必须重新通过门禁
```

---

### 阶段 3：中期优化，2-4 周

#### 7.7 重构 `adaptive_style_switch`

将当前 performance-based switch 升级为 regime-based switch。

旧逻辑：

```text
根据哪个策略近期收益更好进行切换
```

新逻辑：

```text
根据市场状态直接映射策略
```

状态变量优先级：

1. 指数趋势：`market_hs300_ret_20`
2. 成交量：`market_amount_ratio_20`
3. B 点拥挤度：`market_bs_ratio`
4. 涨停情绪：`market_limit_up_rate`
5. 横截面平均分：`market_avg_score`
6. 候选波动：`vol_20`
7. 候选历史回撤：`hist_mdd_20`

#### 7.8 动态因子权重增加状态条件

权重模板：

```text
risk_on_weights:
    s_rs 高
    s_breakout 高
    bs_score_v2 高

neutral_weights:
    dynamic_factor_score 高
    liquidity_detail_score 中高
    s_rs 中

risk_off_weights:
    liquidity_detail_score 高
    low_impact_cost 高
    amount_stability 高
    s_rs 低
    s_breakout 低
```

#### 7.9 模型只做过滤，不做主排序

建议生产公式：

```text
final_score =
    0.60 * rule_score
  + 0.20 * liquidity_quality
  + 0.10 * market_fit_score
  + 0.10 * model_filter_score
```

其中模型低置信度只扣分，不直接加满分。

---

### 阶段 4：长期升级，1-2 个月

#### 7.10 建立真实交易闭环

当前回测口径是 T+1 开盘、成本 0.075%、滑点 0。实盘需要记录：

```text
真实成交价
真实滑点
是否买到
是否涨停无法买入
是否跌停无法卖出
盘中最高/最低触发止损
成交金额占个股成交额比例
```

形成三套收益对照：

```text
backtest_return
paper_trade_return
real_trade_return
```

#### 7.11 引入影子策略矩阵

建议固定保留以下影子策略：

| 影子策略 | 目的 |
|---|---|
| `tiered_liquidity_then_bs_v2` | 进攻基准 |
| `tiered_liquidity_then_bs_v2_industry_cap2` | 行业约束进攻 |
| `baseline_full_liquidity_detail` | 防守基准 |
| `baseline_full_liquidity_detail_market_gate_pos50` | 低回撤防守 |
| `adaptive_style_switch_dynamic_position` | 状态切换观察 |
| `cash` | 空仓基准 |

每天生产输出所有影子策略候选和模拟订单，但真实只执行主控策略。

---

## 8. 推荐生产方案

### 方案 A：稳健实盘版

```text
主策略：regime_switch_v1

进攻状态：
    tiered_liquidity_then_bs_v2_industry_cap2
    总仓位 70%-100%

中性状态：
    baseline_full_dynamic_factor_industry_cap2
    总仓位 50%-70%

防守状态：
    baseline_full_liquidity_detail_market_gate
    总仓位 20%-50%

极端风险：
    空仓或 0%-20% 观察仓
```

### 方案 B：收益优先但有风控版

```text
主策略：tiered_liquidity_then_bs_v2_industry_cap2

约束：
    单行业最多2只
    单票20%
    总仓位默认70%
    risk_on提升到100%
    risk_off降到30%
    组合20日回撤超过-8%降仓
    个股-12%硬止损
```

### 方案 C：保守验证版

```text
主策略：baseline_full_liquidity_detail_market_gate_pos50

用途：
    先跑真实交易闭环
    记录滑点和执行误差
    同时保留进攻策略影子盘
```

若以 50 万启动资金进行实盘验证，优先建议方案 B 或方案 C，不建议直接无条件满仓进攻策略。

---

## 9. 优先级清单

| 优先级 | 任务 | 原因 |
|---:|---|---|
| P0 | 停止无条件满仓 `tiered_liquidity_then_bs_v2` | 三年最大回撤过高 |
| P0 | 加入市场门禁和仓位缩放 | 控制策略失效期 |
| P0 | 单行业最多2只 | 当前行业集中度过高 |
| P1 | 增加组合回撤降仓 | 防止连续亏损扩散 |
| P1 | 增加个股止损 | 防止单票大亏 |
| P1 | 明确 `is_bs_candidate=0` 与 `bs_gate_label=可买` 的语义 | 避免策略解释错误 |
| P2 | 重构 adaptive 为 regime-based | 当前 adaptive 没有解决核心问题 |
| P2 | 模型作为过滤器上线 | 模型有排序能力但样本不足 |
| P3 | 实盘成交闭环 | 校正滑点、涨跌停、成交偏差 |
| P3 | 影子策略矩阵日报 | 长期持续评估策略切换 |

---

## 10. 建议落地模块

基于研究文件中出现的路径和任务，可以优先改造以下模块：

```text
scripts/research_full_pool_liquidity_strategies.py
    - 增加 industry_cap2 默认生产测试
    - 增加 market_gate 对照
    - 增加 regime_switch_v1 策略定义

scripts/research_trusted_strategy_account_backtest.py
    - 增加总仓位缩放
    - 增加组合级 rolling drawdown 降仓
    - 增加个股止损/降半仓逻辑
    - 增加真实交易闭环字段预留

scripts/ops/export_trusted_strategy_candidates.py
    - 增加 bs_event_type
    - 增加 market_regime
    - 增加 suggested_position_scale
    - 增加 industry_weight_after_trade
    - 增加风险标签输出

scoreRank.cli.run_daily
    - 保持评分可信层
    - 增加字段完整性、未来字段、模型字段残留检查
```

---

## 11. 最终建议

当前系统不能再只追求 Top5 收益最大化，应升级为：

```text
进攻策略 + 防守策略 + 市场门禁 + 动态仓位 + 行业约束 + 止损降仓
```

建议下一版主控策略命名为：

```text
trusted_regime_switch_v1
```

`tiered_liquidity_then_bs_v2` 应保留，但角色应调整为：

```text
risk_on 状态下的进攻子策略
```

下一步最优先的生产级改动是：

1. 生产策略切换到 `tiered_liquidity_then_bs_v2_industry_cap2`；
2. 增加 `market_gate`；
3. 增加总仓位缩放；
4. 增加组合级回撤降仓；
5. 增加个股止损；
6. 增加 `bs_event_type` 和订单风险标签。
