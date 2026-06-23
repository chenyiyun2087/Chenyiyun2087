# Chenyiyun2087 优化升级预案

**生成日期**: 2026-06-23  
**状态**: 待讨论后执行  
**基于**: ADC→CY2087 详细改进方案 + CY2087 个股分化诊断 + 今日已实施的修改

---

## 一、已完成 vs 待执行

### ✅ 今日已完成（06-23）

| 改动 | 文件 | 内容 |
|------|------|------|
| P0-1 | `scoreRank/core/config.py` L39-50 | 权重调整：liquidity 27%、rs 18%、contraction 3%、breakout 10% |
| P0-2 | `scoreRank/core/scorer.py` L173-174 | contraction 方向修正：从"越小越好"反转为"越大越好" |
| P1 | `config/production_strategy.yaml` | 行业过滤配置（火力发电/通信设备排除） |
| P1 | `scripts/ops/production_config.py` | 读取 industry_filter 配置 |
| P1 | `scripts/ops/export_trusted_strategy_candidates.py` | 候选导出时自动排除负收益行业 |

### ⏳ 待执行

| 优先级 | 改进项 | 预计算力 | 风险 |
|--------|--------|---------|------|
| 🔴 P0-a | **权重二次精细化** — 与ADC方案对齐 | 直接改配置 | 低 |
| 🔴 P0-b | **非线性评分变换** — 压低>75分、抬升30-60分 | 需改scorer | 低 |
| 🟡 P1-a | **ADC补盲信号源** — 交叉参考脚本 | 新建文件 | 低 |
| 🟡 P1-b | **行业共振打分** — 看多/看空加减分 | 改config+scorer | 中 |
| 🟢 P2-a | **趋势标签辅助** — 看涨+3/看跌-5 | 改scorer | 中 |
| 🟢 P2-b | **V型反转复推加仓** | 新建文件 | 低 |
| 🟢 P3 | **信心度加权** — bs_model_prob映射仓位 | 改export脚本 | 低 |

---

## 二、P0-a：权重二次精细化（对齐ADC方案）

### 当前状态

我今天的权重调整与ADC方案有差异：

| 因子 | 旧权重 | 我的调整 | ADC方案建议 | 差异分析 |
|------|--------|---------|-----------|---------|
| trend | 12% | 8% | **15%** | ADC认为trend+趋势标签后价值更大 |
| liquidity | 10% | 27% | **20%** | 我过度提权了，ADC认为IC仅0.06 |
| rs | 12% | 18% | **12%** | ADC认为IC趋零，应回到原来 |
| breakout | 22% | 10% | **5%** | ADC建议更激进地降权 |
| contraction | 10% | 3% | 3% | ✅ 一致 |
| bias | 7% | 7% | **8%** | 微调 |

### 建议方案

采用ADC方案，因为：
- **两系统独立验证了同一结论**（高分≠高收益），ADC方案的权重分配有双系统数据支撑
- liquidity 27%过高——流动性和收益的IC仅0.06，过度依赖会引入噪音
- trend 15%更合理——趋势稳定性+趋势标签组合后能更好地预测方向

**改动**：修改 `scoreRank/core/config.py` `weights` 字典为ADC方案值。

---

## 三、P0-b：非线性评分变换

### 问题

即使权重调整后，评分公式仍可能产生极端值。双系统数据明确显示：
- **<30分**：胜率26%，太差
- **30-60分**：胜率57%，最佳甜区
- **>75分**：胜率恶化

### 方案

在 `scoreRank/core/scorer.py` 中，`score` 最终赋值前加三次方收缩变换：

```python
raw_score = (d["base_score"] - d["penalty"]).clip(0, 100)
center = 55.0
deviation = (raw_score - center) / 25.0
adjustment = (deviation ** 3) * 25.0 * 0.15
d["score"] = (raw_score - adjustment).clip(0, 100)
```

**效果**：
- 30-60分区几乎不变（偏离小）
- 75分被压低约3分，95分被压低约8分
- 15分被抬升约3分

### 待讨论

- 变换强度参数 `0.15` 是否需要调优？
- 是否只对 TRADE 池生效，WATCH 池保留原始分？

---

## 四、P1-a：ADC 补盲信号源

### 为什么需要

两系统仅重合7%（16/217只），ADC独有的32只大牛股（>+10%）CY完全没选到。ADC在中分段（30-60）的选股能力恰好是CY的盲区。

### 方案

新建 `scripts/research/cross_ref_adc_signals.py`（完整代码已在ADC方案文档中）。

核心逻辑：
1. 读取 ADC `ads_selection_digest_history_di` 当日选股
2. 读取 CY `score_rank_daily` 当日评分
3. 交叉比对，找出「ADC选了但CY评分低」的股票
4. 重点标记 ADC中分段(30-60)+CY低分(<65)的甜区补盲目标
5. 输出CSV供人工审核

每日在 `scheduler.py` 中非阻塞调用（失败不阻断主流水线）。

### 依赖

- 需要能访问 AShareDataCenter 的 MySQL database（同实例不同库，需读 `config/etl.ini` 取凭证）
- `ads_selection_digest_history_di` 表需存在

### 待讨论

- 补盲信号是仅做观察名单，还是直接进入候选池？
- 如果直接进池，权重如何分配（给ADC信号多少权重）？

---

## 五、P1-b：行业共振打分

### 问题

双系统在多个行业上高度共振（同方向），但CY没有行业级加减分。

### 方案

**config.py** 新增 `industry_resonance` 配置：
- 看空行业（火力发电-10、煤炭开采-8等）— 额外扣分
- 看多行业（半导体+5、小金属+4等）— 仅对中分段30-65加分

**scorer.py** 在 score 赋值后应用行业加减分。

### 待讨论

- 扣分是否也限制在 TRADE 池？还是全量？
- 行业名单更新频率：每月第一周自动跑？
- 是否需要把 `warn_industries`（矿物制品/互联网等仅1笔样本的）也加入扣分？

---

## 六、P2-a：趋势标签辅助

### 问题

ADC「看涨」标签→CY胜率62%，「看跌」→胜率25%。趋势方向预测力显著。

### 方案

在 scorer.py 中新增 `trend_label` 计算（看涨/看跌/震荡），基于已有特征：
- 看涨：trend_ok=1 AND bull_align=1 AND rs20>0
- 看跌：trend_ok=0 AND rs20<-0.03 AND bias_ma20<-0.05
- 震荡：其余

最终 score 纳入趋势调整（看涨+3分，看跌-5分）。

### 待讨论

- 需要 ALTER TABLE score_rank_daily 加 `trend_label` 列，是否可以？
- 趋势标签的阈值（rs20<-0.03, bias<-0.05）是否需要参数化到 config.py？

---

## 七、P2-b + P3：V型反转 + 信心度加权

这两个改动都在 `export_trusted_strategy_candidates.py` 的权重计算环节，改动量小、风险低，建议一起做：

### V型反转
- 检测首次亏损后反弹的复推信号
- V型反转 → 权重×1.3
- 持续下跌无反弹 → 权重×0.5

### 信心度加权
- bs_model_prob 映射为系数 0.7~1.3
- 最终仓位 = 基础权重 × 信心系数

### 待讨论

- V型反转需要查历史订单数据，计算量会不会太大？
- 两个系数是相乘关系还是取max/min？

---

## 八、推荐执行顺序

```
第一波（今天，低风险，无依赖）：
├── P0-a 权重二次精细化（改配置1行）
├── P0-b 非线性评分变换（改scorer 10行）
├── P3  信心度加权（改export脚本 15行）
└── 跑一次 run_daily 验证评分分布变化

第二波（明天，需要ADC数据库连通性验证）：
├── P1-a ADC补盲信号源（新建脚本，先手动跑一次看效果）
└── P2-a 趋势标签辅助（改scorer + db_io）

第三波（后天，需要前两波验证通过）：
├── P1-b 行业共振打分（config + scorer）
└── P2-b V型反转复推加仓（新建脚本 + 集成）

第四波（下周，运行一周后验证）：
├── 跑 review_orders_forward_performance.py
├── 跑 diagnose_order_dispersion.py
└── 根据结果迭代参数
```

---

## 九、回滚风险

所有改动都是**纯增量或参数调整**：
- P0/P1 改了权重和公式 → 回滚只需恢复 config.py 和 scorer.py 的 git diff
- P2/P3 新增文件 → 不影响现有流水线
- 唯一有风险的是 scorer.py 的非线性变换和行业共振，可能导致评分分布剧烈变化

**建议**：执行前先备份当前评分结果（`score_rank_daily` 最近3天的数据），跑完新评分后对比分布。

---

## 十、待确认事项

1. **ADC数据库连通性**：`AShareDataCenter` 的 MySQL 是否与 CY2087 在同一实例？需要确认 `config/etl.ini` 中的凭证对本机可用。

2. **score_rank_daily 加列**：trend_label 需要 ALTER TABLE，是否允许？

3. **评分变换强度**：非线性变换的 center=55, half_width=25, strength=0.15 这三个参数是否需要先做网格搜索？

4. **补盲信号入池策略**：ADC交叉参考的结果是仅做观察（人工审核）还是直接进入候选池？

5. **行业共振扣分范围**：warn_industries（仅1笔样本的行业）是否也加入扣分，还是等更多数据？

请就以上事项给出意见，确认后我立即开始执行。
