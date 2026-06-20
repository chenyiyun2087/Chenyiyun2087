# CONTEXT.md — 量化领域共享词典

AI、策略研发和代码都必须遵守的统一术语定义。每个术语只允许一种解释。

---

## 1. 时间与交易日

### 信号日（Signal Date / T）
策略在**收盘后**读取当日及之前所有可得数据，生成候选池和调仓目标的日期。
- 信号日当天收盘价已落库，但**不能使用 T+1 及以后的任何数据**。
- 在代码中通常记为 `trade_date`、`signal_date` 或 `asof_date`。

### 执行日（Execution Date / T+1）
订单在**下一交易日开盘**实际执行的日期。
- 不是信号日当天，也不是信号日之后的任意一天。
- 买入必须使用 T+1 开盘价（或可成交价），不得使用 T 日收盘价。
- 卖出同样使用 T+1 开盘价，除非有特殊风控规则。

### 决策日（Decision Date）
等同于信号日。策略在当日收盘后做出决策。

---

## 2. 数据可得性

### 点时可得数据（Point-in-Time Data）
在信号日收盘时已经落库、可以被策略使用的数据。
- 行情：信号日收盘价、成交量等已在 `dwd_stock_daily_standard`。
- 因子：动态因子只允许使用 `exit_date < signal_date` 的已完成样本。
- B/S 检测：必须在信号日收盘后才能获取当日截图和 OCR 结果。
- 行业分类、ST 标签、停牌状态：以信号日最新可得版本为准。
- 公司行为、分红、配股：以除权除息日 ≤ 信号日为准。

### 未来函数（Future Data Leak）
在信号日尚不可得、但被策略或回测使用的数据。
- 严禁：使用 T+1 收盘价做 T 日决策。
- 严禁：使用 T 日收盘后才公布的财报、评级、新闻。
- 严禁：回测时使用 `trade_date` 之后的因子值。

---

## 3. 策略体系

### sina 策略体系
以 Sina 财经 B/S 检测为起点的策略链。包含：
- B/S 截图 → OCR 检测 → `bs_detection_results`
- ScoreRank 三线评分（技术分、AI 六维分、因子优化分）
- M2~M8 策略评估链
- M7 调仓与实盘跟踪
- **与 chenyiyun 策略体系共享基础设施，但决策逻辑完全独立**。

### chenyiyun 策略体系
迁移自 JoinQuant 的独立策略链。包含：
- 高股息 + 低波动 + 低杠杆 + 小市值多因子筛选
- 日/周调仓信号
- 涨停监控与仓位更新
- **与 sina 策略体系共享基础设施，但决策逻辑完全独立**。

### 策略隔离（Strategy Isolation）
两条策略链的信号生成、调仓规则、评估标准完全独立。
- 可以共享：MySQL、trade_cal、web console、live_positions。
- 不能共享：信号逻辑、排名规则、调仓参数、回测结论。

---

## 4. 评分与池

### score_rank_daily
全 A 股每日评分表。每一行是一只股票在一个交易日的三线评分（score + opt_score + claude_score）。

### 候选池（Candidate Pool）
满足 `is_bs_candidate=1` 且 score ≥ 60 的股票集合。
- TRADE 池：score ≥ 75。
- WATCH 池：60 ≤ score < 75。

### 排名池（Ranked Pool）
候选池经过策略选择（金字塔/加权/象限）排序后的结果。

### 最终组合（Final Portfolio）
排名池经过风险总闸、行业限制、仓位缩放、持仓锁定后的 Top5 实际交易目标。

---

## 5. 回测体系

### 可信回测（Trusted Backtest / Strict Backtest）
满足以下所有条件的账户级回测：
- T 日信号，T+1 开盘执行
- 单边成本 7.5bp，滑点 10bp（可配置）
- 持仓上限 5 只，持有期 10 日
- 公司行为正确处理
- 严格 T+1 账本（ExecutionLedger）
- 无未来函数违规
- 结果可重复（相同代码 + 配置 + 数据快照 → 相同输出）

### 研究回测（Research Backtest）
允许探索性指标和参数的快速回测。可用于：
- 因子效果初筛
- 参数敏感性分析
- 策略对比排序
- **不得直接作为生产收益结论或实盘依据**。

### M2~M8 评估链
基于 `b_event_fact` / `b_event_kpi` 事件级数据的策略评估体系：
- M2：固定策略预设回归
- M3：参数网格搜索
- M4：多策略投票融合
- M5：滚动窗口验证
- M6：事件级 NAV 回测
- M7：调仓规则引擎
- M8：定时循环（运行 M2+M3 并落库）

---

## 6. 执行与审计

### 影子盘（Shadow）
在不使用真实资金的前提下，完整模拟策略的目标组合、订单生成与 T+1 成交。
- 记录每笔订单的理论价格与实际可成交价。
- 输出成交率、滑点、涨跌停阻断、不可成交原因。
- 用于验证策略的**可执行性**，不是用于实盘。

### 模拟盘（Paper Trading）
使用真实行情但不使用真实资金的模拟交易。
- 当前项目不包含完整的模拟盘系统。

### 实盘（Live Trading）
使用真实资金、真实券商接口的交易。
- 当前项目只生成**订单草案**供人工确认，不自动下单。

### 订单草案（Order Draft）
由候选导出器生成的 BUY/SELL 建议，写入 `ads_local_strategy_orders`。
- 不是券商委托单。
- 必须经过人工确认后才能执行。
- RED 健康状态下不生成新 BUY 草案。

---

## 7. 数据完整性 Gate

### PreScoreGate
评分管线运行前执行的数据验证：
- 行情行数 ≥ 4000（全市场覆盖）
- 交易所覆盖（SSE ≥ 1500, SZSE ≥ 2000）
- 日期新鲜度（相对交易日历 ≤ 2 天）
- 样本股票（茅台/平安/宁德）有有效收盘价
- 复权因子空值率 ≤ 1%
- ST/停牌标签表数据可用

### PostScoreGate
评分管线完成后执行的数据验证：
- score_rank_daily 最新日期 == 目标日期
- 必填字段空值率 ≤ 5%
- 行业空值率 ≤ 2%
- 候选池 ≥ 5000 只
- Top5 候选无 ST/停牌/无收盘价

---

## 8. 配置与发布

### production_strategy.yaml
生产策略的**唯一真实配置源**。
- 定义 primary_strategy、selection_strategy、risk_profile、position_ratio、hold_days 等。
- Git 提交的 YAML 文件内容构成实际部署版本。

### production_acceptance.yaml
策略从研究到生产的**每一级晋升门槛**。
- 覆盖 Release、Strict Ledger、Full History、Rolling OOS、Statistical Robustness、Execution、Shadow、Canary、Scale-Up。

### ReleaseManifest
不可变的生产版本快照：release_id + strategy + config_sha + git_commit + data_snapshot_hash。
- 每次候选导出、订单生成、影子成交都引用此 manifest。

---

## 9. 禁止事项（红线）

1. **未来函数**：任何回测、评分、决策不得使用信号日之后的数据。
2. **策略混淆**：sina 和 chenyiyun 的信号逻辑不得互相依赖。
3. **生产目录移动**：sina/、scoreRank/、chenyiyunSelected/、backtest/、web/、eastmoney/ 不得随意移动。
4. **硬编码密码**：数据库凭据必须从环境变量读取，不得写入源码。
5. **TLS 降级**：飞书 webhook 证书异常必须失败告警，不得降级为不验证连接。
6. **RED 健康状态放行**：健康 RED 时不得生成新 BUY 订单。
7. **研究回测当生产**：研究回测结论不得直接作为生产收益依据。
