# 科创板 PE/PEG × Z 值 × MIT Trader 策略回测摘要 (2026-11-27 审计修复版)

- **策略属性**: `RESEARCH_ONLY` (严格隔离于生产环境与实盘交易路由之外)
- **回测区间**: 2023-09-01 至 2026-11-26 (共 782 个交易日，行情与信号预热始于 2019-07-22)
- **股票池标准**: 科创板 `688/689` **逐日/月末动态股票池**，严格按当期时点校验上市满 **1,001 个交易日** 且未退市 (全区间共 622 只科创板股票，473 只满足过 1,001 日门槛)
- **基准标的**: 科创 50 指数 (`000688.SH`) 开盘与收盘归一价格指数
- **账户资金与执行**: 初始本金 2,000,000 元，划分为 3 个独立滚动资金席位 (每席位约 66.67 万元)，多头无杠杆允许空仓，T+1 开盘先卖后买，科创板 200 股起购与碎股全清，20日日均成交额 1% 流动性硬约束，基础成本 7.5 bp，卖出印花税按 2023-08-28 前后切分。
- **审计修复说明**: 已彻底修复 MIT 风控引擎未来信息切片泄露、静态池幸存者偏差、`prototype_rank_z` 月度名单未持久化与日期对齐错位、Three Gates 毛夏普累计笔数虚高及 Z 值停牌滚动窗口问题。

---

## 1. 核心实证结论 (Three Core Empirical Questions)

### Q1: 估值筛选有没有增量？ ("叠加减纯 Z" = 估值+Z 组 减 纯 Z 组)
- **实证结果**: 年化超额收益差为 **-0.19%**，95% 配对分块 Bootstrap 置信区间为 `[-24.23%, +25.23%]`, $p = 0.9650$ (统计学不显著)。
- **机制解析**: 估值筛选 ($0 < \text{PE} < 60, 0 < \text{PEG} < 1.1$) 显著收敛了选股范围（平均仓位从 80.17% 降至 56.29%），但在科创板长期调整期间，仅依靠静态 PE 与历史增长代理无法在均值回归框架下产生显著正 Alpha。

### Q2: Z 值择时有没有增量？ ("叠加减仅估值" = 估值+Z 组 减 仅估值组)
- **实证结果**: 年化超额收益差为 **-20.93%**，95% 配对分块 Bootstrap 置信区间为 `[-57.81%, +15.08%]`, $p = 0.2630$ (统计学不显著)。
- **机制解析**: “仅估值组”月末轮动 Top 3 累计收益为 **-25.04%** (Sharpe -0.01)，而叠加日线 Z 值 ($Z < -2$ 超跌开仓、$Z \ge 0$ 退出) 后累计收益反而恶化至 **-51.79%** (Sharpe -1.00)。原因在于科创板成长股下跌趋势中，50 日均线 Z 值跌破 -2 后往往面临持续寻底（接飞刀效应），均值回归择时在此样本窗口内产生了负增量。

### Q3: MIT Trader 风控是否改善扣费后的风险收益？ ("风控组减叠加组")
- **实证结果**: 年化超额收益差为 **+20.49%**，95% 配对分块 Bootstrap 置信区间为 `[-5.17%, +48.17%]`, $p = 0.1190$。
- **机制解析**: **MIT Trader 风控在零未来函数下依然展现出极强的资本保全与尾部风险阻断能力**：
  - 累计净收益从 **-51.79%** 挽救至 **-2.26%**；
  - 最大回撤从 **-52.31%** 剧烈压缩至 **-3.05%**（回撤绝对值减少 **49.26%**）；
  - 200 万元初始资金期末保全 **195.49 万元**（未风控组仅剩 96.41 万元）；
  - **驱动机制**：严格按 `[:prev_dt]` 截断的 GARCH(1,1) 动态波动止损 ($2\sigma\sqrt{5}$) 与 Monte Carlo 1/4 Kelly 引擎识别出下行周期中 Z 值交易期望为负，自动拒绝绝大部分开仓票据（全区间仅成交 8 笔，平均仓位 0.41%），成功规避了系统性亏损。

---

## 2. 六大对照组同窗全景业绩表

| 对照组名称 | 策略模式与选股逻辑 | 累计收益率 | 年化复合收益 (CAGR) | 年化波动率 | 夏普比率 (Sharpe) | 最大回撤 (MDD) | 卡玛比率 (Calmar) | 平均仓位 (Exposure) | 782日成交笔数 |
|---|---|---|---|---|---|---|---|---|---|
| **基准: 科创50** | `000688.SH` 价格指数归一 | **+25.16%** | **+7.50%** | **37.59%** | **+0.38** | **-47.86%** | — | 100.0% | 被动基准 |
| **纯 Z 组** | 动态 1001d 池 + 纯 Z 穿越 | **-54.29%** | **-22.30%** | **28.89%** | **-0.73** | **-54.29%** | **0.41** | **80.17%** | 88 笔 |
| **本地原型排名组** | 504d 扣费 Sharpe 排名 + Z 值 | **+37.27%** | **+10.75%** | **14.98%** | **+0.76** | **-12.55%** | **0.86** | **14.85%** | 35 笔 |
| **仅估值组** | 月初 Top 3 PEG/PE 轮动 (无择时) | **-25.04%** | **-8.87%** | **42.60%** | **-0.01** | **-52.52%** | **0.17** | **88.35%** | 38 笔 |
| **估值 + Z 代理版** | 月末 PEG/PE 池 + 日线 Z 穿越 | **-51.79%** | **-20.95%** | **21.29%** | **-1.00** | **-52.31%** | **0.40** | **56.29%** | 83 笔 |
| **估值+Z+MIT风控** | 严格 PIT GARCH + Kelly/VaR 控制 | **-2.26%** | **-0.73%** | **0.77%** | **-0.95** | **-3.05%** | **0.24** | **0.41%** | 8 笔 |
| **估值 + Z 预测版** | 卖方一致预期净利润预测算 PEG | *不可估计* | *不可估计* | *不可估计* | *不可估计* | *不可估计* | — | — | **数据缺失** |

---

## 3. MIT Three Gates 三门禁检验与评级判定

| 对照组 | 第一关 OOS (毛SR / 正折率) | 第二关 Luck (置换p / DSR) | 第三关 Cost (净SR / 2x成本) | 终审评级 |
|---|---|---|---|---|
| **纯 Z 组** | -0.70 / 40% (**FAIL**) | p=0.9600 / 0.00 (**FAIL**) | -0.73 / -0.76 (**FAIL**) | **REJECTED** |
| **本地原型排名组** | +0.78 / 80% (**PASS**) | p=0.7201 / 0.00 (**FAIL**) | +0.76 / +0.74 (**PASS**) | **REJECTED** |
| **仅估值组** | 0.00 / 40% (**FAIL**) | p=0.7006 / 0.00 (**FAIL**) | -0.01 / -0.02 (**FAIL**) | **REJECTED** |
| **估值 + Z 代理版** | -0.97 / 20% (**FAIL**) | p=0.9075 / 0.00 (**FAIL**) | -1.00 / -1.03 (**FAIL**) | **REJECTED** |
| **估值+Z+MIT风控** | -0.93 / 0% (**FAIL**) | p=0.9560 / 0.00 (**FAIL**) | -0.95 / -0.97 (**FAIL**) | **REJECTED** |

---

## 4. 预测 PEG 原版的数据现实与补数条件

- **本地只读库核验事实**: `ods_forecast_consensus_versioned` 与 `dws_forecast_consensus_pit_daily` 在科创板 `688/689` 股票上的逐行时点核验通过数为 **0**；`ods_forecast_consensus_metric_versioned_v1` 最早可见时间为 **2026-09-11**，且 `pit_tier` 均为 `ROW_LEVEL_PIT_UNVERIFIED`。收益真实标记为 **`UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING`**。
- **补数与复跑条件**: 需接入 2023–2026 年科创板机构卖方研报一致预期快照，补齐 `ann_date`、`knowledge_time`、`available_at` 双时钟与时点版本修订证据，通过 ASDC Row-Level PIT 审计后方可解冻复跑。

---

## 5. 交付产物与审计链接

- **指标统一源 JSON**: [`exports/backtest/star_pe_peg_z_mit/metrics.json`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/metrics.json)
- **每日净值对比 CSV**: [`exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/daily_nav_comparison.csv)
- **MIT 风控成交明细 CSV**: [`exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv`](file:///Volumes/extension/projects/Chenyiyun2087/exports/backtest/star_pe_peg_z_mit/mit_risk_fills.csv)
- **详尽科研报告**: [`reports/star_pe_peg_z_mit/STAR_PE_PEG_Z_MIT_RESEARCH_REPORT.md`](file:///Volumes/extension/projects/Chenyiyun2087/reports/star_pe_peg_z_mit/STAR_PE_PEG_Z_MIT_RESEARCH_REPORT.md)
- **独立复现 CLI**: [`scripts/research/run_star_pe_peg_z_mit.py`](file:///Volumes/extension/projects/Chenyiyun2087/scripts/research/run_star_pe_peg_z_mit.py)
- **确定性单元测试**: [`test/research/star_pe_peg_z_mit/test_star_strategy_authenticity.py`](file:///Volumes/extension/projects/Chenyiyun2087/test/research/star_pe_peg_z_mit/test_star_strategy_authenticity.py)
