# Chenyiyun2087 项目总览（2026）

Chenyiyun2087 是一个面向 A 股量化研究与执行的多模块仓库，覆盖：

- 数据采集（Sina / Eastmoney）
- 评分选股（ScoreRank）
- 盘后策略与回归优化（M2~M8）
- 实盘跟踪（Live Tracker）
- 本地策略与信号生成（chenyiyunSelected）
- 回测引擎（backtest）
- Web 看板与任务运维（Flask + Admin）
- 定时调度（Web 内置调度，`scheduler.py` 当前未启用）

## 文档中心

项目文档已按“总览、策略研究、个股研究、回测归档、实盘记录、外部研报、提示词库”整理：

| 入口 | 说明 |
|---|---|
| `docs/00_project_overview/PROJECT_DIRECTORY.md` | 项目目录说明和文件归档规则。 |
| `docs/00_project_overview/RUNBOOK.md` | 主流程、常用命令和未来函数红线。 |
| `docs/00_project_overview/2026-06-18_external_expert_project_profile.md` | 面向量化/投研外部专家的项目画像与尽调评估材料。 |
| `docs/01_strategy_research/STRATEGY_RESEARCH_INDEX.md` | 策略研究统一入口。 |
| `docs/02_stock_research/STOCK_RESEARCH_INDEX.md` | 个股研究统一入口。 |
| `docs/03_backtest_reports/BACKTEST_INDEX.md` | 回测报告索引。 |
| `docs/06_prompt_library/PROMPT_INDEX.md` | 提示词库索引。 |
| `AGENTS.md` | 后续协作和 agent 默认遵守的项目管理规范。 |

后续新增文件默认遵守 `AGENTS.md` 和 `docs/00_project_overview/PROJECT_DIRECTORY.md`：代码、数据、回测、研究文档、实盘记录、提示词和归档资料分离管理；自动导出结果留在 `exports/`，人工摘要和索引写入 `docs/`。

## 1. 架构总览（按代码实现）

| 层 | 目录/文件 | 说明 | 典型入口 |
|---|---|---|---|
| 调度层 | `web/app.py`、`scripts/ops/`（`scheduler.py` 历史保留） | 交易日判定、持久化作业队列、去重重试、任务状态记录 | `python web/app.py` |
| 数据采集层 | `sina/bs_detection/`、`eastmoney/` | B/S 信号图片抓取与检测、舆情扫描与落库 | `python sina/bs_detection/main.py config_1` |
| 评分层 | `scoreRank/core/`、`scoreRank/strategies/` | 技术因子打分 + Claude 分 + 优化分（opt_score） | `python -m scoreRank.cli.run_daily` |
| 策略评估层 | `scoreRank/cli/`、`web/strategy_playbook.py` | 事件/KPI 构建、M2~M8 参数回归与评估 | `python -m scoreRank.cli.run_m8_cycle` |
| 实盘与信号层 | `sina/live_tracker/`、`chenyiyunSelected/` | 交易记录、持仓快照、本地策略调调仓信号 | `python scripts/ops/run_chenyiyun_daily.py` |
| 回测层 | `backtest/src/` | 通用回测框架 + 策略集成测试 | `pytest backtest/tests` |
| 展示层 | `web/templates/`、`web/app.py` | 监控看板、股票池管理、任务调度配置 | `http://localhost:5001/admin` |

### 1.1 核心业务主线（按代码执行链）

项目当前的主要业务不是单脚本执行，而是围绕 `sina` 与 `chenyiyun` 两条主线持续运转：

| 主线 | 起点 | 中间处理 | 结果落点 | 主要消费方 |
|---|---|---|---|---|
| `sina` 信号主线 | `sina_picture` 抓图 | `sina_analyse` 识别买卖点 | `bs_detection_results` | `sina_score`、M7 卖出、M8 回归 |
| `sina` 评分主线 | `bs_detection_results` + 全市场股票池 | `scoreRank.cli.run_daily` | `score_rank_daily` | Web 股票池、M2~M8、Snapshot |
| `sina` 评估主线 | `score_rank_daily` + 未来收益标签 | `build_b_event_kpi`、`run_m8_cycle` | `b_event_fact`、`b_event_kpi`、`strategy_m8_runs/items` | M4 配置、M7 调仓、绩效复盘 |
| `sina` 实盘主线 | 持仓/成交/行情 | `sina.live_tracker` | `live_positions`、`live_trades`、`live_daily_snapshots` | 实盘日报、净值、快照通知 |
| `chenyiyun` 本地策略主线 | 本地选股规则 | `run_chenyiyun_daily.py` 等 | 自有信号/仓位表 | 日常调仓、涨停检查、仓位更新 |

### 1.2 策略边界说明（重要）

本项目中 **`sina` 策略** 与 **`chenyiyun` 策略** 是两套独立策略体系，不是同一策略的不同别名：

- **sina 策略体系**
  - 主要目录：`sina/`、`scoreRank/`、`web/strategy_playbook.py`
  - 核心能力：B/S 检测、M2~M8 评估、M7 调仓规则、Sina 实盘跟踪
  - 典型任务：`sina_picture`、`sina_analyse`、`sina_score`、`sina_m8`、`sina_m7_sell`、`sina_snapshot`

- **chenyiyun 策略体系**
  - 主要目录：`chenyiyunSelected/`、`scripts/ops/run_chenyiyun_*.py`
  - 核心能力：本地化选股、日/周调仓信号生成、涨停检查、仓位更新
  - 典型任务：`chenyiyun_selected`、`chenyiyun_weekly_rebalance`、`chenyiyun_limitup_check`、`chenyiyun_position_update`

- **共享但不混用的部分**
  - 共享基础设施：MySQL、交易日历、Web 管理台、部分持仓表
  - 不共享策略决策逻辑：信号生成、调仓规则、评估口径分别独立维护

## 2. 调度系统（三阶段自动化）

项目当前以 `web/app.py` 内置调度为唯一生效入口；`scheduler.py` 仅保留作历史参考，不参与生产调度。

### 2.1 三阶段日内调度流水线

| 阶段 | 时间 | 关键任务 | 脚本/命令 |
|---|---|---|---|
| **一阶段：晨间准备** | 08:00 | 交易日历同步 | `scripts/ops/sync_trade_cal.py` |
| | 09:05 | 信号强度检查 | `scripts/ops/run_chenyiyun_signal_check.py` |
| | 09:30 | 周度调仓（周一）| `scripts/ops/run_chenyiyun_weekly_rebalance.py` |
| **二阶段：盘中/收盘后** | 14:00 | 涨停状态检查 | `scripts/ops/run_chenyiyun_limitup_check.py` |
| | 15:20 | Sina 批量截图（`sina_picture`） | `sina/bs_detection/main.py --capture-only` |
| | 16:10 | Sina 买卖点分析（`sina_analyse`） | `sina/bs_detection/main.py --analyze-only` |
| | 16:30 | 舆情扫描 | `eastmoney/main.py` |
| **三阶段：夜间处理** | 21:00 | 全A股评分流水线 | `run_pipeline()` (含 Pipeline 内子任务) |
| | 21:10 | M8 回归与仓位更新 | `run_m8_cycle.py` / `run_chenyiyun_position_update.py` |
| | 21:30 | 实盘快照同步 | `run_live_tracker.py snapshot` |

### 2.2 调度器说明

- **Web 内置调度 (`web/app.py`)**: 当前生产调度入口；定时与手动请求统一进入持久化作业队列，同任务同业务日期合并，失败自动重试一次，并保留锁、依赖等待和执行历史。
- **独立调度器 (`scheduler.py`)**: 当前未启用，仅保留代码与日志结构供排查/回溯。

### 2.3 交易日门禁规则（2026-02-27 更新）

- 所有定时任务在触发执行前，统一查询 `chenyiyun.dim_trade_cal`（`exchange='SSE'`）判断是否交易日。
- 若当日非交易日：任务不执行业务脚本，直接按 `Success` 记账并切日（`switched_day=True`）。
- Web 调度会写入 `app_task_history`，消息包含 `reason=NON_TRADING_DAY`，便于审计。

## 3. 核心模块详解

### 3.1 chenyiyunSelected (本地策略与信号)
- **职责**: 维护“陈依云”系列量化策略，生成每日买卖信号。
- **关键脚本**:
    - `run_chenyiyun_daily.py`: 信号生成入口，自动推断总资产并计算仓位。
    - `run_chenyiyun_signal_check.py`: 早盘信号确认。
    - `run_chenyiyun_limitup_check.py`: 盘中涨停监控。

### 3.2 ScoreRank (评分引擎)
- **定位**: `ScoreRank` 是 `sina` 策略体系的评分与研究中台，负责“全市场打分 -> 候选池落库 -> 事件收益构建 -> M2~M8 回归 -> 调仓决策支持”。
- **主入口**: `python -m scoreRank.cli.run_daily`
- **评分输出不是单一分数**:
  - `score`: 技术总分，0~100
  - `opt_score`: 分类因子优化分，通常约 0~10
  - `claude_score`: AI 六维分，0~100
- **M8 回归优化**: 对 M1 (事件) 与 KPI (收益) 进行自动化评估与回测。
  - **增量构建**: `build_b_event_kpi.py` 支持增量更新，大幅提升每日处理性能。
  - **风险量化**: 评估体系引入 **夏普比率 (Sharpe Ratio)** 与 **最大回撤 (MDD)**。
  - **参数搜索**: `run_m8_cycle.py` 自动执行网格搜索并持久化冠军参数。

#### 3.2.1 评分主调用链（按 `run_daily.py`）

| 步骤 | 输入 | 核心处理 | 输出 |
|---|---|---|---|
| 1. 候选收集 | `bs_detection_results`、`a_share_stock_list`、`sina/stock_codes.xlsx` | 合并 B/S 股票、自选股、全市场活跃股票，形成 `all_symbols` | 待评分股票集合 |
| 2. 行情取数 | `tushare_stock.dwd_stock_daily_standard`、`tushare_stock.dwd_daily` | 拉取约 `lookback_days*2` 天历史行情，分别用于技术特征和流动性 | QFQ 行情、RAW 行情 |
| 3. 技术评分 | `TechnicalScorer` | 生成 `score/base_score/penalty` 及各技术分项 | 技术评分结果 |
| 4. AI 评分 | `ClaudeScorer` | 补充 `claude_score` 供展示和 M2~M8 使用 | AI 评分结果 |
| 5. 市场增强 | 当日特征 + `bs_detection_results` | 回填 `buy_point_close`、`close_price`、`is_limit_up`、`price_change_ratio` | 增强后的评分表 |
| 6. 因子优化分 | `AShareDataCenter` 因子分类表 | 计算 `opt_score` 并按股票代码 merge | 三分体系结果 |
| 7. 业务标签 | B/S 集合、自选集合、技术总分 | 计算 `is_bs_candidate`、`is_self_selected`、`pool_type` | 最终落库结果 |
| 8. 落库 | 最终评分表 | 覆盖写入 `score_rank_daily` | Web 展示、M8、M7、实盘 |
| 9. 综合建议批量化 | `score_rank_daily` 当日记录 | 复算并写回 `bs_score/bs_score_v2/bs_research_score/bs_consensus_score` 与建议文案 | `/sina/scores` 稳定排序与展示 |

#### 3.2.2 技术分 `score` 的数据处理流程

`TechnicalScorer` 的实现位于 `scoreRank/strategies/technical.py`，具体打分逻辑位于 `scoreRank/core/scorer.py`。

1. 输入行情

- 前复权行情：`tushare_stock.dwd_stock_daily_standard`
- 原始行情：`tushare_stock.dwd_daily`
- 股票名称：`tushare_stock.dim_stock`

2. 构建特征

| 特征类别 | 关键字段 | 说明 |
|---|---|---|
| 均线趋势 | `ma5/ma10/ma20/ma60`、`ma20_slope` | 判断趋势和多头排列 |
| 突破 | `hh_n`、`is_breakout`、`breakout_dist` | 判断是否突破最近 N 日高点 |
| 量能 | `vol_ma5`、`vol_ratio` | 衡量放量强度 |
| 相对强弱 | `ret20`、`rs20` | 使用横截面中位数构建相对强弱 |
| 收敛与波动 | `std5/std20`、`contraction` | 越收敛越高分 |
| 乖离 | `bias_ma20` | 乖离越大越不利于追涨 |
| 流动性 | `avg_amount20`、`avg_price20` | 对小成交额标的压分 |
| 风险项 | `suspended_recent_flag`、`limit_up_lock_flag` | 用于最终 penalty 扣分 |
| 筹码健康 | `chip_healthy` | `raw_close > avg_price20` 视为健康 |

3. 技术分分项规则

| 分项 | 字段 | 当前规则 |
|---|---|---|
| 趋势分 | `s_trend` | `trend_ok` 为 1 记 100，否则 0 |
| 多头排列分 | `s_bull_align` | `bull_align` 为 1 记 100，否则 0 |
| 突破分 | `s_breakout` | 将 `breakout_dist` 映射到 `0.003~0.06` 后做横截面百分位 |
| 放量分 | `s_volume` | 将 `vol_ratio` 映射到 `1.0~2.5` 后做横截面百分位 |
| 温和放量分 | `s_vol_mild` | 以 `1.5` 为中心、`0.8` 为半区间做中心型打分 |
| 相对强弱分 | `s_rs` | `rs20` 的横截面百分位 |
| 收敛分 | `s_contraction` | `contraction` 的反向百分位，越小越好 |
| 乖离分 | `s_bias` | 绝对乖离以 `5%` 为上限，越小分越高 |
| 筹码分 | `s_chip` | `chip_healthy` 为 1 记 100，否则 0 |
| 流动性分 | `s_liquidity` | `avg_amount20` 百分位，低于门槛时乘 `0.3` 压分 |

4. 技术总分权重

| 分项 | 权重 |
|---|---|
| `trend` | 0.12 |
| `bull_align` | 0.08 |
| `breakout` | 0.22 |
| `volume` | 0.12 |
| `vol_mild` | 0.04 |
| `rs` | 0.12 |
| `contraction` | 0.10 |
| `bias` | 0.07 |
| `chip` | 0.03 |
| `liquidity` | 0.10 |

技术总分先得到 `base_score`，再减去风险扣分：

- 停牌风险：40
- 涨停锁死：20
- 名称含 `ST`：25
- 重大利空：15

最终：

```text
score = clip(base_score - penalty, 0, 100)
```

补充说明：

- `trigger_today = trend_ok == 1 and is_breakout == 1`
- `trade_threshold = 75`
- `watch_threshold = 60`
- `min_avg_amount20 = 50,000,000`

#### 3.2.3 因子优化分 `opt_score`

`opt_score` 来自 `AShareDataCenter/score/factor_optimizer/data_loader.py`，不是 `score` 的副本。

上游分类因子来源：

- `dws_momentum_score`
- `dws_value_score`
- `dws_quality_score`
- `dws_technical_score`
- `dws_capital_score`
- `dws_chip_score`
- `dwd_daily_basic`（使用 `circ_mv` 推导 `size`）

当前权重：

| 分类因子 | 权重 |
|---|---|
| `momentum` | 0.15 |
| `value` | 0.05 |
| `quality` | 0.05 |
| `technical` | 0.25 |
| `capital` | 0.25 |
| `chip` | 0.15 |
| `size` | 0.10 |

说明：

- `opt_score` 一般在 `0~10` 区间
- 若 Optimizer 导入失败或运行异常，会回退为 `score / 10`
- 若当日分类因子表为空，当前实现会将 `opt_score` 记为 `NULL`

#### 3.2.4 AI 分 `claude_score`

`claude_score` 由 `scoreRank/strategies/claude.py` 生成，包含六个维度：

- 动量 `score_momentum`
- 估值 `score_value`
- 质量 `score_quality`
- 技术 `score_technical`
- 资金 `score_capital`
- 筹码 `score_chip`

其作用不是替代技术分，而是为：

- Web 多维筛选
- M2/M3 参数比较
- M4/M7 融合投票

提供第二套独立评分视角。

#### 3.2.5 候选池划分与落库字段

评分完成后，`run_daily` 会补齐业务标签：

| 字段 | 规则 |
|---|---|
| `is_bs_candidate` | 是否属于 `bs_detection_results` 最新有效买点集合 |
| `is_self_selected` | 是否属于自选股 DB 或 `sina/stock_codes.xlsx` |
| `pool_type=TRADE` | `is_bs_candidate=1` 且 `score >= 75` |
| `pool_type=WATCH` | `is_bs_candidate=1` 且 `60 <= score < 75` |

最终写入 `score_rank_daily` 的核心字段包括：

- 基础信息：`trade_date`、`symbol`、`name`
- 技术评分：`score`、`base_score`、`penalty`
- 技术分项：`s_trend`、`s_breakout`、`s_volume`、`s_rs`、`s_contraction`、`s_liquidity`
- 增强字段：`buy_point_close`、`close_price`、`is_limit_up`、`price_change_ratio`
- 组合评分：`opt_score`、`claude_score`
- 业务标签：`pool_type`、`is_self_selected`、`is_bs_candidate`

#### 3.2.6 评分结果如何流向 M2~M8

评分落库不是终点，而是后续策略评估的输入层：

- `build_b_event_kpi.py` 从 `score_rank_daily` 构建 `b_event_fact` 与 `b_event_kpi`
- `evaluate_m2_presets`、`evaluate_m3_optimizer` 主要消费 `score/opt_score/claude_score + ret_3/5/10`
- `evaluate_m4_allocation` 用三套投票规则融合出 `m4_score`
- `evaluate_m7_rebalance` 再用 `m4_score + 当前实盘仓位` 生成调仓清单

M4 当前融合口径：

```text
vote_pyramid  = score > 60 and claude_score > 50
vote_weighted = 0.4*score + 0.3*(opt_score*10) + 0.3*claude_score >= 65
vote_quadrant = opt_score >= 6 and claude_score >= 50 and score > 60

consensus = vote_pyramid + vote_weighted + vote_quadrant
m4_score  = 0.35*score + 0.25*(opt_score*10) + 0.30*claude_score + 10*consensus
```

#### 3.2.7 M2~M8 逻辑链路（输入/处理/输出）

| 阶段 | 主要输入 | 核心处理 | 主要输出 | 代码入口 |
|---|---|---|---|---|
| M2 预设回归 | `b_event_fact + b_event_kpi`（仅 `is_eligible=1`） | 固定三套策略（Pyramid/Weighted/Quadrant）对比，计算 `avg_ret/hit/mdd/sharpe` | 预设策略效果排名 | `web.strategy_playbook.evaluate_m2_presets` |
| M3 参数优化 | 同 M2 | 对三类策略家族做网格搜索，按 `avg_ret_10 + hit_10` 选每家族冠军参数 | 各家族冠军参数与绩效 | `web.strategy_playbook.evaluate_m3_optimizer` |
| M4 组合分配 | M1 样本 + M2/M3 共识逻辑 | 融合三家族投票构建 `m4_score`，选 TopN 并分配权重 | 目标持仓与权重 | `web.strategy_playbook.evaluate_m4_allocation` |
| M5 滚动验证 | 最近 N 个 `event_date` 样本 | 按滚动窗口重复 M4，观察收益与命中稳定性 | 窗口级统计与离散度 | `web.strategy_playbook.evaluate_m5_rolling` |
| M6 净值回测 | M4 每日选股结果 | 叠加成本/滑点，计算 `gross_nav/net_nav` 与回撤 | 净值曲线、净收益、最大回撤 | `web.strategy_playbook.evaluate_m6_nav` |
| M7 调仓生成 | M4 目标仓位 + 当前实盘仓位 | 生成模拟买卖单（金额/股数/方向/命令） | 调仓指令清单 | `web.strategy_playbook.evaluate_m7_rebalance` |
| M8 周期任务 | 最近 `lookback_dates` 的 M1 样本（默认 60） | 先做上游新鲜度门禁，再执行 M2/M3 并落库 | `strategy_m8_runs` + `strategy_m8_items` | `python -m scoreRank.cli.run_m8_cycle` |

- `sina_m8` 任务的上游门禁：若 `bs_detection_results` 最新日期领先 `score_rank_daily`，会提示“上游任务未执行完成，不能执行 M8”，并以非零退出码中止，避免假成功。
- Web 页面分工：`/sina/strategy/m2`、`/sina/strategy/m3` 主要做在线展示与分析；`sina_m8` 任务负责周期落库与可审计追踪。

#### 3.2.8 M7 模拟调仓执行口径（2026-02-27）

- M7 是“**每日评估（仅交易日）**、**按条件交易**”机制，不是“每日必有交易”机制。
- 交易日内每次运行都会基于 `M4目标仓位 + 当前实盘仓位` 重新计算调仓单；若无触发条件，`orders_total=0`，当天不下单。
- 普通调仓触发条件：`|target_weight - current_weight| >= min_trade_weight`。
- 强制卖出触发条件（优先级最高）：
  - `B/S反转卖出`：`latest_sell_date >= latest_buy_date`；
  - `硬止损`：`current_price <= avg_cost * (1 - stop_loss_pct)`。
- 强制卖出会直接清仓当前持股，不受 `min_trade_weight` 限制，且不做 100 股取整。
- 非强制调仓按金额差换算股数，按 100 股取整；结果按“先卖后买”排序，优先释放资金。
- 卖出信号会同步落库到 `m7_sell_signals`（`FORCED_EXIT` / `REBALANCE`），用于审计与复盘。

### 3.3 数据处理流程（从信号到实盘）

| 步骤 | 关键表/文件 | 说明 |
|---|---|---|
| 1. 信号采集 | `sina/bs_detection/`、`bs_detection_results` | 新浪页面截图、OCR 识别买卖点并写库 |
| 2. 股票池拼装 | `a_share_stock_list`、`sina/stock_codes.xlsx` | 合并全市场、自选股、B/S 股票，形成评分全集 |
| 3. 全市场评分 | `scoreRank.cli.run_daily`、`score_rank_daily` | 计算 `score/opt_score/claude_score` 并落库 |
| 4. 事件收益构建 | `build_b_event_kpi.py`、`b_event_fact`、`b_event_kpi` | 生成未来 3/5/10 日收益与命中标签 |
| 5. 参数回归 | `run_m8_cycle.py`、`strategy_m8_runs/items` | 评估 M2/M3 并输出冠军参数与样本统计 |
| 6. 组合与调仓 | `web.strategy_playbook`、`m7_sell_signals` | 生成 `m4_score` 和调仓建议 |
| 7. 实盘快照 | `sina/live_tracker/`、`live_daily_snapshots` | 汇总持仓、现金、成交和收益，生成盘后总结 |

### 3.4 Sina & Live Tracker (实盘监控)
- **B/S 扫描**: 全自动截图 + Tesseract OCR 识别新浪财经买卖信号。
- **Live Tracker**: 实盘流水审计、持仓估值与每日建档快照。

### 3.5 Web 控制台 (`web/`)
- **Dashboard**: 策略绩效、信号趋势、持仓分布可视化。
- **Monitor**: B/S 信号最新监控、当日汇总、技术因子热力图。
- **Admin**: 任务锁状态管理、调度配置更新、手动录入成交单。

## 4. 目录结构

```text
Chenyiyun2087/
├── scheduler.py                 # 历史保留（当前未启用）
├── web/                         # Flask Web 看板与调度管控
├── scripts/ops/                  # 业务运维脚本 (陈依云信号、日历同步等)
├── sina/                        # Sina B/S 数据流与实盘同步
├── scoreRank/                   # 评分核心、KPI构建与 M8 管道
├── chenyiyunSelected/           # 核心策略实现与信号生成器
├── eastmoney/                   # 舆情分析与盘后辅助策略
├── backtest/                    # 统一回测引擎
└── logs/                        # 各模块执行日志
```

## 5. 常用命令

```bash
# 1. 启动 Web 管理台
python web/app.py

# 2. 运行陈依云每日信号生成 (Web 友好封装)
python scripts/ops/run_chenyiyun_daily.py

# 3. 手动触发全 A 股评分
python -m scoreRank.cli.run_daily

# 4. 执行 M8 策略回归优化 (支持增量)
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60

# 5. 手动重建 B-Event KPI 基础数据 (增量模式下跳过已有数据)
python -m scoreRank.cli.build_b_event_kpi --all  # 使用 --all 强制全量重建

# 6. 实盘持仓同步
python sina/live_tracker/run_live_tracker.py sync
```

## 6. 注意事项
- 修改调度时间以 `web/app.py` 的任务字典为准（`scheduler.py` 当前未启用）。
- 实盘同步（Snapshot）前需确保行情数据已在 ODS 层落库完成。

## 7. 任务完成通知系统（新增）

### 7.1 Web Console 配置入口

- 入口页面：`/admin`（后台任务中心）
- 配置区域：`消息通知渠道`
- 支持渠道：
  - 飞书（Feishu）
  - 企业微信（Wechat）
  - 钉钉（Dingtalk）
  - 自定义 Webhook
- 生效规则：仅对“已启用 + URL 合法（http/https）”的渠道发送通知。

> 测试环境默认预填飞书 Webhook（可在后台覆盖）：
> `https://open.feishu.cn/open-apis/bot/v2/hook/a8374c19-3620-4891-8c7a-df6885229607`

### 7.2 调用链（任务完成后通知）

统一调用链如下（位于 `web/app.py`）：

1. 任务执行线程：`_execute_locked_task(...)`
2. 写入任务历史：`_insert_task_history(...)`
3. 触发通知总入口：`_send_task_completion_notification(...)`
4. 构建任务摘要：`_build_task_completion_notification(...)`
5. 多渠道分发：`_dispatch_task_notification(...)`
6. 单渠道发送：`_post_channel_webhook(...)`

### 7.3 触发条件

- 仅在任务 **执行成功**（`history_status == "Success"`）后触发通知。
- 当前仅对以下任务启用通知：
  - `sina_analyse`
  - `sina_m8`
  - `sina_snapshot`

### 7.4 业务功能（按任务）

#### A) `sina_analyse` 完成通知

- 目标：通知“分析完成”，并给出当日 B/S 统计结果。
- 摘要字段：
  - 分析日期（`batch_date`）
  - 覆盖股票数
  - 买点信号数 / 卖点信号数 / 双向信号数
  - 数据更新时间
  - 买点示例代码（最多 8 条）
  - 卖点示例代码（最多 8 条）
- 数据来源：`bs_detection_results`

#### B) `sina_m8` 完成通知

- 目标：通知“M8 已完成”，并给出“今天调仓结果”（卖出侧）。
- 摘要字段（M8 本体）：
  - `run_id`、`as_of_date`、`status`
  - `lookback_dates`、样本行数、可交易样本、搜索组合数、结果条目数
  - M3 冠军参数摘要（最多 3 条）
- 调仓结果字段（卖出侧）：
  - 今日卖出单总数
  - 强制卖出数
  - 再平衡卖出数
  - 挂起单数（`pending`）
  - 预计卖出总金额
- 数据来源：`strategy_m8_runs`、`strategy_m8_items`、`m7_sell_signals`

#### C) `sina_snapshot` 完成通知

- 目标：通知“实盘快照已完成”，并给出当日实盘总结。
- 摘要字段：
  - 快照日期
  - 总权益、现金、持仓市值
  - 当日盈亏、当日收益率、沪深300收益率、超额收益率
  - 当前持仓数量
  - 当日成交汇总（买入笔数/金额、卖出笔数/金额）
- 数据来源：`live_daily_snapshots`、`live_positions`、`live_trades`

### 7.5 消息样式（模板）

统一消息头：

```text
【任务完成】<任务显示名>
任务ID：<task_name>
触发方式：manual/schedule
开始时间：YYYY-MM-DD HH:MM:SS
完成时间：YYYY-MM-DD HH:MM:SS
```

任务摘要正文按任务类型拼接（见 7.4）。

渠道适配：

- 飞书：`msg_type=text`
- 企业微信：`msgtype=markdown`
- 钉钉：`msgtype=markdown`（含标题）
- 自定义：默认 `{text, content}` JSON

## 8. 评分公式（2026-03）

本节给出当前代码实现中的核心评分公式，便于联调、核对与回归测试。

### 8.1 Technical 总分（`score`）

TechnicalScorer 的总分由 10 个分项加权求和后再扣减风险惩罚：

$$
base\_score = 0.12\,s_{trend}+0.08\,s_{bull\_align}+0.22\,s_{breakout}+0.12\,s_{volume}+0.04\,s_{vol\_mild}+0.12\,s_{rs}+0.10\,s_{contraction}+0.07\,s_{bias}+0.03\,s_{chip}+0.10\,s_{liquidity}
$$

$$
penalty = 40\,I_{suspended}+20\,I_{limit\_up\_lock}+25\,I_{ST}+15\,I_{negative\_news}
$$

$$
score = \mathrm{clip}(base\_score-penalty,\,0,\,100)
$$

其中关键分项定义：

- `s_trend = 100 * trend_ok`
- `s_bull_align = 100 * bull_align`
- `s_breakout = pct_rank_100(is_breakout * clip((breakout_dist-0.003)/(0.06-0.003),0,1))`
- `s_volume = pct_rank_100(clip((vol_ratio-1.0)/(2.5-1.0),0,1))`
- `s_vol_mild = 100 * clip(1-|vol_ratio-1.5|/0.8,0,1)`
- `s_rs = pct_rank_100(rs20)`
- `s_contraction = 100 - pct_rank_100(contraction)`
- `s_bias = 100 * (1-clip(|bias_ma20|/0.05,0,1))`
- `s_chip = 100 * I(raw_close > avg_price20 > 0)`
- `s_liquidity = pct_rank_100(avg_amount20)`，若 `avg_amount20 < 50,000,000` 则再乘 `0.3`

触发信号定义：

$$
trigger\_today = I(trend\_ok=1\ \land\ is\_breakout=1)
$$

### 8.2 Claude 六维分（`claude_score`）

ClaudeScorer 使用六维评分，总分 100 分封顶：

$$
claude\_score = \mathrm{clip}(S_{momentum}+S_{value}+S_{quality}+S_{technical}+S_{capital}+S_{chip},\,0,\,100)
$$

各维上限：

- 动量 `S_momentum`：25 分（`ret_5(5) + ret_20(6) + ret_60(7) + vol_ratio(4) + turnover_rate_f(3)`，高分位更优）
- 价值 `S_value`：20 分（`PE(7)+PB(7)+PS(6)`，低分位更优）
- 质量 `S_quality`：20 分（`ROE(8)+gross_margin(6)+debt_to_assets(6)`，其中负债率低更优）
- 技术 `S_technical`：15 分（MACD/RSI/KDJ/CCI/BIAS 规则打分）
- 资金 `S_capital`：10 分（`big_order_flow(6)+margin_ratio(4)`，高分位更优）
- 筹码 `S_chip`：10 分（`winner_rate` 三角偏好 6 分 + `close/cost_50pct` 区间分 4 分）

其中：

$$
S_{chip,winner}=6\cdot clip\left(1-\frac{|winner\_rate-30|}{30},0,1\right)
$$

`close/cost_50pct` 加分规则：`>1.10 -> 4`，`>1.03 -> 2.5`，`>0.97 -> 1`，否则 `0`。

### 8.3 因子优化分（`opt_score`）

当 Factor Optimizer 可用时：

$$
opt\_score = 0.15\,momentum + 0.05\,value + 0.05\,quality + 0.25\,technical + 0.25\,capital + 0.15\,chip + 0.10\,size
$$

当 Optimizer 不可用时，回退为：

$$
opt\_score = score / 10
$$

### 8.4 `score_rank_daily` 字段口径说明

- 默认 `run_daily --strategy technical`：`score` 为 Technical 总分；`claude_score` 为并行计算后的 Claude 六维总分；`opt_score` 为因子优化分。
- 若 `run_daily --strategy claude`：`score` 即 Claude 六维总分，同时 `claude_score = score`。

### 8.5 B点增强分（`bs_score`）

`bs_score` 只用于已经出现 B 点的候选股排序，不替代全市场 Technical 总分。当前口径：

$$
bs\_score = 0.15\,score + 0.30\,(10 \cdot opt\_score) + 0.25\,claude\_score + 0.15\,s\_{rs} + 0.05\,s\_{breakout} + 0.10\,bs\_{entry} - penalty
$$

`bs_entry_score` 描述买点后节奏：买点后小幅确认（约 0%~8%）更高，明显破位或过度追高会降分；当日涨停、买点后涨幅过大或跌幅过深会触发额外扣分。

### 8.6 B点增强分 V2 与专家数据包

`bs_score_v2` 是面向 B 点信号增强的保守排序分，重点提高相对强弱、流动性、突破放量和多源评分一致性的权重，并对涨停锁死、买点后过度追高、明显破位和评分分歧做扣分。默认标签：

- `强买`: `bs_score_v2 >= 72`
- `观察`: `58 <= bs_score_v2 < 72`
- `剔除`: `bs_score_v2 < 58`

`bs_research_score` 是 2026 年以来样本研究后的页面提示层，核心规则来自 `bs_score_v2` 与 `rs_liquidity_combo` 的共振。它的标签是 `强观察` / `普通观察` / `回避`，用于辅助复核，不等同于自动交易指令。

当 `score_rank_daily` 中存在市场环境字段时，`bs_research_score` 会做市场感知校正：指数 20 日涨幅过高时降低追高型 B 点，弱市中仍保持强势流动性的标的会获得额外确认。

外部专家协作数据通过以下脚本导出：

```bash
python3 scripts/export_signal_enhancement_dataset.py
```

导出目录位于 `exports/signal_enhancement/<timestamp>/`，包含首次 B 点事件、1/3/5/10/20/60 日标签、60 日价格路径、活跃 B 点日面板、最新候选池、特征白名单、质量报告和 Excel 汇总包。训练特征只应从 `feature_whitelist.json` 读取，避免未来收益字段泄漏到模型。

数据包同时包含市场环境特征：沪深300当日与近 5/20 日表现、当日 B 点拥挤度、市场涨停率、市场平均 V2/研究分，以及 `market_regime`。这些字段用于解释行情阶段差异，避免模型把市场环境误学成个股质量。

新版数据包还会尝试接入 AShareDataCenter 的同日可见 ADS/DWS/ODS 因子，并统一以 `adc_*` 前缀输出，包括技术形态、资金流、融资情绪、筹码、流动性、风险、综合评分、前复权技术指标和 B/S 信号确认字段。若本地库缺少部分 AShareDataCenter 表或字段，导出会保留空列并继续完成，训练仍以 `feature_whitelist.json` 为准。

基线模型训练入口：

```bash
python3 scripts/train_bs_signal_model.py --dataset-dir exports/signal_enhancement/<timestamp> --target hit_20_10pct
```

模型产物位于 `exports/bs_signal_models/<timestamp>/`，包括校准后的 Logistic 模型、验证/测试指标、模型报告和最新候选股概率排序。

模型分写回页面入口：

```bash
python3 scripts/import_bs_model_scores.py --model-dir exports/bs_signal_models/<timestamp>
```

写回后，`/sina/scores` 会展示：

- `综合分`: 规则研究分、模型概率、V2 分的融合排序分。
- `综合建议`: `共振观察` / `谨慎观察` / `模型分歧` / `回避`。
- `综合原因`: 展示触发共振、风险扣分或模型规则分歧的主要原因。
- `模型分` 与 `模型概率`: 最新训练模型对当前候选池的排序结果。
- `市场`: 当前候选对应的市场环境标签。

日终复算并写回页面排序字段：

```bash
python3 scoreRank/cli/build_bs_consensus.py
```

可指定日期重跑：

```bash
python3 scoreRank/cli/build_bs_consensus.py --date 20260508
```

完整闭环入口：

```bash
python3 scripts/run_bs_signal_enhancement_cycle.py --target hit_20_10pct --model-kind all
```

该脚本会依次完成“专家数据包导出 -> 研究报告生成 -> 基线模型训练 -> 最新候选模型分入库”，并在 `exports/bs_signal_cycles/<timestamp>/cycle_manifest.json` 留存本轮数据包、研究报告、模型目录、入库结果和核心测试集指标。

月度自动闭环入口：

```bash
python3 scripts/ops/run_monthly_bs_signal_enhancement_cycle.py
```

该入口按“当月首个交易日”守卫执行；Web Admin 中的 `B点模型月度闭环` 任务默认 21:45 触发，但非当月首个交易日或当月已完成时只记录跳过，不重复训练。手动指定日期重跑可使用：

```bash
python3 scripts/ops/run_monthly_bs_signal_enhancement_cycle.py --date 20260511 --force
```

当前样本仍以 2026 年以来的短期历史为主，20 日标签样本量有限。已验证更复杂的树模型在当前样本上不稳定，校准 Logistic 暂时是更稳的基线。后续应随着每日新增 B 点事件持续重跑闭环；当 20 日有效标签达到约 1500 条、60 日有效标签达到约 500 条后，再重新评估分市场阶段模型、非线性模型和分行业模型。
