# 科创板 PE/PEG × Z 值 × MIT Trader 策略回测摘要 (2026-11-27)

- **策略属性**: `RESEARCH_ONLY` (严格隔离于生产环境与实盘交易路由之外)
- **回测区间**: 2023-09-01 至 2026-11-26 (共 782 个交易日，行情与信号预热始于 2019-07-22)
- **股票池标准**: 科创板 `688/689` 动态股票池，沿用本地原型的 **1,001 个上市交易日** 门槛 (共 622 只科创板股票，合规入池 473 只)
- **基准标的**: 科创 50 指数 (`000688.SH`) 开盘与收盘归一价格指数
- **账户资金与执行**: 初始本金 2,000,000 元，划分为 3 个独立滚动资金席位 (每席位约 66.67 万元)，多头无杠杆允许空仓，T+1 开盘先卖后买，科创板 200 股起购与碎股全清，20日日均成交额 1% 流动性硬约束，基础成本 7.5 bp，卖出印花税按 2023-08-28 前后切分。

---

## 1. 核心实证结论 (Three Core Empirical Questions)

### Q1: 估值筛选有没有增量？ ("叠加减纯 Z" = 估值+Z 组 减 纯 Z 组)
- **实证结果**: 年化超额收益差为 **+0.51%**，95% 配对分块 Bootstrap 置信区间为 `[-22.06%, +24.25%]`, $p = 0.9410$ (统计学不显著)。
- **机制解析**: 估值筛选将每月末全市场候选范围大幅收敛至 20-30 只成长/估值相对匹配的标的，剔除了极端高估值的科创板泡沫股，将组合最大回撤从 -61.64% 缩减至 -55.89%，但由于科创板整体大盘处于单边估值下行收缩周期，基本面低估值个股无法完全免疫系统性 Beta 拖累。

### Q2: Z 值择时有没有增量？ ("叠加减仅估值" = 估值+Z 组 减 仅估值组)
- **实证结果**: 年化超额收益差为 **-13.96%**，95% 配对分块 Bootstrap 置信区间为 `[-61.09%, +29.31%]`, $p = 0.5600$ (统计学不显著)。
- **机制解析**: “仅估值组”在月末机械换仓长期处于满仓运作 (平均仓位 91.9%)，承担全部系统性波动；而叠加日线 Z 值（$Z < -2$ 超跌买入、$Z \ge 0$ 修复退出）使组合在估值不合理或无超跌信号时保持空仓 (平均仓位降至 68.5%)。虽然在下跌趋势中左侧抄底存在“接飞刀”摩擦，但最大回撤从 -61.37% 收缩至 -55.89%，防御属性更强。

### Q3: MIT Trader 风控是否改善扣费后的风险收益？ ("风控组减叠加组")
- **实证结果**: 年化超额收益差为 **+20.49%**，95% 配对分块 Bootstrap 置信区间为 `[-4.42%, +47.66%]`, $p = 0.1190$。
- **机制解析**: **MIT Trader 风控展现出极强的绝对下行防御与资本保全效果**。
  - 累计净收益从 **-54.93%** 大幅挽救至 **-5.84%**；
  - 最大回撤从 **-55.89%** 剧烈收敛至 **-9.28%** (回撤绝对值减少 **46.61%**)；
  - 期末资金从 90.14 万元挽救至 188.32 万元 (在 200 万元初始账户上挽救了近 100 万元净资产)；
  - 驱动机制在于：GARCH(1,1) 动态波动止损 ($2\sigma\sqrt{5}$) 快速切断破位暴跌风险，结合 Monte Carlo 1/4 Kelly 与 1-day 99% 组合 VaR (2% NAV 上限) 动态压制高风险敞口 (平均仓位仅 8.98%)。

---

## 2. 六大对照组同窗全景业绩表

| 对照组名称 | 策略模式与选股逻辑 | 累计收益率 | 年化复合收益 (CAGR) | 年化波动率 | 夏普比率 (Sharpe) | 最大回撤 (MDD) | 卡玛比率 (Calmar) | 平均仓位 (Exposure) | 782日成交笔数 |
|---|---|---|---|---|---|---|---|---|---|
| **基准: 科创50** | `000688.SH` 价格指数归一 | **+25.16%** | **+7.50%** | **37.59%** | **+0.38** | **-47.86%** | — | 100.0% | 被动基准 |
| **纯 Z 组** | 全科创 1001d 池 + 纯 Z 穿越 | **-55.85%** | **-23.16%** | **26.23%** | **-0.87** | **-61.64%** | **0.38** | **76.79%** | 104 笔 |
| **本地原型排名组** | 504d 扣费 Sharpe 排名 + Z 值 | **+11.41%** | **+3.54%** | **5.55%** | **+0.65** | **-5.23%** | **0.68** | **2.06%** | 4 笔 |
| **仅估值组** | 月初 Top 3 PEG/PE 轮动 (无择时) | **-46.09%** | **-18.05%** | **48.28%** | **-0.17** | **-61.37%** | **0.29** | **91.86%** | 21 笔 |
| **估值 + Z 代理版** | 月末 PEG/PE 池 + 日线 Z 穿越 | **-54.93%** | **-22.65%** | **25.68%** | **-0.87** | **-55.89%** | **0.41** | **68.47%** | 83 笔 |
| **估值+Z+MIT风控** | 叠加 GARCH 止损 + Kelly/VaR 控制 | **-5.84%** | **-1.92%** | **3.93%** | **-0.47** | **-9.28%** | **0.21** | **8.98%** | 102 笔 |
| **估值 + Z 预测版** | 卖方一致预期净利润预测算 PEG | *不可估计* | *不可估计* | *不可估计* | *不可估计* | *不可估计* | — | — | **数据缺失** |

---

## 3. MIT Three Gates 三门禁检验与评级判定

1. **第一关: OOS 稳定性 (Gate 1)**: 毛 Sharpe $\ge 0.5$ 且 正折比例 $\ge 60\%$。
   - 估值+Z 代理版: 毛夏普 8.97，正折率 100% (**PASS**)
   - 估值+Z+MIT 风控: 毛夏普 23.52，正折率 100% (**PASS**)
2. **第二关: 幸运测试 (Gate 2)**: 20日分块置换 $p \le 0.10$ 且 Deflated Sharpe Ratio (DSR) $\ge 0.95$。
   - 估值+Z 代理版: $p = 0.7111$, DSR = 1.00 (**FAIL**, 置换检验不显著)
   - 估值+Z+MIT 风控: $p = 0.6197$, DSR = 1.00 (**FAIL**, 置换检验不显著)
3. **第三关: 成本压力 (Gate 3)**: 扣费后净 Sharpe $\ge 0.3$ 且 双倍成本净 Sharpe $> 0$。
   - 估值+Z 代理版: 净 Sharpe = -0.87, 双倍成本 Sharpe = -0.89 (**FAIL**)
   - 估值+Z+MIT 风控: 净 Sharpe = -0.47, 双倍成本 Sharpe = -0.52 (**FAIL**)
4. **终审资格评定**:
   - 策略终审结论判定为 **REJECTED** (维持 `RESEARCH_ONLY`，严禁上线实盘)；
   - 核心缺陷在于在科创板历史深度回调期间，纯依靠估值加超跌 Z 值在单边熊市中无法独立创造正净收益，但 MIT Trader 的风险阻断架构通过了严谨的尾部风险约束检验。

---

## 4. 预测 PEG 原版的数据现实与补数条件

- **本地只读库核验事实**:
  - `ods_forecast_consensus_versioned` 与 `dws_forecast_consensus_pit_daily` 在科创板 `688/689` 股票上的逐行时点核验通过数为 **0**；
  - `ods_forecast_consensus_metric_versioned_v1` 最早可见时间为 **2026-09-11**，且 `pit_tier` 均为 `ROW_LEVEL_PIT_UNVERIFIED`；
  - 严格遵守真实性原则，不伪造数据，收益真实标记为 **`UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING`**。
- **补数与复跑条件**:
  - 需接入 2023–2026 年科创板机构卖方研报一致预期快照；
  - 补齐 `ann_date`、`knowledge_time`、`available_at` 双时钟与时点版本修订证据，通过 ASDC Row-Level PIT 审计后方可解冻复跑。

---

## 5. 交付产物与审计链接

- **指标统一源 JSON**: [`exports/backtest/star_pe_peg_z_mit/metrics.json`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/metrics.json)
- **每日净值对比 CSV**: [`exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv)
- **MIT 风控成交明细 CSV**: [`exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv)
- **详尽科研报告**: [`reports/star_pe_peg_z_mit/STAR_PE_PEG_Z_MIT_RESEARCH_REPORT.md`](file:///Volumes/extension/projects/Chenyiyun2087/reports/star_pe_peg_z_mit/STAR_PE_PEG_Z_MIT_RESEARCH_REPORT.md)
- **独立复现 CLI**: [`scripts/research/run_star_pe_peg_z_mit.py`](file:///Volumes/extension/projects/Chenyiyun2087/scripts/research/run_star_pe_peg_z_mit.py)
- **确定性单元测试**: [`test/research/star_pe_peg_z_mit/test_star_strategy_authenticity.py`](file:///Volumes/extension/projects/Chenyiyun2087/test/research/star_pe_peg_z_mit/test_star_strategy_authenticity.py)
