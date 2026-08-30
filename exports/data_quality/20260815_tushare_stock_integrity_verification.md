# tushare_stock 数据修复验收报告

- 验收时间：2026-08-15 23:15:39（Asia/Shanghai）
- 验收方式：只读 SQL / 元数据检查；未执行数据库写入
- 数据截止日：2026-08-14

## 已确认通过

- `dwd_stock_daily_standard`：2011-01-04 至 2026-08-14，交易日缺口 0。
- `dwd_stock_label_daily`：2011-01-04 至 2026-08-14，交易日缺口 0。
- `dwd_daily`、`dwd_daily_basic`、`dwd_adj_factor`：各自区间交易日缺口 0。
- `ads_universe_daily`、`ads_stock_score_daily`、`ads_stock_bs_signal`：各自区间交易日缺口 0。
- 2026-08-14 最新行情：价格、OHLC、成交量、评分范围、B/S 信号枚举均无异常。
- 核心表最新交易日覆盖已恢复；`dwd_tradeability_snapshot_di`、`dwd_daily_market_snapshot`、`dwd_limit_price_di`、`dwd_suspension_di`、`dwd_market_cap_daily` 与标准行情相比无缺失键。
- `dws_stock_fundamental_pit_daily` 相对标准行情缺 83 个标的，覆盖约 98.50%。
- 已检查重复键的 `dwd_suspension_di`、`ods_margin_detail`、`ads_selection_digest_history_di`，重复组均为 0。
- Plate 相关 watermark 已追到 2026-08-14，状态为 `SUCCESS`。

## 仍需关注

- `ods_stk_factor` 仍缺 16 个历史交易日：2010-04-07 至 2010-04-26 的早期日期，以及 2013-10-21 至 2013-10-22。
- `meta_production_pit_quality_run_v2` 最新记录仍是 2026-08-10 的 `BLOCKED`（14 个问题），修复后的数据尚未生成新的正式门禁记录。
- `dwd_equity_daily_bar_v2`、`dwd_security_lifecycle_daily_v2`、`meta_formal_data_snapshot_v2` 仍为空；如它们属于当前正式 PIT 链路，需要单独补齐。
- 2026-08-14 的标准行情、标签、Universe 等 DWD/ADS 数据仍有 `UNVERIFIED` 或来源时间字段不完整的问题，不能据此宣称完整 PIT 可追溯。

## 结论

本次修复已解决主要交易日覆盖和当前行情覆盖问题；`tushare_stock` 可作为普通日频数据源继续使用，但在重新运行并通过正式 PIT 质量门禁前，不应标记为“正式 PIT 完整通过”。
