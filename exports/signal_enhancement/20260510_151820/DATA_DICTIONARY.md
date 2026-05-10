# 字段字典

## 标识字段

- `event_date`：首次 B 点出现日期或 B 点有效日。
- `event_uid`：首次 B 点事件唯一 ID，可用于连接价格路径表。
- `symbol` / `ts_code` / `name`：股票代码、带交易所后缀的代码和名称。CSV 被 Excel 打开时优先使用 `ts_code`，避免前导 0 丢失。
- `event_seq_for_symbol`：同一股票第几次出现 B 点事件。
- `sample_split`：按时间切分的 train / validation / test，避免随机切分导致时间泄漏。

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
- `claude_score`：Claude 六维评分，0-100 标尺。
- `bs_score`：当前系统的 B 点增强分，0-100 标尺。
- `bs_entry_score`：买点后节奏分，偏好买点后温和确认、不过度追高。
- `close_price`：事件日收盘价。
- `buy_point_close`：买点日收盘价。
- `price_change_ratio`：事件日相对买点价涨幅百分比。
- `is_limit_up`：事件日是否涨停。
- `pool_type`：当前系统分层，`TRADE` / `WATCH` / 空。
- `is_self_selected`：是否在自选池。

## 标签字段

- `ret_1` / `ret_3` / `ret_5` / `ret_10` / `ret_20`：事件后第 N 个交易日收益。
- `max_ret_N`：事件后 N 个交易日窗口内最大收益。
- `mdd_N`：事件后 N 个交易日窗口内最大不利浮亏。
- `hit_N_5pct` / `hit_N_10pct`：N 日内是否曾达到 +5% / +10%。
- `days_to_10pct_within_N`：N 日内首次达到 +10% 所需交易日数；空表示未达到或数据不足。

## 价格路径字段

- `rel_ret_d0` 至 `rel_ret_d20`：事件后第 N 个交易日相对事件日收盘价的收益，`d0=0`。
