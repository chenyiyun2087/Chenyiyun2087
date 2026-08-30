# tushare_stock 再次数据审查

- 审查时间：2026-08-16 08:05:11（Asia/Shanghai）
- 数据截止日：2026-08-14
- 范围：行情、复权因子、标签、Universe、评分、B/S、PIT 快照、ETL watermark、质量门禁
- 方式：只读 SQL 检查；未执行数据库写入

## 已通过

- 最新 `meta_production_pit_quality_run_v2`：`20260814`，9/9 数据集通过，0 个阻断问题。
- `dwd_tradeability_snapshot_di`、`dwd_daily_market_snapshot`、`dwd_limit_price_di`、`dwd_suspension_di`、`dwd_market_cap_daily` 已更新到 2026-08-14。
- `dwd_equity_daily_bar_v2` 与 `dwd_security_lifecycle_daily_v2` 已生成，2026-08-14 的关键字段无 NULL。
- `dwd_daily`、`dwd_daily_basic`、`dwd_adj_factor`、`dwd_stock_label_daily`、Universe、评分、B/S 的主要交易日缺口为 0。
- `ods_stk_factor` 此前的 16 个缺失交易日已补齐。
- 最新交易日价格、OHLC、成交量、评分范围、B/S 信号和已检查重复键均无异常。
- 基本面 PIT 的 `visible_date_max` 无未来日期，关键基本面值无 NULL。

## 遗留风险

1. `dwd_stock_daily_standard` 仍缺 167 个早期交易日，集中在 2010-04-27 至 2010-12-31。`dwd_daily` 原始行情完整；若正式研究从 2011 年或 2013 年开始，该缺口可隔离，否则需要继续补齐。
2. `dws_stock_fundamental_pit_daily` 相对标准行情少 83 个标的，当前覆盖约 98.50%；未发现未来可见日期，但 `visible_datetime` 当前全部为空。
3. `meta_formal_data_snapshot_v2` 当前 4 条诊断快照均为 `REJECTED`，其阻断原因与来源 `QUALIFIED`/可见性契约有关。虽然生产 PIT 质量门禁已 PASS，仍需确认正式消费链路使用的是 PASS 门禁还是这些诊断快照。
4. `dwd_stock_daily_standard`、`dwd_adj_factor`、`dwd_stock_label_daily`、`ads_universe_daily` 最新数据仍为 `UNVERIFIED / UNVERIFIED`；`dim_trade_cal` 的 `available_at` 仍为空。`dwd_daily` 与 `dwd_daily_basic` 已为 `QUALIFIED / PROVIDER_SLA`。
5. `meta_etl_watermark` 中 `dwd_daily`、`dwd_daily_basic`、`dwd_adj_factor` 的 watermark 仍为 2013-10-22，`ads_index_daily` 仍为 2026-04-08；需要确认这些是历史回补游标还是未同步的运行状态。

## 结论

当前 `tushare_stock` 的生产 PIT 质量门禁已通过，最新行情与主要数据链路可条件使用。正式 PIT 回测仍应优先使用已生成的 V2 快照，并明确排除 2010 年早期缺口；若要求全历史可追溯，仍需处理上述 167 个交易日、来源血缘和诊断快照状态问题。
