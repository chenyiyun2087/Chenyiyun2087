# 外部专家策略评估材料说明

生成时间：2026-06-02  
项目路径：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087`  
材料目录：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/exports/reports/external_strategy_review_20260602`

## 1. 评估目标

本材料包用于请外部专家评估当前 A 股量化选股系统的策略逻辑、风险收益表现、未来函数控制和生产化风险，并提出优化升级建议。

当前生产策略已经切换为：`tiered_liquidity_then_bs_v2`。该策略为收益优先的进攻型策略，当前以 50 万资金、Top5、目标满仓、每只约 20% 权重生成本地订单草案，并通过飞书推送。

## 2. 系统主链路

日终生产主链路：

1. Sina / B/S 信号与全市场行情数据进入评分体系。
2. `scoreRank.cli.run_daily` 生成每日全量股票评分，写入 `score_rank_daily`。
3. 行业字段与 B 点规则增强字段补齐。
4. `scripts/ops/export_trusted_strategy_candidates.py` 基于 T 日评分导出可信策略 Top5。
5. 生成本地订单草案，写入 `ads_local_strategy_orders` 与 `ads_chenyiyun_selected_signals`。
6. 飞书推送候选、订单和策略对照信息。
7. 核心精选页面查看：`http://192.168.50.88:5001/chenyiyun/selected`。

关键文档：

- `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/README.md`
- `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/AGENTS.md`
- `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/docs/00_project_overview/RUNBOOK.md`
- `/Users/chenyiyun/PycharmProjects/Chenyiyun2087/docs/03_backtest_reports/BACKTEST_INDEX.md`

## 3. 策略池逻辑

当前重点评估的可信策略包括：

| 策略 | 定位 | 股票池 | 排序字段 | 生产状态 |
|---|---|---|---|---|
| `tiered_liquidity_then_bs_v2` | 进攻策略 | 流动性分层池 | `bs_score_v2` | 当前生产 |
| `baseline_full_dynamic_factor_industry_cap2` | 均衡策略 | 全量池 | `dynamic_factor_score`，行业最多2只 | 历史生产/对照 |
| `baseline_full_liquidity_detail` | 防守/流动性质量策略 | 全量池 | `liquidity_detail_score` | 防守对照 |
| `baseline_full_score` | 综合分兜底基准 | 全量池 | `score` | 基准对照 |
| `adaptive_style_switch` | 研究型选择器 | 每日硬切换一个底层策略 | adaptive | 未生产化 |

代码锚点：

- 策略定义：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/research_full_pool_liquidity_strategies.py`
- 账户级回测：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/research_trusted_strategy_account_backtest.py`
- 生产候选/订单/飞书：`/Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/export_trusted_strategy_candidates.py`

## 4. 回测口径

可信回测口径：

- 初始资金：500,000 元。
- 组合：Top5。
- 持有周期：10 个交易日。
- 交易执行：T 日信号，T+1 开盘价成交。
- 交易成本：单边 0.075%，滑点 0。
- 持仓上限：最多 5 只。
- 禁用历史回填的 `bs_model_*` 字段。
- 动态权重和自适应策略只允许使用 `exit_date < signal_date` 的历史样本。

## 5. 收益风险摘要

完整表见：`strategy_risk_return_summary.csv`。

### 最近一年

| 策略 | 总收益 | 年化 | 最大回撤 | 日胜率 | 最终资金 |
|---|---:|---:|---:|---:|---:|
| `tiered_liquidity_then_bs_v2` | 208.94% | 228.49% | -23.28% | 57.50% | 1,544,720 |
| `baseline_full_liquidity_detail` | 69.19% | 74.10% | -30.80% | 52.08% | 845,960 |
| `baseline_full_dynamic_factor_industry_cap2` | 65.58% | 70.18% | -32.90% | 55.83% | 827,899 |
| `adaptive_style_switch` | 55.69% | 59.48% | -23.64% | 53.75% | 778,429 |
| `baseline_full_score` | 30.64% | 32.55% | -40.87% | 52.08% | 653,208 |

### 关键观察

- `tiered_liquidity_then_bs_v2` 最近一年和最近三个月收益第一，但最近半年亏损明显，说明它是强进攻、强风格依赖策略。
- `baseline_full_liquidity_detail` 在 2025 年初至今窗口表现相对最好，更适合作为防守切换候选。
- `baseline_full_dynamic_factor_industry_cap2` 近期中上，但长窗口表现不佳，说明动态因子仍需进一步风控约束。
- `baseline_full_score` 是基础基准，不适合当前生产主策略。
- `adaptive_style_switch` 作为研究型选择器尚未跑赢固定进攻策略，但可能作为风控框架继续优化。

## 6. 当前生产状态

当前生产候选来源：`exports/production_candidates/20260602_112141_tiered_liquidity_then_bs_v2/`

当前订单草案：

| 股票代码 | 名称 | 买卖 | 价格 | 目标股数 | 目标权重 |
|---|---|---|---:|---:|---:|
| `636` | 风华高科 | BUY | 55.05 | 1800 | 20.0% |
| `300433` | 蓝思科技 | BUY | 41.83 | 2300 | 20.0% |
| `600027` | 华电国际 | BUY | 5.92 | 16800 | 20.0% |
| `600795` | 国电电力 | BUY | 5.25 | 19000 | 20.0% |
| `600863` | 内蒙华电 | BUY | 7.92 | 12600 | 20.0% |

当前生产候选 Top5：

| 排名 | 股票代码 | 名称 | 行业 | 排序分 | 权重 |
|---:|---|---|---|---:|---:|
| 1 | `300433` | 蓝思科技 | 元器件 | 73.98 | 20.0% |
| 2 | `600027` | 华电国际 | 火力发电 | 73.48 | 20.0% |
| 3 | `600863` | 内蒙华电 | 火力发电 | 73.18 | 20.0% |
| 4 | `000636` | 风华高科 | 元器件 | 73.17 | 20.0% |
| 5 | `600795` | 国电电力 | 火力发电 | 73.01 | 20.0% |

## 7. 未来函数与可信性说明

需要专家重点关注未来函数控制：

- 生产信号只使用 T 日已经落库的 `score_rank_daily` 字段。
- 交易模拟按 T+1 开盘执行，不使用 T+1 之后数据决定 T 日买卖。
- 可信回测不使用 `bs_model_*` 历史回填字段。
- 动态因子权重、自适应策略选择，只能使用已完成持有期且 `exit_date < signal_date` 的历史样本。
- 严格三年回测仍缺 2023-2024 的评分数据，因此不能将最近一年高收益直接当作三年稳定结论。

## 8. 希望专家重点回答的问题

1. 当前 `tiered_liquidity_then_bs_v2` 是否适合满仓生产？如果不适合，应如何设定仓位上限？
2. 是否应增加自动切换到 `baseline_full_liquidity_detail` 的风控逻辑？触发条件如何设计？
3. 风格切换如何避免未来函数，尤其如何处理滚动收益、回撤和策略选择样本？
4. 是否需要加入止损、降仓、行业集中度、市场流动性门禁？
5. 是否应优先补齐 2023-2024 评分数据做严格三年 T+1 回测？
6. 当前交易成本、滑点、整数手、T+1 开盘成交口径是否足够接近实盘？

## 9. 材料阅读顺序建议

1. `EXPERT_REVIEW_BRIEF.md`
2. `strategy_risk_return_summary.csv`
3. `current_production_orders.csv`
4. `current_production_candidates.csv`
5. 四个账户级回测包中的 `trusted_account_backtest_report.md` 与 `trusted_account_backtest_trades.csv`
6. 策略定义代码和生产导出脚本
