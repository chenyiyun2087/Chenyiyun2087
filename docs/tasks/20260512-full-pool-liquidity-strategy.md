# 全量池流动性策略开发登记

## 目标

验证并迭代“全量股票池 + 流动性约束 + 现有评分体系”的研究策略，并提供人工复核式生产候选导出。当前阶段不自动下单，不改 Web 页面和实盘入池逻辑。

## 统一回测口径

- 信号日 T 使用 `score_rank_daily` 当日已落库字段。
- T+1 开盘买入，持有 10 个交易日后收盘卖出。
- 默认 Top5 等权。
- 默认成本档：0、0.15%、0.20%。
- `rebalance_step=1` 是每日滚动事件研究；`rebalance_step=10` 是更接近非重叠调仓的粗略口径。
- 全量池有效门槛：单日评分股票数不少于 5000。

## 任务登记

| 时间 | 任务 | 状态 | 说明 |
|---|---|---|---|
| 2026-05-12 | 建立任务登记文件 | 完成 | 新增本文件，后续每完成一个开发任务都在这里登记。 |
| 2026-05-12 | 第一批研究脚本 | 完成 | 新增 `scripts/research_full_pool_liquidity_strategies.py`，支持基准、流动性预筛、B点加分、成本、样本切分和报告输出。 |
| 2026-05-12 | 第一批回测验证 | 完成 | 已跑 `rebalance_step=1` 和 `rebalance_step=10` 两个口径，报告位于 `exports/signal_research/20260512_192233_full_pool_liquidity` 与 `exports/signal_research/20260512_192317_full_pool_liquidity`。 |
| 2026-05-12 | 流动性分层策略 | 完成 | 新增 `liquidity_tiered` 选池：高流动性层直接参与排序，中流动性层要求 B 点且门禁不为“过滤”，低流动性层排除。策略包括 `tiered_liquidity_then_score`、`tiered_liquidity_then_bs_v2`、`tiered_liquidity_then_liq_breakout_adj`。 |
| 2026-05-12 | 突破因子流动性折扣 | 完成 | 新增 `score_liq_breakout_adj`：当流动性排名不在前 40% 时，`s_breakout` 按 50% 折扣重算近似总分。新增全量和流动性预筛后的折扣突破策略。 |
| 2026-05-12 | 第二批回测验证 | 完成 | 已跑 `rebalance_step=1` 和 `rebalance_step=10`。报告位于 `exports/signal_research/20260512_195337_full_pool_liquidity` 与 `exports/signal_research/20260512_195335_full_pool_liquidity`。 |
| 2026-05-12 | 流动性衍生因子 | 完成 | 新增 `relative_amount`、`amount_ratio_5_20`、`impact_cost_raw`、`amount_stability_raw` 及对应横截面分，合成 `liquidity_detail_score`。所有计算只使用信号日及以前的滚动窗口。 |
| 2026-05-12 | 衍生流动性回测验证 | 完成 | 已跑 `rebalance_step=1` 和 `rebalance_step=10`。报告位于 `exports/signal_research/20260512_195726_full_pool_liquidity` 与 `exports/signal_research/20260512_195723_full_pool_liquidity`。 |
| 2026-05-12 | 动态因子加权 | 完成 | 新增 `dynamic_factor_score`，只使用已完成持有期的历史窗口估计权重，避免未来函数；输出 `full_pool_liquidity_strategy_dynamic_weights.csv`。 |
| 2026-05-12 | 市场环境切分归因 | 完成 | 新增 `market_liquidity_bucket`、`index_bucket` 分桶归因，输出市场环境表与分环境收益汇总。 |
| 2026-05-12 | 组合约束研究 | 完成 | 新增 `max_per_industry=2` 研究策略和 `avg_max_industry_weight` 指标；修复行业为空时被误归为同一行业的问题。 |
| 2026-05-12 | 最终完整验证 | 完成 | 已跑最新 `rebalance_step=1` 和 `rebalance_step=10` 两个口径。报告位于 `exports/signal_research/20260512_200726_full_pool_liquidity` 与 `exports/signal_research/20260512_200722_full_pool_liquidity`。 |
| 2026-05-12 | 历史行业字段修复 | 完成 | 新增 `scripts/backfill_score_rank_daily_industry.py`，从 `tushare_stock.dim_stock` 按股票代码回填 `score_rank_daily.industry`；已回填 240,783 行，修复后空行业为 0。 |
| 2026-05-12 | 行业修复后回测验证 | 完成 | 已基于修复后的行业字段重跑 `rebalance_step=1` 和 `rebalance_step=10`。报告位于 `exports/signal_research/20260512_202059_full_pool_liquidity` 与 `exports/signal_research/20260512_202055_full_pool_liquidity`。 |
| 2026-05-12 | 行业集中软惩罚研究 | 完成 | 新增 `industry_penalty_step` 迭代选股逻辑，验证纯流动性、流动性Top10+综合分、分层流动性策略的行业软惩罚效果。报告位于 `exports/signal_research/20260512_202949_full_pool_liquidity` 与 `exports/signal_research/20260512_202838_full_pool_liquidity`。 |
| 2026-05-12 | 历史评分入口兼容性修复 | 完成 | `scoreRank.cli.run_daily` 增加 postponed annotations，修复 Python 3.9 下 `str \| None` 导致入口无法启动的问题；已成功补跑 2026-01-21，写入 5,176 条全量评分。 |
| 2026-05-12 | 批量历史评分回填工具 | 完成 | 新增 `scripts/backfill_score_rank_daily_history.py`，支持按行情交易日筛选缺失/低覆盖评分日、dry-run、限量执行、日志输出和单日结果校验。 |
| 2026-05-12 | 2026-01 至 2026-02 历史评分回填 | 完成 | 已回填/刷新 2026-01-05 至 2026-02-12 之间 28 个缺失或低覆盖交易日，单日评分行数约 5,165 至 5,179；执行日志位于 `exports/score_backfill/score_backfill_20260512_204016.csv`。 |
| 2026-05-12 | 退市股空行业兜底修复 | 完成 | 定位残留空行业为 `300379.SZ`（东通退）缺失于 `tushare_stock.dim_stock`；`scoreRank.core.external_features` 已增加“未知”行业兜底，`scripts/backfill_score_rank_daily_industry.py` 已支持备用来源和未知兜底，修复后全表空行业为 0。 |
| 2026-05-12 | 扩展样本后最终回测验证 | 完成 | 基于 74 个全量评分日、394,977 条评分记录重跑 `rebalance_step=1` 和 `rebalance_step=10`。报告位于 `exports/signal_research/20260512_212900_full_pool_liquidity` 与 `exports/signal_research/20260512_213025_full_pool_liquidity`。 |
| 2026-05-13 | P0 可信策略集合与风险标记 | 完成 | `scripts/research_full_pool_liquidity_strategies.py` 新增 `pit_status`、`risk_note` 与 `--trusted-only`，默认可剔除模型版本未来函数风险策略。 |
| 2026-05-13 | P1/P2 候选池重构与流动性分层增强 | 完成 | 新增/完善流动性 Top20/Top30、`bs_score_v2` 排序、B点 3%/5% 加分、分层流动性策略与市场门禁组合。 |
| 2026-05-13 | P3 突破因子流动性过滤参数化 | 完成 | 新增 `score_liq_breakout_adj_50p_50d`、`score_liq_breakout_adj_40p_30d`，可比较不同流动性阈值和突破折扣强度。 |
| 2026-05-13 | P4 市场流动性门禁 | 完成 | 新增 `market_gate` 策略属性，低市场成交环境下提高最低排序分并将总敞口降至 60%。 |
| 2026-05-13 | P5 动态因子调权重构 | 完成 | 动态权重因子收敛到非模型可信因子，并新增 `dynamic_ic_factor_score`，只使用已完成持有期历史样本估计权重。 |
| 2026-05-13 | P6 模型 Walk-Forward 验证框架 | 完成 | 新增 `scripts/research_bs_model_walkforward.py`，按月份仅用预测月之前的数据训练并预测，输出无模型版本穿越的模型验证报告。 |
| 2026-05-13 | P7 仓位控制研究 | 完成 | 新增 `hist_mdd_20`、`vol_20` 与 `expected_mdd` 仓位模式；`expected_mdd` 相关策略被标记为 `model_risk`，可信模式默认剔除。 |
| 2026-05-13 | P0-P7 可信模式回测验证 | 完成 | 已跑 `--trusted-only` 的 `rebalance_step=10` 与 `rebalance_step=1`。报告位于 `exports/signal_research/20260513_030551_full_pool_liquidity`、`exports/signal_research/20260513_031026_full_pool_liquidity`；模型 walk-forward 报告位于 `exports/bs_model_walkforward/20260513_030343_walkforward`。 |
| 2026-05-13 | 可信策略生产候选导出 | 完成 | 新增 `scripts/ops/export_trusted_strategy_candidates.py`，默认导出 `baseline_full_dynamic_factor_industry_cap2` 的 Top5 候选；价格与评分数据均截断到信号日，动态权重只使用已完成持有期历史样本。 |
| 2026-05-13 | 生产候选导出验证 | 完成 | 已基于最新评分日 2026-05-12 生成候选名单，输出位于 `exports/production_candidates/20260513_093520_baseline_full_dynamic_factor_industry_cap2`；输出无告警，价格最大日期为 2026-05-12。 |
| 2026-05-13 | 生产操作步骤文档 | 完成 | 新增 `docs/production_trusted_strategy_usage.md`，说明每日评分、行业校验、候选导出、人工复核、T+1 执行、10 日持仓和生产结果登记流程。 |
| 2026-05-13 | 纳入日终批量任务 | 完成 | `scheduler.py` 的 `daily_pipeline` 已在 `scoreRank` 后自动回填当日空行业，并在 B 点综合分后导出可信全量池候选；Web 任务中心新增“可信全量池候选导出”，默认交易日 21:25 执行并带结果校验。 |
| 2026-05-13 | 实盘入池和本地订单自动写入 | 完成 | `export_trusted_strategy_candidates.py` 新增 `--write-db --emit-orders`，自动写入 `ads_trusted_strategy_candidates`、`TRUSTED_FULL_POOL_TOP5` 股票池、`ads_chenyiyun_selected_signals` 和 `ads_local_strategy_orders`；已基于 2026-05-12 写入候选 5 条、股票池 5 条、本地订单/信号 7 条。 |
| 2026-05-13 | 批量任务优化升级 | 完成 | 日终 `scoreRank` 与 B 点综合分已显式绑定目标交易日执行，避免依赖隐式最新日期；dry-run 调度校验已覆盖行业回填和可信候选导出步骤。 |
| 2026-05-13 | 本地订单飞书通知与前置失败门禁 | 完成 | `export_trusted_strategy_candidates.py` 新增 `--notify-feishu` 和订单前置校验；日终批量/Web任务均带该参数。已验证前置条件不足时脚本返回非 0；正常生成 7 条本地订单草案后飞书发送成功（`ok_ssl_unverified`）。 |
| 2026-05-13 | Web 资产初始状态重置 | 完成 | `sina/positions` 页面新增一键资产重置入口；后端新增 `/sina/assets/reset`，清空 `live_positions` 并重建 50 万初始账户快照，使现金、总权益、累计收益率和年化收益率回到初始状态。 |
| 2026-05-13 | 旧陈依云精选策略生产隔离 | 完成 | 将 `chenyiyun_selected` 标记为 Legacy，任务中心禁止调度和手动运行；旧脚本默认输出改为 `ads_legacy_chenyiyun_*` 表，避免误写 `ads_chenyiyun_selected_signals` 和 `ads_local_strategy_orders` 影响当前可信全量池选股。 |
| 2026-05-13 | 生产订单持仓期门禁 | 完成 | `export_trusted_strategy_candidates.py` 生成本地订单草案时启用 `--hold-days` 门禁：未满 10 个交易日的持仓不卖、不减仓，并先占用组合预算，避免日更 Top5 导致过度换手和超配买入；飞书通知同步展示锁定持仓数量。 |
| 2026-05-31 | 生产口径账户级回测 | 完成 | 新增 `scripts/research_trusted_strategy_account_backtest.py`，按 50 万初始资金、T+1 开盘调仓、10 个交易日持仓门禁、整数手、最小交易额、单边成本和可选滑点模拟每日批量任务驱动下的真实账户净值。 |
| 2026-05-31 | 账户级基准验证 | 完成 | 已跑 2026-01-05 至 2026-05-29 账户级回测。基准成本口径输出位于 `exports/signal_research/20260531_220015_trusted_account_backtest`，压力成本/滑点口径输出位于 `exports/signal_research/20260531_220259_trusted_account_backtest`。 |
| 2026-05-31 | 账户持仓上限优化 | 完成 | `scripts/research_trusted_strategy_account_backtest.py`、`scripts/ops/export_trusted_strategy_candidates.py` 新增 `--max-total-positions`；经 5/10/12 档账户级验证，默认生产值设为 5，并已接入 `scheduler.py` 与 Web 任务中心。 |
| 2026-05-31 | 持仓上限生产演练 | 完成 | 已用 2026-05-29 信号日执行不写库、不飞书的候选导出演练，输出位于 `exports/production_candidates/20260531_221517_baseline_full_dynamic_factor_industry_cap2`；结果无告警，订单草案包含 `max_total_positions=5`。 |
| 2026-05-31 | 持有期与 TopN 参数复核 | 完成 | 账户级验证显示当前 `top_n=5`、`hold_days=10`、`max_total_positions=5` 仍是本轮测试中的最佳组合；5/7/15 日持有期、Top3 和 Top7 均未胜出。 |
| 2026-05-31 | 并行研究输出目录修复 | 完成 | `research_trusted_strategy_account_backtest.py` 输出目录增加微秒级时间戳，避免并行参数回测在同一秒生成相同目录导致文件互相覆盖。 |
| 2026-05-31 | 市场门禁与仓位比例复核 | 完成 | 新增 `baseline_full_dynamic_factor_industry_cap2_market_gate` 研究变体；账户级验证显示市场成交门禁本样本未触发。账户回测新增 `--position-ratio`，已验证 80%/60% 仓位风险预算。 |
| 2026-05-31 | 硬止损阈值复核 | 完成 | `research_trusted_strategy_account_backtest.py` 新增 `--hard-stop-loss-pct`，按次日/当日可观测开盘价触发卖出，验证 6%/8%/10% 三档止损对收益和回撤的影响。 |
| 2026-05-31 | 本地 MySQL 数据底座复核 | 完成 | 已对齐 `AShareDataCenter` 数据结构：行情底座为 `tushare_stock.dwd_stock_daily_standard`，评分表为 `chenyiyun.score_rank_daily`；当前评分覆盖 2026-01-05 至 2026-05-29，共 87 个评分日、462,058 条记录，行情表覆盖 20110104 至 20260529。 |
| 2026-05-31 | 全策略矩阵与未来函数审计 | 完成 | 已跑全策略事件研究，输出位于 `exports/signal_research/20260531_231457_full_pool_liquidity`；`bs_model_rank_score`、`bs_consensus_score`、`expected_mdd` 相关策略继续标记为 `model_risk`，因历史日期存在当前活跃模型回填风险，不纳入生产默认。 |
| 2026-05-31 | 全可信策略账户级 T+1 回测 | 完成 | 已跑全部可信策略账户级回测，输出位于 `exports/signal_research/20260531_232054_087068_trusted_account_backtest`；口径为 T 日信号、T+1 开盘成交、10 日持仓门禁、账户最多 5 只、单边成本 0.075%。 |
| 2026-06-01 | 真实可成交影子盘监控 | 完成 | 新增 `scripts/ops/run_trusted_strategy_shadow_monitor.py`，按 T 日订单和 T+1 开盘价复盘可成交性、涨跌停风险与滑点，结果写入 `ads_trusted_strategy_shadow_fills` 和 `ads_trusted_strategy_shadow_daily`，支持飞书通知。 |
| 2026-06-01 | 影子盘接入日终与 Web | 完成 | `scheduler.py` 日终流水线和 Web 任务中心新增“可信策略影子盘监控”；核心精选页新增影子盘成交监控面板。2026-05-28 信号在 2026-05-29 开盘影子复盘：8 笔订单均可成交，1 笔大滑点警告，最大不利滑点约 805 bps。 |
| 2026-06-01 | 2025 全年全量评分补齐 | 完成 | 新增 `scripts/backfill_score_rank_daily_2025_full.py`；已补齐 2025 年 243 个交易日、1,245,710 条 `score_rank_daily` 记录。`industry`、`score`、`s_liquidity`、`bs_score_v2`、`bs_consensus_score` 空值均为 0，`bs_model_*` 残留为 0；最终汇总位于 `exports/score_backfill/20260601_190355_692205_85246_2025_full/2025_full_score_backfill_summary.json`。 |
| 2026-06-01 | 最近一年回测准备校验 | 完成 | 2025 部分已满足最近一年回测数据要求；`2025-06-01` 至 `2026-05-29` 窗口校验发现缺口来自既有 2026 数据：评分日 233/241，且 2026 部分 `bs_consensus_score` 仍有 229,225 行为空。需另起 2026 规则字段补齐任务后再启动完整最近一年回测。 |
| 2026-06-01 | 最近一年窗口数据补齐 | 完成 | 已补齐 `2025-06-01` 至 `2026-05-29` 回测窗口缺失数据：评分日 241/241，评分记录 1,254,235 条，`industry`、`score`、`s_liquidity`、`bs_score_v2`、`bs_consensus_score`、`bs_model_*` 残留均为 0；汇总位于 `exports/score_backfill/20260601_213144_929557_97115_2025_full/2025_full_score_backfill_summary.json`。 |
| 2026-06-01 | 最近一年账户级 T+1 回测 | 完成 | 已跑 `2025-06-03` 至 `2026-05-29` 账户级回测，口径为 50 万初始资金、Top5、持有 10 个交易日、T+1 开盘成交、账户最多 5 只、单边成本 0.075%、无滑点；结果位于 `exports/signal_research/20260601_220005_679986_trusted_account_backtest`。 |
| 2026-06-01 | 市场风格自适应硬切换回测 | 完成 | `research_trusted_strategy_account_backtest.py` 新增 `adaptive_style_switch`，每日在进攻/均衡/防守/兜底策略间硬切换；选择器只使用 T 日市场字段和 `exit_date < T` 的已完成策略样本。最近一年回测输出位于 `exports/signal_research/20260601_221903_213811_trusted_account_backtest`。 |
| 2026-06-03 | 自适应动态仓位研究变体 | 完成 | `research_trusted_strategy_account_backtest.py` 新增 `adaptive_style_switch_dynamic_position`，在自适应策略切换基础上按 T 日市场状态和 `exit_date < T` 的已完成策略样本缩放目标仓位；最近一年对照输出位于 `exports/signal_research/20260603_094422_665991_trusted_account_backtest`。 |
| 2026-06-03 | 自适应动态仓位飞书影子对照 | 完成 | `export_trusted_strategy_candidates.py` 的飞书“策略订单对照”新增 `adaptive_style_switch_dynamic_position`，每日通知可展示其候选、订单、当前风格状态、底层策略、目标仓位和仓位原因；当前仅影子对照，不写入生产候选主策略。 |
| 2026-06-03 | 策略订单对照产物落盘 | 完成 | `export_trusted_strategy_candidates.py` 在生成飞书策略订单对照时同步输出 `trusted_strategy_order_detail_summary.csv`、`trusted_strategy_order_detail_candidates.csv`、`trusted_strategy_order_detail_orders.csv`、`trusted_strategy_order_detail_report.md/json`，便于后续长期复盘多策略订单差异。 |
| 2026-06-03 | 策略订单对照 Web 展示 | 完成 | 核心精选页 `/chenyiyun/selected` 新增“策略订单对照”面板，从最新候选 `output_json_path` 同目录读取 `trusted_strategy_order_detail_summary.csv`，展示多策略候选数、订单数、买卖数、自适应状态、底层策略和动态仓位。 |
| 2026-06-03 | 三年评分数据就绪检查 | 完成 | 新增 `scripts/maintenance/check_three_year_score_readiness.py`，检查三年窗口交易日、行情、评分覆盖、核心字段、`bs_model_*` 残留和 B/S 检测覆盖；默认检查输出位于 `exports/score_backfill/three_year_score_readiness_20260603_100024`。 |
| 2026-06-03 | 最新评分日模型字段残留清理 | 完成 | 新增 `scripts/maintenance/clear_score_rank_daily_model_fields.py`，支持 dry-run/execute 清理指定日期范围 `bs_model_*` 并重算规则口径 B 点增强分；已清理 2026-06-01 至 2026-06-02 共 10,323 行，清理报告位于 `exports/score_backfill/model_field_cleanup_20260603_100506`。复跑三年就绪检查后 `bs_model_*` 残留日从 2 降为 0，报告位于 `exports/score_backfill/three_year_score_readiness_20260603_100533`。 |
| 2026-06-03 | 2023-2024 评分补齐小样本试跑 | 完成 | `backfill_score_rank_daily_2025_full.py` 的行数校验改为动态阈值 `min(5000, 当日行情股票数×95%)`，三年就绪检查同步采用该口径。已试跑 2023-01-03 至 2023-01-05 三个交易日，共生成 14,326 条评分记录，核心字段和 `bs_model_*` 残留均为 0；试跑输出位于 `exports/score_backfill/20260603_100749_768603_28617_2025_full`。 |
| 2026-06-03 | 2023-2024 全量评分补齐与三年回测 | 完成 | 已补齐 2023-01-01 至 2024-12-31 共 484 个交易日、2,407,857 条评分记录；最终汇总位于 `exports/score_backfill/20260603_101607_705002_42526_2025_full/2025_full_score_backfill_summary.json`。复跑三年就绪检查显示 2023-01-01 至 2026-06-02 共 824 个交易日全部 ready，缺失/低覆盖/核心字段空值/`bs_model_*` 残留均为 0，报告位于 `exports/score_backfill/three_year_score_readiness_20260603_200251`。完整三年账户级 T+1 回测已跑通，输出位于 `exports/signal_research/20260603_202728_444675_trusted_account_backtest`。 |
| 2026-06-03 | 三年优化矩阵分组执行 | 完成 | `scripts/research/run_trusted_strategy_optimization_matrix.py` 新增 `--groups`，支持按 `hold_cost`、`stop_loss`、`industry`、`market_gate_position`、`adaptive` 分组执行，避免三年窗口一次性跑全矩阵耗时过长。已完成三年持仓期、进攻止损、行业约束、市场门禁/仓位、自适应切换五组矩阵，输出分别位于 `exports/signal_research/trusted_strategy_optimization_20260603_203215`、`exports/signal_research/trusted_strategy_optimization_20260603_212905`、`exports/signal_research/trusted_strategy_optimization_20260603_223619`、`exports/signal_research/trusted_strategy_optimization_20260603_224953`、`exports/signal_research/trusted_strategy_optimization_20260603_232619`。 |
| 2026-06-03 | 防守影子策略接入飞书对照 | 完成 | `export_trusted_strategy_candidates.py` 的“策略订单对照”新增 `baseline_full_liquidity_detail_hold12_shadow` 和 `baseline_full_liquidity_detail_market_gate_pos50_shadow`，分别代表“流动性质量防守策略（12日持有影子）”与“流动性质量防守策略（市场门禁50%仓位影子）”。两者只进入飞书/归档/Web 对照，不写入生产候选主策略，不替换当前进攻策略。已用本地假飞书、不写库方式验证 2026-06-02 候选导出，测试产物位于 `exports/production_candidates/20260603_234550_tiered_liquidity_then_bs_v2`。 |
| 2026-06-03 | Web 策略订单对照字段增强 | 完成 | 核心精选页“策略订单对照”新增基础策略、持有期、目标仓位和说明字段，可展示新增防守 12 日与防守半仓影子策略的关键差异；`web/app.py` 为 `base_strategy_display_name` 增加兜底中文名。已完成模板解析、后端语法、离线渲染和 5001 运行服务验证。 |
| 2026-06-04 | 生产风险档位升级 | 完成 | 新增 `--risk-profile offensive|balanced|defensive`。生产默认切为 `balanced`：`baseline_full_liquidity_detail_market_gate`、12 日持有、80% 基准仓位、最多 5 只；低市场成交环境由市场门禁把实际敞口降至约 50%。日终调度、Web 任务中心、候选导出报告、飞书通知、账户级回测汇总和核心精选页均已显示风险档位。 |

## 当前发现

- 数据覆盖已经扩展到 2026-01-05 至 2026-05-12，共 74 个评分日、394,977 条评分记录；2026-01-01 至 2026-02-12 已无低覆盖待回填交易日。
- `score_rank_daily.industry` 已全表清零空值。`300379.SZ` 因退市股维表缺失，当前使用“未知”行业兜底，避免行业约束和归因把空值误归类。
- 每日滚动事件研究（`rebalance_step=1`）仍只用于因子强弱判断，因为周期高度重叠，不能直接等同账户净值。
- 扩展样本后，每日滚动、0.15% 成本下，表现最强的是 `liq_top_20_then_model_rank` / `liq20_bs_model_rank_score_b_bonus_0pct`，总收益约 +184.27%，最大回撤约 -26.48%。但该类模型字段只覆盖 29 个可交易周期，仍不能作为全周期默认方案。
- 扩展样本后，每日滚动、0.15% 成本下，`baseline_full_liquidity_detail` 总收益约 +35.33%，但最大回撤约 -76.41%；`baseline_full_liquidity` 总收益约 -37.32%，最大回撤约 -82.23%。这说明早前短样本“纯流动性最强”的结论不稳健。
- 扩展样本后，非重叠 10 日换仓口径（`rebalance_step=10`）、0.15% 成本下，`baseline_full_score` 总收益约 +54.63%，最大回撤约 -22.17%；`baseline_full_liquidity_detail` 总收益约 +52.32%，最大回撤约 -4.28%；`tiered_liquidity_then_bs_v2` 总收益约 +49.73%，最大回撤约 -8.21%。
- `rebalance_step=10` 只有 7 个完整周期，收益方向有参考意义，但样本仍偏短。当前更合理的开发结论是：优先保留全量评分、衍生流动性、分层流动性和模型排序作为研究候选，不应直接把单一流动性排序接入生产。
- 行业硬上限和软惩罚可降低行业集中度，但在每日滚动全样本中会明显伤害收益或加深回撤；当前只建议作为风险诊断和备选风控参数，不作为默认主排序规则。
- 动态因子加权在扩展后的每日滚动样本仍表现较弱，0.15% 成本下 `baseline_full_dynamic_factor` 总收益约 -69.23%；10 日换仓口径下为正但周期太少，不建议生产化。
- 市场环境归因已输出。当前样本较短，分桶结果只作为诊断信息，不作为参数优化依据。
- 历史回填过程中出现 scikit-learn 模型版本告警：模型由 1.8.0 训练，当前运行环境为 1.6.1。回填未中断，但生产部署前应保持训练和推理环境版本一致。
- 2026-05-13 可信模式重构后，10 日换仓、0.15% 成本下，`baseline_full_dynamic_factor_industry_cap2` 总收益约 +66.02%，最大回撤约 -4.84%；`baseline_full_dynamic_factor` 总收益约 +63.60%，最大回撤约 -4.84%；`baseline_full_score` 仍为 +54.63%，最大回撤约 -22.17%。
- 2026-05-13 可信模式下，`baseline_full_liquidity_detail` 仍表现稳健：10 日换仓、0.15% 成本下总收益约 +52.32%，最大回撤约 -4.28%；每日滚动诊断下总收益约 +35.33%，但滚动口径回撤仍很深。
- 市场流动性门禁在当前 10 日换仓样本中基本未触发，因此收益与原策略接近；每日滚动中能降低部分敞口，但不能根本解决重叠周期回撤。
- 模型 walk-forward 框架已跑通，当前只有 2 个可预测月份、243 条预测样本，平均 Precision@10 约 0.30、Precision@20 约 0.35；该结果只能证明流程合规，不能作为模型生产化充分证据。

## 后续任务

- 本轮 P0-P7 研究、可信回测、候选导出、日终批量接入、Web 实盘入池、本地订单自动生成和生产操作文档均已完成。
- 旧“陈依云精选”高股息/小市值策略已隔离为 Legacy，不再参与生产选股；生产默认由“可信全量池 Top5”日终任务写入当前信号和订单草案。
- 可信全量池生产订单已加入持仓期门禁，默认与回测持有期一致：未满 10 个交易日的持仓不会因每日 Top5 变化被常规卖出。
- 当前“自动下单”实现范围是自动生成本地调仓订单草案并入库，尚未接入券商真实委托 API；如需真实券商下单，需要另起高风险生产化任务，并补成交回报、撤单、异常重试和审计设计。
- 2026-05-31 新增账户级回测后，默认生产策略 `baseline_full_dynamic_factor_industry_cap2` 在 2026-01-05 至 2026-05-29、50 万初始资金、单边成本 0.075% 口径下，期末权益约 966,468，收益约 +93.29%，最大回撤约 -20.60%，平均持仓数约 14.46，交易 268 笔。
- 同一账户级口径下，`tiered_liquidity_then_bs_v2` 收益约 +82.95%、最大回撤约 -20.87%；`baseline_full_score` 收益约 +82.23%、最大回撤约 -30.89%；`baseline_full_liquidity_detail` 收益约 +64.17%、最大回撤约 -17.25%。默认策略收益最高，但不是回撤最低。
- 在更保守的单边成本 0.10% + 单边滑点 0.10% 压力口径下，默认生产策略收益约 +87.05%、最大回撤约 -20.81%；`baseline_full_liquidity_detail` 收益约 +54.06%、最大回撤约 -17.67%。结论对执行摩擦有一定韧性，但账户回撤明显高于非重叠事件回测。
- 账户级回测显示：由于每日生成 Top5 且未满 10 日持仓不卖，真实组合会自然扩展到十余只股票，并非始终只有 Top5。后续优化重点应从“提高排序分收益”转向“限制重叠持仓、降低回撤、降低换手和建立生产影子净值监控”。
- 2026-05-31 持仓上限优化验证显示，`baseline_full_dynamic_factor_industry_cap2` 在单边成本 0.075% 口径下，账户持仓上限 5 的收益约 +110.86%、最大回撤约 -20.40%、平均持仓数 5、交易 95 笔；上限 10 的收益约 +92.14%、最大回撤约 -21.32%；上限 12 的收益约 +94.90%、最大回撤约 -21.25%；无上限基准收益约 +93.29%、最大回撤约 -20.60%。因此当前生产执行层默认使用持仓上限 5。
- 持仓上限 5 在单边成本 0.10% + 单边滑点 0.10% 压力口径下，收益约 +101.97%、最大回撤约 -20.75%，仍优于无上限压力口径的 +87.05%。当前结论是：限制账户总持仓数不仅降低组合扩散，还提升了收益/换手质量。
- 2026-05-31 持有期参数复核显示，在 `top_n=5`、`max_total_positions=5` 下，`hold_days=5` 收益约 +29.19%、最大回撤约 -31.60%、交易 180 笔；`hold_days=7` 收益约 +63.45%、最大回撤约 -17.15%、交易 134 笔；`hold_days=15` 收益约 +18.59%、最大回撤约 -34.55%、交易 66 笔。10 日持有期仍是收益/回撤综合最优，不调整生产默认。
- 2026-05-31 TopN 参数复核显示，`top_n=3,max_total_positions=3` 收益约 +67.97%、最大回撤约 -18.36%；`top_n=5,max_total_positions=3` 收益约 +81.04%、最大回撤约 -18.36%；`top_n=7,max_total_positions=5` 收益约 +89.21%、最大回撤约 -22.70%。均不优于当前 `top_n=5,max_total_positions=5` 的 +110.86%、-20.40%，因此不调整 Top5 默认。
- 2026-05-31 市场成交门禁复核显示，`baseline_full_dynamic_factor_industry_cap2_market_gate` 与当前默认策略结果完全一致，说明本样本低成交门禁未触发；暂不切换生产策略名。
- 2026-05-31 仓位比例复核显示，在 `top_n=5,hold_days=10,max_total_positions=5` 下，满仓收益约 +110.86%、最大回撤约 -20.40%；80% 仓位收益约 +77.94%、最大回撤约 -16.52%；60% 仓位收益约 +56.95%、最大回撤约 -12.30%。当前生产默认仍保持 `position_ratio=1.0`，但若实盘风险预算要求最大回撤压到约 15% 附近，可临时使用 `--position-ratio 0.8`。
- 2026-05-31 硬止损复核显示，在 `top_n=5,hold_days=10,max_total_positions=5` 满仓口径下，6% 止损收益约 +5.22%、最大回撤约 -21.96%，止损卖出 34 次；8% 止损收益约 +60.24%、最大回撤约 -13.67%，止损卖出 24 次；10% 止损收益约 +56.77%、最大回撤约 -11.63%，止损卖出 15 次。8%/10% 止损能降回撤，但收益损失过大，暂不接入默认生产订单；仅作为人工防守开关继续观察。
- 2026-05-31 全策略事件研究显示，0.15% 成本下 `liq20_bs_model_rank_score_b_bonus_0pct` 与 `liq_top_20_then_model_rank` 收益约 +176.31%、最大回撤约 -26.48%，但 `pit_status=model_risk`，原因是 `bs_model_rank_score` 仅 227,629 行覆盖，且模型版本从历史日期回填到 2026-01-05，不能作为生产收益结论。
- 2026-05-31 全可信策略账户级 T+1 回测显示，当前默认 `baseline_full_dynamic_factor_industry_cap2` 与其 market gate 版本并列第一：期末权益约 1,054,299，收益约 +110.86%，最大回撤约 -20.40%，日胜率约 64.89%，交易 95 笔。market gate 版本结果完全一致，说明本样本门禁未触发。
- 2026-05-31 全可信策略账户级对照显示，低回撤候选为 `baseline_full_liquidity_detail`/`liq_top_20_then_liquidity_detail` 系列，收益约 +85.24%、最大回撤约 -16.38%；收益低于默认策略，但回撤更低，可作为防守模式候选。
- 2026-05-31 最近 8 个评分日 `industry`、`bs_model_prob`、`bs_model_rank_score`、`bs_model_risk_score` 均无缺失；此前模型字段缺失问题当前已恢复正常。
- 2026-06-01 最近一年自适应硬切换回测显示，`adaptive_style_switch` 期末权益约 778,429，收益约 +55.69%，最大回撤约 -23.64%，切换 22 次；低于固定进攻策略 `tiered_liquidity_then_bs_v2` 的期末权益约 1,544,720、收益约 +208.94%、最大回撤约 -23.28%。当前切换器只适合继续研究和影子盘观察，不应替换生产默认策略。
- 2026-06-03 自适应动态仓位回测显示，`adaptive_style_switch_dynamic_position` 最近一年期末权益约 730,623，收益约 +46.12%，年化约 +49.17%，最大回撤约 -17.80%，平均目标仓位约 76.77%；相比原 `adaptive_style_switch` 收益下降约 9.56 个百分点，但最大回撤从 -23.64% 改善到 -17.80%。该变体可作为“风险预算/降仓研究”继续观察，暂不替换生产进攻策略。
- 2026-06-03 飞书影子对照已纳入 `adaptive_style_switch_dynamic_position`。已用假飞书发送验证通知正文，能显示“市场风格自适应切换策略（动态仓位）”、当前状态、底层中文策略名、目标仓位和仓位原因；验证未写库、未真实推送，临时导出目录已清理。
- 2026-06-03 策略订单对照已从“只在飞书正文展示”升级为“飞书正文 + 文件归档”。已用假飞书导出验证五个对照策略均生成汇总、候选、订单和 Markdown/JSON 报告，其中 `adaptive_style_switch_dynamic_position` 汇总行包含动态仓位比例；验证未写库、未真实推送，临时导出目录已清理。
- 2026-06-03 核心精选 Web 已接入策略订单对照归档展示。页面已重启并验证可正常渲染“策略订单对照”面板；由于当前最新正式候选包生成早于该归档功能，页面暂显示“暂无策略订单对照归档”，下一次带飞书通知的候选导出后会自动展示归档内容。
- 2026-06-03 三年评分数据就绪检查初次显示：`2023-01-01` 至 `2026-06-02` 窗口共有 824 个交易日，行情覆盖 824 日，但评分覆盖仅 340 日；缺评分/低评分行数交易日 484 个，核心字段异常日 0 个，`bs_model_*` 残留日 2 个（2026-06-01、2026-06-02）。
- 2026-06-03 已清理 2026-06-01 至 2026-06-02 的 `bs_model_*` 残留，并对 10,323 行重算规则口径 B 点增强分。复跑三年就绪检查后，模型字段残留日为 0，核心字段异常日为 0；三年可信回测仍不满足条件，唯一主要缺口是 2023-2024 缺评分/低评分行数交易日 484 个，需补齐后再跑三年账户级 T+1 回测。
- 2026-06-03 2023-2024 补齐 dry-run 显示：2023-2024 共 484 个交易日、行情覆盖 484 日、评分覆盖 0 日，B/S 检测历史批次为 0，因此该窗口只能补齐规则类 B 点增强字段，不能伪造历史 B 点候选事件。
- 2026-06-03 2023-2024 小样本试跑显示：2023-01-03 至 2023-01-05 单日评分耗时约 67-71 秒，评分行数约 4,774-4,776，覆盖当日行情股票数约 96.74%。该覆盖率符合既有评分 universe 过滤口径，因此三年检查的行数质量阈值已改为 `min(5000, 当日行情股票数×95%)`。
- 2026-06-03 2023-2024 全量补齐完成后，三年窗口评分覆盖为 824/824，就绪日 824，缺评分交易日 0，低覆盖日 0，核心字段异常日 0，模型字段残留日 0。2023-2024 没有历史 B/S 检测批次，因此该窗口只补齐规则类 B 点增强字段，不伪造历史 B 点候选事件。
- 2026-06-03 完整三年账户级 T+1 回测显示，最近一年高收益策略不能直接外推到三年。按 50 万初始资金、Top5、持有 10 个交易日、最多 5 只、单边成本 0.075%、无滑点、`min_pool_size=0` 的完整评分日口径，六个可信策略全部为负收益；其中 `baseline_full_liquidity_detail` 相对最好，期末权益约 283,045，收益约 -43.39%，年化约 -16.01%，最大回撤约 -75.27%；`adaptive_style_switch_dynamic_position` 次之，期末权益约 268,830，收益约 -46.23%，最大回撤约 -66.46%；当前生产进攻策略 `tiered_liquidity_then_bs_v2` 三年收益约 -70.63%，最大回撤约 -94.20%。该结果说明后续优化重点应优先做市场状态门禁、降仓/止损和样本外风格切换，而不是继续单纯放大进攻策略。
- 2026-06-03 三年持仓期矩阵显示，在 8/10/12/15 日、单边成本 0.075%、无滑点口径下，相对最好的组合是 `baseline_full_liquidity_detail` 持有 12 日：期末权益约 489,717，收益约 -2.06%，年化约 -0.64%，最大回撤约 -65.17%。15 日同策略收益约 -7.49%，10 日同策略收益约 -43.39%。这说明长期窗口中“流动性质量防守 + 更长持有期”显著优于当前进攻满仓 10 日，但最大回撤仍过高，不能直接作为最终生产策略。
- 2026-06-03 三年进攻策略硬止损矩阵显示，8%/10%/12%/15% 止损均未改善 `tiered_liquidity_then_bs_v2` 的长期收益回撤比。无止损收益约 -70.63%、最大回撤约 -94.20%；8% 止损收益约 -75.34%、最大回撤约 -90.45%，且止损卖出 238 次、换手升至约 205.85。结论：硬止损暂不适合作为进攻策略默认生产规则，只能保留为人工预警或另行研究移动止损/组合降仓。
- 2026-06-03 三年行业约束矩阵显示，`tiered_liquidity_then_bs_v2_industry_cap1` 和行业惩罚能把最大行业持仓数从 4 降到 2、平均最大行业权重从约 43.85% 降到约 30.95%，最大回撤从 -94.20% 改善到 -88.50%；但收益恶化到约 -79.86%/-80.02%，Calmar 也变差。结论：行业约束可用于风险归因和告警，不适合作为当前进攻策略的默认替代。
- 2026-06-03 三年市场门禁/仓位矩阵显示，`baseline_full_liquidity_detail_market_gate` 在 50% 仓位下表现相对稳健：期末权益约 475,270，收益约 -4.95%，年化约 -1.54%，最大回撤约 -43.42%，平均总敞口约 48.74%。70% 仓位同策略收益约 -17.27%、最大回撤约 -54.17%；100% 仓位收益约 -43.39%、最大回撤约 -75.27%。结论：防守策略 + 半仓是当前三年窗口中回撤控制较好的生产影子候选，但仍未转正，不能直接替换收益优先生产策略。
- 2026-06-03 三年自适应切换复核显示，`adaptive_style_switch_dynamic_position` 收益约 -46.23%、最大回撤约 -66.46%、平均目标仓位约 72.13%，优于硬切换 `adaptive_style_switch` 的收益约 -80.08%、最大回撤约 -88.72%，但仍弱于防守半仓和防守 12 日持有。结论：动态仓位自适应可继续作为影子盘/告警逻辑，不应直接生产化；硬切换版本需要重构规则后再评估。
- 2026-06-03 防守半仓和防守 12 日已接入生产前置影子对照。飞书通知会在“策略订单对照”中显示两条新增影子策略的候选、订单、基础策略、持有期、目标仓位和三年研究备注；`trusted_strategy_order_detail_summary.csv` 也会记录 `base_strategy`、`hold_days`、`position_ratio`、`total_equity_used` 和 `shadow_note`。测试验证中，12 日防守影子资金基数 500,000、持有期 12 日；防守半仓影子资金基数 250,000、目标仓位 50%。
- 2026-06-03 Web 核心精选页已能展示防守影子策略对照差异：基础策略、持有期、目标仓位和研究说明会直接出现在“策略订单对照”表格中。离线渲染验证确认页面包含“流动性质量防守策略（12日持有影子）”“12日”“50%”“市场门禁50%仓位影子”等关键文本；5001 运行服务已重启并验证页面实际 HTML 包含“基础策略”“持有期”“目标仓位”“说明”表头。当前页面引用的最新正式归档仍是旧导出，下一次真实日终带飞书通知的候选导出后会自动显示新增影子行。
- 2026-06-04 生产默认口径已从单一进攻满仓切换为 `balanced` 风险档位。三年 T+1 账户级回测优先级高于最近一年高收益结果；`tiered_liquidity_then_bs_v2` 保留为 `offensive` 档和订单对照，不再作为未经门禁的长期满仓默认。
