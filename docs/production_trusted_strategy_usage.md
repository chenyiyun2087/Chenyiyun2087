# 可信全量池策略生产使用步骤

## 当前推荐策略

当前生产默认使用风险档位 `adaptive`，不再使用固定单一满仓口径：

- 主策略：`adaptive_market_style`（市场风格识别 + 底层策略切换 + 动态仓位）。
- 默认映射：`attack` -> `tiered_liquidity_then_bs_v2`，`balanced` -> `baseline_full_liquidity_detail_market_gate`，`defensive` -> `baseline_full_liquidity`。
- 组合：Top5，账户总持仓数上限 5。
- 持有：10 个交易日。
- 仓位：由市场风格状态动态调整到约 50% / 80% / 100%。
- 执行层：未满持有期的持仓不卖、不减仓，若持仓上限已满，则不再因每日新 Top5 额外扩仓。
- 风控：以三年 T+1 账户级回测为最高优先级验收口径；不使用被标记为 `model_risk` 的模型排序策略。

备选复核策略：

- `baseline_full_score`：最朴素综合分排序，适合动态权重历史样本不足时做兜底。
- `baseline_full_liquidity_detail`：衍生流动性排序，回撤较低，可作为防守复核。
- `baseline_full_liquidity_detail_hold12_shadow`：防守策略 12 日持有影子对照。
- `baseline_full_liquidity_detail_market_gate_pos50_shadow`：防守市场门禁 50% 仓位影子对照。
- `tiered_liquidity_then_bs_v2`：流动性分层后使用 B 点增强分，仅作为进攻观察/对照，不作为未经门禁的长期满仓默认。
- `baseline_full_liquidity_shadow`：纯流动性防守影子对照。
- `adaptive_style_shadow`：自适应生产策略影子对照，展示当天状态、底层策略和目标仓位。
- `adaptive_style_switch`：旧市场风格自适应硬切换研究策略，仅用于历史对照。

## 每日生产流程

日终批量任务已自动包含候选导出。独立调度器 `scheduler.py` 的 `daily_pipeline` 顺序为：

1. 等待当日行情数据就绪。
2. 执行 `eastmoney/run_strategy.py`。
3. 执行 `scoreRank/run_daily.py --date <交易日> --force`，评分日期显式绑定到本次 pipeline 交易日。
4. 执行 `scripts/backfill_score_rank_daily_industry.py --execute`，仅回填当日空行业。
5. 执行 `scoreRank/cli/build_bs_consensus.py --date <交易日>`。
6. 执行 `scripts/ops/export_trusted_strategy_candidates.py --risk-profile adaptive --write-db --emit-orders --notify-feishu --max-total-positions 5`，导出可信全量池候选名单，并自动写入候选表、Web 股票池、核心精选信号和本地订单表；订单草案生成后发送飞书通知。
7. 执行 `scripts/ops/run_trusted_strategy_shadow_monitor.py --execution-date <交易日> --write-db --notify-feishu --allow-empty`，复盘上一信号日订单在本交易日开盘的可成交性、涨跌停风险和滑点，并发送飞书通知。
8. 继续执行 M1、M8 和实盘快照同步。

Web 任务中心也已注册“可信全量池候选导出”和“可信策略影子盘监控”，默认交易日 `21:25`、`21:28` 执行，可手动触发或调整调度时间。

代码更新后需要重启独立 `scheduler.py` 进程和 Web 服务，新的流水线步骤与 Web 任务才会生效。

以下步骤用于人工复核、手动补跑或排查：

1. 更新行情、B/S 点检测、外部特征和全量评分。

   可通过 Web 任务入口执行现有“全A股评分/chenyiyunSelected”相关任务；命令行入口为：

   ```bash
   CHENYIYUN_DB_PASSWORD=你的密码 python3 scoreRank/run_daily.py
   ```

2. 确认最新评分日期和行业字段正常。

   ```sql
   SELECT MAX(trade_date) FROM score_rank_daily;
   SELECT trade_date, COUNT(*) rows, SUM(industry IS NULL OR TRIM(industry) = '') empty_industry
   FROM score_rank_daily
   WHERE trade_date = (SELECT MAX(trade_date) FROM score_rank_daily)
   GROUP BY trade_date;
   ```

   如果 `empty_industry` 不为 0，先执行行业回填：

   ```bash
   CHENYIYUN_DB_PASSWORD=你的密码 python3 scripts/backfill_score_rank_daily_industry.py --execute
   ```

3. 导出可信策略候选名单。

   ```bash
   CHENYIYUN_DB_PASSWORD=你的密码 \
   python3 scripts/ops/export_trusted_strategy_candidates.py \
     --risk-profile adaptive \
     --strategy adaptive_market_style \
     --top-n 5 \
     --max-total-positions 5 \
     --write-db \
     --emit-orders \
     --notify-feishu
   ```

   脚本会输出：

   - `trusted_strategy_candidates.md`：人工复核用报告。
   - `trusted_strategy_candidates.csv`：候选明细和建议权重。
   - `trusted_strategy_candidates.json`：机器可读结果。
   - `trusted_strategy_dynamic_weights.csv`：信号日动态因子权重记录。
   - `trusted_strategy_market_environment.csv`：市场流动性环境记录。

   同时会写入：

   - `ads_trusted_strategy_candidates`：可信候选明细。
   - `stock_pools` / `stock_pool_items`：`TRUSTED_FULL_POOL_TOP5` 股票池。
   - `ads_chenyiyun_selected_signals`：核心精选页展示的买卖信号。
   - `ads_local_strategy_orders`：本地调仓订单草案。
   - `ads_trusted_strategy_shadow_fills`：上一信号日订单在执行日开盘价的影子成交明细。
   - `ads_trusted_strategy_shadow_daily`：影子盘可成交、不可成交、滑点和告警汇总。

   订单生成时会同时执行两个账户级约束：

   - 未满 `--hold-days` 的持仓锁定，不卖出、不减仓，并先占用组合预算。
   - 账户总持仓数不超过 `--max-total-positions`。当前默认值为 5；若锁定持仓已经占满上限，则当日只允许卖出到期/未入选持仓，不再新增买入。
   - `--risk-profile` 控制生产风险档位。当前默认 `adaptive`：按 T 日市场风格切换底层策略，并把实际敞口调到约 50% / 80% / 100%。
   - `--position-ratio` 可覆盖风险档位的目标总仓位。人工降风险时优先使用 `defensive` 或显式降低该参数。

   生成本地订单前会强制校验前置条件：当日全量评分行数、空行业、总分、流动性分、B点增强分、B点综合分、账户权益。任一条件不满足，脚本返回非 0，日终批量任务失败。

4. 人工复核候选名单。

   必查项：

   - 是否停牌、临停、涨跌停无法成交。
   - 是否存在重大公告、退市风险、ST 或交易权限限制。
   - `industry` 是否为空。
   - `effective_weight` 合计是否接近 100%；若低于 100%，通常说明市场门禁或仓位规则降仓。
   - `market_liquidity_bucket` 是否为 `low_liquidity`；若是，建议降低总仓位。
   - 若动态权重历史样本不足，改用 `baseline_full_score` 或 `baseline_full_liquidity_detail` 生成名单交叉验证。

   核心精选页面 `/chenyiyun/selected` 同时展示最新候选、每日信号和影子盘成交监控。影子盘面板用于检查上一信号日在 T+1 开盘是否真实可成交，重点关注不可成交、接近涨跌停和大滑点警告。

5. 下一交易日执行。

   - 在 T 日收盘后生成名单。
   - T+1 开盘附近建仓。
   - 每只股票按 `effective_weight` 分配目标仓位。
   - 若某只股票无法成交，不追高补买；可按现金保留，或用同策略下一名替补名单手动复核后替换。

6. 持仓管理。

- 当前 `balanced` 档计划持有 12 个交易日；若人工切换 `offensive` 档，才回到 10 个交易日。
- 账户总持仓数原则上保持不超过 5 只；除非已有锁定持仓因数据异常超过上限，否则新订单不会继续扩仓。
- 到期日收盘前后退出，或按现有账户风控规则提前退出。
   - 若已有日内止损、涨停检查、持仓更新任务，继续照常运行：

   ```bash
   CHENYIYUN_DB_PASSWORD=你的密码 python3 scripts/ops/run_chenyiyun_position_update.py
   CHENYIYUN_DB_PASSWORD=你的密码 python3 scripts/ops/run_chenyiyun_limitup_check.py
   ```

7. 记录生产结果。

   每个调仓周期至少记录：

   - 信号日、执行日、退出日。
   - 候选文件路径。
   - 实际成交价、滑点、无法成交原因。
   - 组合收益、最大回撤、是否触发止损。
   - 与回测假设的差异。

## 未来函数控制

生产候选导出脚本遵守以下约束：

- 评分数据只读取到 `--date` 指定日期，默认最新评分日。
- 行情数据只读取到信号日当天，不读取 T+1 或退出日价格。
- 动态因子权重只使用“退出日早于当前信号日”的历史样本。
- 默认只允许 `pit_status=trusted` 的策略；模型版本穿越风险策略不会作为主策略。

## 当前限制

- 最近一年强势策略不能直接外推到三年窗口。三年 T+1 账户级回测显示，未经门禁的进攻策略回撤极深，因此当前生产默认转为 `balanced` 风险档。
- 账户级验证显示，加入 `max_total_positions=5` 后能显著抑制每日 Top5 滚动带来的持仓扩散；但长期窗口仍需资金比例控制和人工风控。
- 仓位比例是主要风险预算旋钮：当前默认不再满仓，`balanced` 以 80% 为基准，`defensive` 以 50% 为基准。
- 硬止损目前不作为默认订单规则。账户级验证中 8%/10% 止损能把最大回撤降到约 -13.67%/-11.63%，但收益降到约 +60.24%/+56.77%；若后续实盘风险偏好转防守，可先用模拟盘或人工单独执行，不直接写入日终默认买卖。
- 脚本会自动生成本地调仓订单草案并发送飞书通知；目前没有接入券商真实委托 API，不会向券商柜台发送订单。
- 模型排序相关策略仍需更多 walk-forward 样本验证，暂不作为生产默认方案。
