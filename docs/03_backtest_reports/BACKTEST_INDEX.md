# 回测报告索引

> 2026-06-20 strict 研发闭环升级：新增版本化公司行为快照、配股全额认购/资金不足冻结状态机、精确拒单原因独立 replay、偏离归因扩展及可靠性矩阵脚本。生产默认与 `research_shadow_candidate.enabled=false` 均未改变；在 clean 全历史证据与所有门槛通过前，strict 继续不可晋级。

> 2026-06-20 strict 独立执行 replay：commit `b4e2655e` 的 clean-worktree 证据包完成 15 笔逐订单价格、费用、T+1 gate、时间顺序与守恒审计，均为 0 差异。真实执行失败残差为 0，但价格缓冲残差约 63,041、P95 权重偏离约 298.95bps，且公司行为仍 `PARTIAL_UNVERIFIED`；状态继续为 `CAUSAL_BUT_LEDGER_UNVERIFIED`。详见 `2026-06-20_strict_执行重放与偏离证据清单.md`。

> 2026-06-20 strict ledger 独立重放：提交 `a71f1c23` 的 clean worktree 烟测完成 15 个订单事件重放，订单守恒、事件重放与 ledger-vs-NAV 误差均为 0bp；但公司行为覆盖仍为 `PARTIAL_UNVERIFIED`，风险漏判为 2，现金与权重偏离超阈值，验收固定为 `CAUSAL_BUT_LEDGER_UNVERIFIED`、`promotion_enabled=false`。详见 `2026-06-20_strict_ledger_replay_证据清单.md`。

> 2026-06-20 strict 唯一账本烟测：strict 预提交订单、T+1 成交/拒单/取消和公司行为事件已收敛至 `ExecutionLedger`，T+1 错成交数为 0。但烟测工作区为 dirty、公司行为覆盖仍为 `PARTIAL_UNVERIFIED`，验收状态固定为 `CAUSAL_BUT_LEDGER_UNVERIFIED`，`promotion_enabled=false`；不产生全历史绩效或晋级结论。详见 `2026-06-20_strict_ledger_唯一账本烟测摘要.md`。

> 2026-06-19 strict precommit uplift 首轮账户级回测：`production_governed_vol_position_v1_2b_strict_precommit_uplift` 完成 2023-11-30 至 2026-06-18 的 616 日 T 日预提交/T+1 原始开盘固定股数路径。cap 输入覆盖率 100%、缺失回退 0 天，normal/high/extreme 为 91/205/318 日；表面结果为收益 +62.12%、年化 +21.89%、最大回撤 -26.40%。但当前执行账本缺少复权因子和公司行为调整，且平均现金残差 47.63%、最大开盘权重偏离 2,330.93bps，收益不可与 v1 严谨比较。结论：研究路径通过特征/因果审计，**不通过 production、shadow 或 canary 晋级**；先补齐执行基础数据后重跑。详见 `2026-06-19_strict_precommit_uplift_回测摘要.md`。

> 2026-06-18 governed 矩阵验收更新：`production_governed_vol_position` 已作为当前生产默认底座，主选股引擎仍为 `baseline_full_liquidity_detail_vol_position`，风险总闸使用 `production_risk_governor`。2023-01-04 至 2026-06-17 三年 T+1 账户级回测收益 +19.94%、年化 +7.75%、最大回撤 -24.81%、`missed_risk_events=0`；`adaptive_market_style` 同期收益 +44.91%、年化 +16.44%、最大回撤 -26.68%，资本效率更强但先保留为挑战者和风险锚。`production_governed_adaptive_pattern_guard` 三年收益 -11.79%、最大回撤 -52.06%、`missed_risk_events=6`，不得进入生产候选。

> 2026-06-18 governor v2 研究更新：`production_governed_vol_position_v2` 引入 `soft_reduce / hard_reduce` 分层后，三年收益 -1.80%、年化 -0.74%、最大回撤 -29.29%、`missed_risk_events=20`；虽然 false positive reduce days 从 132 降到 122，但风险收益显著恶化，不能进入生产候选。当前生产默认继续保持 `production_governed_vol_position`。

> 2026-06-18 v1.1 selective recovery 更新：`production_governed_vol_position_v1_1_recovery` 三年收益 +41.89%、年化 +15.44%、最大回撤 -25.65%，最差 20 日 -16.65%，平均仓位 59.17%；但 `missed_risk_events=8`，false positive reduce days 仅从 132 降到 118，未达到“missed_risk_events<=0、误降仓下降至少15%”门槛。结论：v1.1 是强观察候选，不进入当前生产默认。

> 2026-06-18 v1.2 missed-risk 清零研究更新：新增 `production_governed_vol_position_v1_2_recovery` 和 pattern veto 版本，首版参数为 `champion_score_floor=-0.03`、`recovery_position=0.58`、`nav_ret_10d_kill=-0.04`、`nav_dd_20d_kill=-0.08`、`max_recovery_streak=5`。三年结果与当前生产 v1 完全一致：收益 +19.94%、年化 +7.75%、最大回撤 -24.81%、`missed_risk_events=0`、`false_positive_reduce_days=132`、`recovery_days=0`。结论：v1.2 首版安全但过度保守，未达到收益提升和误降仓下降目标，不进入生产候选。

> 2026-06-18 v1.2b dynamic score 研究更新：`production_governed_vol_position_v1_2b_dynamic_score` 使用 `negative_recent_champion` 历史分位数/z-score 做选择性恢复，三年收益 +57.23%、年化 +20.41%、最大回撤 -25.52%、平均仓位 56.64%、`missed_risk_events=0`、`recovery_days=59`。但 `false_positive_reduce_days=122` 未达 <=112，最差 20 日 -16.82% 略低于 -16.8% 门槛，因此暂列强观察候选，不替换当前生产默认。

> 2026-06-19 v1.2b gate tuned 边界精修更新：新增 `production_governed_vol_position_v1_2b_gate_tuned` 与 pattern veto 版本，生产默认仍保持 `production_governed_vol_position`。首版 gate tuned 参数 `nav_dd_20d_kill=-0.075`、`max_recovery_streak=5`、`top_industry_weight_limit=0.48` 未改变实际路径，三年收益 +57.23%、年化 +20.41%、最大回撤 -25.52%、`missed_risk_events=0`、`false_positive_reduce_days=122`、最差 20 日 -16.82%，仍未过硬门槛。false-positive gap 显示 122 天中 benign 56、dangerous 34、borderline 32；局部网格首组把误降仓降至 111，但 `missed_risk_events=7`、最差 20 日 -22.41%，失败。pattern veto 本次实际删除数为 0，继续作为解释性研究。

> 2026-06-19 v1.2b FP classified 研究更新：新增 `production_governed_vol_position_v1_2b_fp_classified`，把 false-positive gap 转成 benign/dangerous/borderline 恢复判别。三年结果为收益 +25.61%、年化 +9.81%、最大回撤 -24.81%、`recovery_days=8`、`missed_risk_events=3`、`false_positive_reduce_days=134`，未通过硬门槛。feature profile 显示 dangerous false-positive 的 `champion_score_pctile_252` 中位数反而高于 benign，说明单纯用高分位恢复的规则方向不稳；pattern coverage 显示 Top5/Top30 high-risk 命中均为 0，图形 veto 仍无实际覆盖。结论：FP classified 首版作为失败研究样本归档，不进入 shadow production。

> 2026-06-19 v1.2b shadow monitor 与特征可分性升级：生产默认继续保持 `production_governed_vol_position`，`research_shadow_candidate.enabled=false` 保持禁用。新增手工只读 shadow monitor，默认比较当前生产 v1 与 `production_governed_vol_position_v1_2b_gate_tuned`，输出仓位差、risk decision 差异、Top5 重合度、订单金额差、理论收益差和执行可行性代理到 `exports/research_shadow_candidate/`。新增 false-positive feature separability 分析，使用 AUC、KS、IQR overlap 和阈值 precision/recall 验证 benign/dangerous 是否可分；false-positive 标签只作解释，不再直接进入交易 gate。pattern 能力降级为覆盖率/质量监控，暂不参与生产候选判断。

> 2026-06-19 v1.2b gate tuned 手工 shadow 验收器更新：`run_research_shadow_candidate_monitor.py --rolling-days 20` 已升级为 20 日验收器，并生成 `reports/production_monitor/research_shadow_candidate_daily.json/md`。当前历史窗口烟测 rows=20、avg/min Top5 overlap=1.0、risk decision 差异 0 天、仓位差 0、理论收益差合计 +1.23%、执行降级 0 天，`shadow_pass=true`；这只表示历史样本满足手工 shadow 口径，不代表启用生产或 shadow 配置。feature separability 当前按规则给出 `SEPARABLE`，但最佳特征方向为 `champion_score_pctile_252` 越低越接近 benign，仍只作解释标签；pattern feature quality 为 `PATTERN_QUALITY_MONITOR_ONLY`，Top5/Top30 核心覆盖率 0%，不得进入风控。

> 2026-06-19 v1.2b gate tuned 双门槛 shadow 更新：shadow monitor 新增 calendar/event 双验收、执行代理 pass 和 recovery-event 报告。最新 20 日历史窗口 `calendar_window_pass=true`，但 `event_window_pass=false`，原因是 `recovery_event_days=0` 且 `shadow_recovery_theory_gap_sum=0`；同时 `execution_proxy_pass=false`，因为 20 天均为 `unknown_missing_execution_proxy`。结论：v1.2b gate tuned 只能继续手工 shadow 观察，不能启用 `research_shadow_candidate.enabled`，不能进入 canary。Pattern lineage 审计显示 `PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING`，v1.2b gate tuned 的 pattern 核心字段覆盖仍为 0。

> 2026-06-19 shadow 事件台账与 promotion dashboard 更新：新增 `scripts/ops/append_research_shadow_event_log.py`，将 `research_shadow_candidate_recovery_events.json` 中的 recovery event 以 `trade_date + shadow_strategy + production_strategy` 去重累计到 `reports/production_monitor/research_shadow_event_log.csv`，并生成累计 summary。新增 `scripts/ops/report_research_shadow_promotion_status.py`，汇总 calendar/event/execution、pattern lineage、FP separability 和累计事件样本；当前 blocking 状态为 `NOT_READY_NO_EVENTS`、`NOT_READY_EXECUTION_PROXY`，pattern lineage 调整为 `PATTERN_LINEAGE_WARNING`，不再阻断 enabled shadow。账户级回测 candidates 补充执行代理字段与 `top_pattern_ids`，用于后续把 `execution_proxy_pass` 和 pattern lineage 从 unknown/missing 推进到可审计；生产默认与 `research_shadow_candidate.enabled=false` 不变。

> 2026-06-19 shadow promotion 口径修正计划落地：promotion dashboard 新增 `blocking_statuses` 与 `warning_statuses` 分层，pattern/FP 仅作为解释和 pattern 风控前置门槛；event log 支持 `--input-glob` 和 `--from-monitor-csv` 批量回填；新增 `scripts/research/analyze_execution_proxy_quality.py`，按 strategy/date/Top5/Top10/Top30 审计 6 个执行代理字段的缺失率、可用率和 degraded 比例。下一步需基于新 candidates 重跑 governed 回测、20/60/120/full-history shadow monitor 与 execution proxy quality，确认 `execution_unknown_days` 是否归零。

> 2026-06-19 shadow execution proxy 验收重跑：基于 `exports/signal_research/20260619_070339_967843_trusted_account_backtest/` 重跑 governed 矩阵，`production_governed_vol_position_v1_2b_gate_tuned` 三年收益 +59.38%、年化 +21.04%、最大回撤 -25.52%、`missed_risk_events=0`。新 candidates 的 execution proxy 可用性已恢复：20/60/120 日窗口 `execution_unknown_days=0`，质量报告 `EXECUTION_PROXY_READY`，Top5/Top30 available ratio 均约 99.93%。但 20 日窗口因 2 天 large-slippage proxy 降级导致 `calendar_window_pass=false`、`execution_proxy_pass=false`；60 日有 6 个 recovery event 但 recovery theory gap 为 -3.39%；120 日有 23 个 recovery event 且 gap +3.77%，但 event execution degraded 3 天。累计事件台账已回填 98 个 recovery event，累计 gap +4.94%，execution proxy available ratio 100%，但 degraded event days=4。当前 promotion status 仍为 `MANUAL_SHADOW_OBSERVATION`，blocking 为 `NOT_READY_CALENDAR_WINDOW`、`NOT_READY_NO_EVENTS`、`NOT_READY_EXECUTION_PROXY`，pattern lineage 仅为 warning。

> 2026-06-19 shadow 事件质量与增量执行风险升级：新增 `scripts/research/analyze_shadow_execution_degradation.py` 和 `scripts/research/analyze_research_shadow_event_quality.py`，promotion dashboard 升级为 calendar / event window / cumulative event / incremental execution 四层 gate。基于 120 日 shadow monitor 的执行降级归因显示：`calendar_execution_degraded_days=3`、`event_execution_degraded_days=3`、`incremental_execution_degraded_days=3`、`common_execution_degraded_days=0`，说明当前阻断来自 v1.2b recovery 增量暴露而非生产与 shadow 共同风险。累计 98 个 recovery event 的质量分层为 `CUMULATIVE_EVENT_READY`：positive rate 56.12%、累计 theory gap +4.94%、event degraded ratio 4.08%。因此结论更新为：累计事件正贡献已达观察门槛，但增量执行风险未清零，promotion status 继续为 `MANUAL_SHADOW_OBSERVATION`，blocking 为 `NOT_READY_CALENDAR_WINDOW`、`NOT_READY_NO_EVENTS`、`NOT_READY_EXECUTION_PROXY`、`NOT_READY_INCREMENTAL_EXECUTION`；不得启用 shadow/canary。

> 2026-06-19 shadow promotion gate v2.1 更新：新增逐日逐股执行降级报告 `reports/production_monitor/shadow_execution_degradation_report.md`，并将执行风险拆为 common / shadow incremental / event / non-event large slippage。基于最新 120 日窗口，`calendar_execution_degraded_days=24`、`common_execution_degraded_days=21`、`event_execution_degraded_days=3`、`incremental_execution_degraded_days=3`、`incremental_large_slippage_days=3`，三个增量降级日均为同一 Top5 路径下的增量仓位暴露，并非 shadow 新增股票。累计 98 个 recovery event 中，execution-safe 子集 94 个，positive rate 55.32%、累计 gap +3.30%，`execution_safe_event_gate=pass_execution_safe_events`；但 120 日 event window 仍因 3 天 event degraded 失败。promotion dashboard v2.1 已将 `NOT_READY_EXECUTION_PROXY` 拆为 `NOT_READY_EXECUTION_PROXY_MISSING` / `NOT_READY_EXECUTION_DEGRADED`，当前 blocking 为 `NOT_READY_CALENDAR_WINDOW`、`NOT_READY_EVENT_WINDOW`、`NOT_READY_EXECUTION_DEGRADED`、`NOT_READY_INCREMENTAL_EXECUTION`，继续不得启用 shadow/canary。

> 2026-06-19 shadow promotion gate v2.2 更新：新增 `scripts/research/simulate_execution_safe_recovery_uplift.py`，以路径级 counterfactual 验证 v1.2b gate tuned 的增量 recovery 仓位。执行风险拆为 hard block 与 slippage warning：`large_slippage_proxy > 3%` 仅披露为 `EXECUTION_SLIPPAGE_WARNING`，`abs(open_gap_proxy)>5%`、涨跌停代理或冲击成本超阈值才阻断 promotion。基于 120 日窗口，20 日 calendar v2.2 为 `pass_with_slippage_warning`，累计 98 个 recovery event 仍为正贡献，promotion-valid uplift 子集 17 个事件、positive rate 64.71%、累计 gap +5.55%；但逐股候选归因发现 2 个 event/incremental hard-block 日，dashboard 当前 blocking 为 `NOT_READY_EVENT_WINDOW`、`NOT_READY_INCREMENTAL_EXECUTION`，仍不得启用 shadow/canary。核心输出：`exports/signal_research/execution_safe_recovery_uplift/20260619_120424_execution_safe_recovery_uplift/`、`reports/production_monitor/research_shadow_promotion_status.json`。

> 2026-06-19 execution-safe uplift 晋级口径修正：uplift 输出新增候选级 `hard_block_fallback_cases.csv`、`hard_block_fallback_events.csv` 与 fallback gate。hard block 事件整日回退 production 路径，单纯 large-slippage 仍为 warning；`promotion_valid_hard_block_count` 改名为 `excluded_hard_block_event_count`，旧字段仅保留兼容标记。Dashboard 同时展示原始 shadow blocker 与 `READY_FOR_EXECUTION_SAFE_UPLIFT_RESEARCH` 研究状态；后者绝不解除原始 event-window 或 incremental-execution blocker，也不代表 enabled shadow、canary 或生产切换。该路径仍是反事实验证，下一步必须经过独立账户级 T+1 回测。

> 2026-06-19 execution-safe uplift 账户级研究接入：新增 `production_governed_vol_position_v1_2b_execution_safe_uplift`。同一 T 日准备 v1 与 v1.2b gate-tuned 目标，T+1 开盘仅对相对 v1 的 recovery 增量买入检查跳空、涨跌停和冲击代理；hard block 或代理缺失时执行 v1 路线，large-slippage-only 保持 warning。该策略不进入生产配置或 enabled shadow；完整三年验收待数据库运行环境提供凭据后执行。

> 2026-06-19 execution-safe uplift 因果性修正：账户级回测新增 `--execution-mode`，默认且唯一可验收口径为 `strict_t1_open_precommit`。strict 模式在 T 日预提交目标、T+1 开盘成交，不得用 T+1 `adj_open` 或日线执行代理改变路线；开盘 fallback 仅保留为 counterfactual 解释。竞价与 post-open 模式在没有带时间戳分钟数据时 fail closed。决策、候选与交易写入 execution timestamp、mode 与 `causality_pass` 审计字段，当前不具备 enabled-shadow 晋级资格。

> 2026-06-04 更新：当前 `adaptive_market_style` 从单纯三年低回撤口径升级为“最近 3 个月收益优先 + 长期风险约束 + 日检周切”。近期冠军默认指向 `baseline_full_liquidity_detail_vol_position`，系统每天检测市场、行业和量能状态，最多每周切换一次底层基准，目标仓位约 50% / 70% / 80%。`tiered_liquidity_then_bs_v2` 只在强市场短期增强，不作为长期满仓默认。

> 2026-06-04 双系统路由更新：新增 `dual_system_adaptive_route`、`ashare_auto_shadow`、`ashare_trend_breakout_shadow`、`ashare_hybrid_conservative_shadow`。最近 3 个月账户级对照中，AShare AUTO 影子收益约 +8.72%、最大回撤约 -17.28%，`adaptive_market_style` 收益约 +7.36%、最大回撤约 -10.97%，双系统路由收益约 +5.27%、最大回撤约 -12.06%。当前结论：AShare AUTO 有收益弹性但波动更大，dual route 风控较保守，需继续优化 AShare 周线门禁和候选缓存后再跑三年验收。

> 2026-06-04 v2.1 更新：`adaptive_market_style` 已加入 AShare 加权增强，AShare 周线未确认不再硬剔除，而是降权；AShare 补位最多 2 只，ST 类外部候选硬过滤。最近半年窗口收益约 +35.14%、最大回撤约 -11.09%。三年硬底线 `-45%` 尚未完成自动验收，三年单策略回测超过 4 分钟未返回，需继续缓存 adaptive perf 与路由目标后复跑。

> 2026-06-05 v2.2 更新：`adaptive_market_style` 升级为收益优先生产版，正式标记 `adaptive_version=v2.2`、`ashare_weight_profile=prod_stage1`、`ashare_release_tier=production_stage1`，默认 AShare 补位上限 2 只。新增路由磁盘缓存与防守态风险叠加：防守状态且近期冠军分数转负时，目标仓位从 50% 降至 45%。三年 T+1 账户级回测收益约 +42.09%、年化约 +11.36%、最大回撤约 -37.33%，满足三年最大回撤不劣于 -45% 的硬底线。

> 2026-06-05 主推送切换：飞书生产主策略改为 `baseline_full_liquidity_detail_vol_position`，默认仓位 70%。该策略最近 3 个月收益约 +36.71%、最大回撤约 -12.87%，最近半年收益约 +84.99%、最大回撤约 -28.75%，但完整三年裸跑最大回撤约 -66.41%。因此本次是“收益优先主推送”，不是长期满仓无门禁策略；`adaptive_market_style` v2.2 继续保留为每日市场/行业状态风控影子对照。

| 策略名称 | 资金规模 | 回测周期 | 期末权益 | 年化收益 | 最大回撤 | 结论 | 文件夹 |
|---|---:|---|---:|---:|---:|---|---|
| `production_governed_vol_position_v1_2b_fp_classified` / FP classified 首版 | 50 万 | 2023-01-04 至 2026-06-17 | 628,050 | +9.81% | -24.81% | 只恢复 8 天 benign-like，`missed_risk_events=3`、误降仓 134，收益和改善门槛均未达；首版分类规则方向被证伪，归档为失败研究样本 | `exports/signal_research/20260619_011955_448345_trusted_account_backtest/` |
| `production_governed_vol_position_v1_2b_gate_tuned` / gate tuned 强观察 | 50 万 | 2023-01-04 至 2026-06-17 | 786,126 | +20.41% | -25.52% | 首版 gate 参数未改变 v1.2b 路径，`missed_risk_events=0`，但误降仓 122 和最差 20 日 -16.82% 仍未过线；局部网格首组误降仓 111 但漏风险 7，暂不进 shadow production | `exports/signal_research/20260619_005237_692696_trusted_account_backtest/` |
| `adaptive_market_style` / 挑战者对照 | 50 万 | 2023-01-04 至 2026-06-17 | 724,539 | +16.44% | -26.68% | 三年收益 +44.91%，平均仓位 37.80%，年化/仓位效率高于 governed；仍需专项归因验证，不直接替换生产默认 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `production_governed_vol_position_v1_2b_dynamic_score` / dynamic score 强观察 | 50 万 | 2023-01-04 至 2026-06-17 | 786,126 | +20.41% | -25.52% | 三年收益 +57.23%，`missed_risk_events=0`、`recovery_days=59`，但误降仓 122 未达 <=112，最差 20 日 -16.82% 略低于门槛，暂不进生产默认 | `exports/signal_research/20260618_213002_059138_trusted_account_backtest/` |
| `production_governed_vol_position_v1_1_recovery` / selective recovery 强观察 | 50 万 | 2023-01-04 至 2026-06-17 | 709,468 | +15.44% | -25.65% | 三年收益 +41.89%，收益和回撤接近挑战者，但 `missed_risk_events=8`，暂不进入生产默认 | `exports/signal_research/20260618_193951_941705_trusted_account_backtest/` |
| `production_governed_vol_position_v1_2_recovery` / missed-risk 清零过保守样本 | 50 万 | 2023-01-04 至 2026-06-17 | 599,703 | +7.75% | -24.81% | `missed_risk_events=0`，但 `recovery_days=0`、false positive reduce days 仍为 132，未带来收益或误降仓改善 | `exports/signal_research/20260618_204722_645522_trusted_account_backtest/` |
| `production_governed_vol_position` / 当前生产默认底座 | 50 万 | 2023-01-04 至 2026-06-17 | 599,703 | +7.75% | -24.81% | 三年收益 +19.94%，相比裸 `vol_position` 收益提升且回撤下降；`missed_risk_events=0`，作为当前生产默认固化 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `production_governed_vol_position_v2` / soft-hard reduce 研究失败样本 | 50 万 | 2023-01-04 至 2026-06-17 | 490,997 | -0.74% | -29.29% | 三年收益 -1.80%，`missed_risk_events=20`；误降仓仅小幅下降，不满足最大回撤和收益目标 | `exports/signal_research/20260618_131158_324863_trusted_account_backtest/` |
| `production_governed_adaptive_pattern_guard` / 下一代候选失败样本 | 50 万 | 2023-01-04 至 2026-06-17 | 441,040 | -5.02% | -52.06% | 三年收益 -11.79%，最坏归因 `missed_risk_events=6`，未达“回撤不劣于当前 governed”门槛，不进入生产候选 | `exports/signal_research/20260618_125140_642924_trusted_account_backtest/` |
| `baseline_full_liquidity_detail_vol_position` / 飞书主推送 | 50 万 | 2023-01-04 至 2026-06-02 | 592,007 | +5.32% | -66.41% | 最近 3 个月 +36.71%、半年 +84.99%、一年 +177.92%；近期收益弹性最强，但三年回撤深，默认生产仓位降为 70%，并保留 adaptive 防守影子对照 | `exports/signal_research/20260604_152142_206060_trusted_account_backtest/` |
| `adaptive_market_style` / AShare 加权增强 v2.2 | 50 万 | 2023-01-04 至 2026-06-04 | 710,445 | +11.36% | -37.33% | 三年收益约 +42.09%，默认 AShare 补位上限 2 只；防守态近期冠军转负时降至 45% 仓位，已通过 `-45%` 回撤硬底线 | `exports/signal_research/20260605_004258_229723_trusted_account_backtest/` |
| `adaptive_market_style` / AShare 加权增强 v2.1 | 50 万 | 2025-12-04 至 2026-06-04 | 675,724 | +91.30% | -11.09% | 半年收益约 +35.14%，周线未确认走降权，AShare 补位最多 2 只；三年硬底线待缓存优化后复跑 | `exports/signal_research/20260604_231724_811158_trusted_account_backtest/` |
| `dual_system_adaptive_route` / v2.1 对照 | 50 万 | 2025-12-04 至 2026-06-04 | 640,090 | +70.23% | -9.26% | 半年收益约 +28.02%，回撤低于 adaptive，但收益也低于正式 adaptive | `exports/signal_research/20260604_231724_811158_trusted_account_backtest/` |
| `dual_system_adaptive_route` / 双系统路由 | 50 万 | 2026-03-03 至 2026-06-02 | 526,350 | +24.07% | -12.06% | 最近 3 个月收益约 +5.27%，低于 `adaptive_market_style` 和 AShare AUTO；路由状态含 18 天 attack、22 天 neutral、14 天 defensive、7 天 freeze | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `ashare_auto_shadow` / AShare AUTO 影子 | 50 万 | 2026-03-03 至 2026-06-02 | 543,590 | +42.06% | -17.28% | 最近 3 个月收益约 +8.72%，收益弹性最高但波动和回撤也最高，仅作外部源影子观察 | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `adaptive_market_style` / 双系统对照窗口 | 50 万 | 2026-03-03 至 2026-06-02 | 536,817 | +34.77% | -10.97% | 最近 3 个月收益约 +7.36%，回撤低于 AShare AUTO，是当前双系统路由的主要对照基准 | `exports/signal_research/20260604_163941_308980_trusted_account_backtest/` |
| `adaptive_market_style` / 3个月收益优先周切换 | 50 万 | 2023-01-04 至 2026-06-02 | 696,564 | +10.71% | -43.01% | 三年收益约 +39.31%，回撤低于单独 `vol_position`；最近 3 个月 +6.39%、半年 +41.73%、一年 +47.67% | `exports/signal_research/20260604_152142_206060_trusted_account_backtest/` |
| 核心策略风格画像研究 | - | 2023-01-04 至 2026-06-02 | - | - | - | 输出市场/行业/量能分组、网格阈值和每日 adaptive 决策表 | `exports/signal_research/20260604_105452_core_strategy_style_research/` |
| `baseline_full_liquidity_detail_market_gate` / 50% 仓位 | 50 万 | 2023-01-04 至 2026-06-02 | 475,270 | -1.54% | -43.42% | 三年回撤控制相对最好，可做防守影子候选 | `exports/signal_research/trusted_strategy_optimization_20260603_224953/` |
| `baseline_full_liquidity_detail` / hold12 | 50 万 | 2023-01-04 至 2026-06-02 | 489,717 | -0.64% | -65.17% | 三年持仓期矩阵相对最好，仍需降回撤 | `exports/signal_research/trusted_strategy_optimization_20260603_203215/` |
| `tiered_liquidity_then_bs_v2` / stop8 | 50 万 | 2023-01-04 至 2026-06-02 | 123,306 | -34.90% | -90.45% | 硬止损未修复进攻策略长期风险，不建议默认上线 | `exports/signal_research/trusted_strategy_optimization_20260603_212905/` |
| `tiered_liquidity_then_bs_v2_industry_cap1` | 50 万 | 2023-01-04 至 2026-06-02 | 100,684 | -38.82% | -88.50% | 行业集中下降但收益恶化，不建议默认上线 | `exports/signal_research/trusted_strategy_optimization_20260603_223619/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2023-01-04 至 2026-06-02 | 268,830 | -17.32% | -66.46% | 三年弱于防守半仓，可继续影子观察 | `exports/signal_research/trusted_strategy_optimization_20260603_232619/` |
| `baseline_full_liquidity_detail` | 50 万 | 2023-01-04 至 2026-06-02 | 283,045 | -16.01% | -75.27% | 完整三年口径相对最好，但仍为负收益 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2023-01-04 至 2026-06-02 | 268,830 | -17.32% | -66.46% | 三年回撤低于固定进攻，但仍未盈利 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `tiered_liquidity_then_bs_v2` | 50 万 | 2023-01-04 至 2026-06-02 | 146,850 | -31.31% | -94.20% | 最近一年强、三年回撤极深，不能单独长期满仓外推 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `baseline_full_score` | 50 万 | 2023-01-04 至 2026-06-02 | 121,280 | -35.23% | -91.67% | 基础综合分长期风险很高 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `adaptive_style_switch` | 50 万 | 2023-01-04 至 2026-06-02 | 99,602 | -39.02% | -88.72% | 硬切换规则三年未通过，需要继续优化 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `baseline_full_dynamic_factor_industry_cap2` | 50 万 | 2023-01-04 至 2026-06-02 | 30,490 | -57.58% | -96.23% | 动态因子行业约束在三年窗口失效严重 | `exports/signal_research/20260603_202728_444675_trusted_account_backtest/` |
| `tiered_liquidity_then_bs_v2` | 50 万 | 2025-06-03 至 2026-05-29 | 1,544,720 | +228.49% | -23.28% | 最近一年固定策略第一 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `adaptive_style_switch` | 50 万 | 2025-06-03 至 2026-05-29 | 778,429 | +59.48% | -23.64% | 未跑赢固定进攻策略，仅研究观察 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `adaptive_style_switch_dynamic_position` | 50 万 | 2025-06-04 至 2026-05-29 | 730,623 | +49.17% | -17.80% | 降低回撤但牺牲收益，作为动态仓位研究观察 | `exports/signal_research/20260603_094422_665991_trusted_account_backtest/` |
| `baseline_full_liquidity_detail` | 50 万 | 2025-06-03 至 2026-05-29 | 845,960 | +74.10% | -30.80% | 防守/流动性质量对照 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `baseline_full_dynamic_factor_industry_cap2` | 50 万 | 2025-06-03 至 2026-05-29 | 827,899 | +70.18% | -32.90% | 均衡策略对照 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |
| `baseline_full_score` | 50 万 | 2025-06-03 至 2026-05-29 | 653,208 | +32.55% | -40.87% | 综合分兜底基准 | `exports/signal_research/20260601_221903_213811_trusted_account_backtest/` |

## 文件说明

- `trusted_account_backtest_summary.csv`：账户级回测汇总。
- `trusted_account_backtest_nav.csv`：净值曲线。
- `trusted_account_backtest_positions.csv`：每日持仓。
- `trusted_account_backtest_trades.csv`：交易日志。
- `trusted_account_backtest_candidates.csv`：每日候选。
- `trusted_account_backtest_adaptive_decisions.csv`：自适应策略每日切换决策。
- `trusted_account_backtest_report.md/json`：回测报告。

## 归档原则

- 原始导出文件保留在 `exports/signal_research/`。
- `docs/03_backtest_reports/` 只存索引、摘要和人工复盘文档。
- 影响生产策略选择的回测必须记录：周期、资金、成本、滑点、T+1 口径、未来函数控制。
