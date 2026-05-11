# 字段字典

## 标识字段

- `event_date`：首次 B 点出现日期或 B 点有效日。
- `event_uid`：首次 B 点事件唯一 ID，可用于连接价格路径表。
- `symbol` / `ts_code` / `name`：股票代码、带交易所后缀的代码和名称。CSV 被 Excel 打开时优先使用 `ts_code`，避免前导 0 丢失。
- `event_seq_for_symbol`：同一股票第几次出现 B 点事件。
- `sample_split`：按时间切分的 train / validation / test，避免随机切分导致时间泄漏。
- `split_hit_N_10pct`：每个目标 horizon 的独立切分列，仅对该 horizon 标签完整的行分配 train / validation / test，窗口边界处标记为 `embargo`。

## 当时可见信号字段

- `buy_signal_description`：B 点检测描述。
- `total_b_points` / `total_s_points`：图上历史 B/S 点数量。
- `buy_points_count` / `sell_points_count`：当日识别出的 B/S 点数量。
- `score`：Technical 总分。
- `base_score` / `penalty`：Technical 基础分与风险扣分。
- `s_trend`：趋势项。
- `s_breakout`：突破项。
- `s_volume`：量能项。
- `s_rs`：近 20 日相对强弱项。
- `s_contraction`：波动收敛项，当前分值越高代表越收敛。
- `s_liquidity`：流动性项。
- `opt_score`：因子优化分，当前通常是 0-10 标尺。
- `opt_momentum` / `opt_value` / `opt_quality` / `opt_technical` / `opt_capital` / `opt_chip` / `opt_size`：Factor Optimizer 分类因子子分项。
- `claude_score`：Claude 六维评分，0-100 标尺。
- `score_momentum` / `score_value` / `score_quality` / `score_technical` / `score_capital` / `score_chip`：Claude 六维子分项。
- `bs_score`：当前系统的 B 点增强分，0-100 标尺。
- `bs_entry_score`：买点后节奏分，偏好买点后温和确认、不过度追高。
- `bs_score_v2`：规则增强版 B 点分，强化 RS、流动性、突破质量、节奏确认和风险约束。
- `bs_score_v2_label`：`强买` / `观察` / `剔除` 分层。
- `bs_research_score`：基于 2026 年以来样本研究得到的建议分，强调 `bs_score_v2` 与 `rs_liquidity_combo` 共振。
- `bs_research_label`：`强观察` / `普通观察` / `回避`，用于页面研究提示，不等同于自动交易指令。
- `bs_research_reason`：研究建议的主要原因，例如强势流动性共振、追高风险、流动性偏弱等。
- `bs_gate_score` / `bs_gate_pass` / `bs_gate_label` / `bs_gate_reason`：两阶段交易门禁，先判断可买性，再进入排序。
- `bs_model_prob`：模型对目标 `hit_N_10pct` 的校准命中概率。
- `bs_model_expected_mdd` / `bs_model_risk_score`：模型回撤头输出，前者为预期最大回撤，后者为 0-100 风险友好分。
- `bs_model_rank_score` / `bs_model_version`：模型综合排序分与模型版本。
- `bs_consensus_score` / `bs_consensus_label` / `bs_consensus_reason`：规则、模型和门禁融合后的最终综合建议。
- `score_*_gap`、`score_dispersion`：不同评分体系之间的分歧特征。
- `rs_liquidity_combo`、`breakout_volume_combo`：组合交互特征。
- `overextended_flag`、`pullback_flag`：买点后过热或破位提示。
- `market_hs300_pct_chg` / `market_hs300_ret_5` / `market_hs300_ret_20`：沪深300当日涨跌幅、近 5/20 日收益。
- `market_bs_count` / `market_bs_ratio`：当日评分池中 B 点候选数量与占比，用于衡量信号拥挤度。
- `market_limit_up_rate` / `market_avg_score` / `market_avg_v2` / `market_avg_research_score`：当日市场横截面环境。
- `market_regime`：基于沪深300 20 日收益和当日跌幅的简化市场状态，`risk_on` / `neutral` / `risk_off`。
- `close_price`：事件日收盘价。
- `buy_point_close`：买点日收盘价。
- `price_change_ratio`：事件日相对买点价涨幅百分比。
- `is_limit_up`：事件日是否涨停。
- `pool_type`：当前系统分层，`TRADE` / `WATCH` / 空。
- `is_self_selected`：是否在自选池。

## 标签字段

- `ret_1` / `ret_3` / `ret_5` / `ret_10` / `ret_20` / `ret_60`：事件后第 N 个交易日收益。
- `max_ret_N`：事件后 N 个交易日窗口内最大收益。
- `mdd_N`：事件后 N 个交易日窗口内最大不利浮亏。
- `hit_N_5pct` / `hit_N_10pct`：N 日内是否曾达到 +5% / +10%。
- `days_to_10pct_within_N`：N 日内首次达到 +10% 所需交易日数；空表示未达到或数据不足。

## 价格路径字段

- `rel_ret_d0` 至 `rel_ret_d60`：事件后第 N 个交易日相对事件日收盘价的收益，`d0=0`。
