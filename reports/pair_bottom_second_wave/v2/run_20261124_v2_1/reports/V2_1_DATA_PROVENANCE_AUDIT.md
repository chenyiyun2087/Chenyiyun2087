# SWI_FACTOR V2.1: 数据来源与事实验真审计 (V2_1_DATA_PROVENANCE_AUDIT)

- **生成时间**: 2026-11-24T20:45:15.179864
- **底层数据库**: ASDC MySQL (`tushare_stock`)
- **目标股票**: `688401.SH` (路维光电)

## 1. 核心事实动态验证结果

| 事实项 | ASDC 动态查询数值 | 状态 | 证据出处 | available_at |
|---|---|---|---|---|
| **2026-07-01 盘中最高价** | 103.9 元 | `CONFIRMED` | `ods_daily(trade_date=20260701, high=103.9000, close=99.4900)` | 2026-07-01 15:00:00 |
| **2026-07-01 收盘价** | 99.49 元 | `CONFIRMED` | `ods_daily(trade_date=20260701)` | 2026-07-01 15:00:00 |
| **2026-11-23 阶段低点** | 40.89 元 | `CONFIRMED` | `ods_daily(trade_date=20261123)` | 2026-11-23 15:00:00 |
| **2026-11-24 收盘价** | 43.43 元 | `CONFIRMED_AS_CLOSE_PRICE` | `ods_daily(trade_date=20261124, close=43.4300, low=43.0900)` | 2026-11-24 15:00:00 |
| **收盘峰值回撤** | -58.20% | `CONFIRMED` | 严格由 (43.43 / 103.90 - 1) 计算 | 2026-11-24 15:00:00 |
| **复权因子一致性** | 1.4914 (无断点) | `CONFIRMED_NO_SPLIT_OR_DIVIDEND` | `dwd_adj_factor` | 2026-11-24 15:00:00 |
| **定增转股价调整公告** | 2026-07-16 | `CONFIRMED_ANNOUNCEMENT_EXISTS` | `路维光电:路维光电关于因向特定对象发行股票调整“路维转债”转股价格暨转股停复牌的公告` | 2026-07-16 00:00:00 |
| **股份回购状态** | 缺失底表 | `UNVERIFIED_IN_LOCAL_ASDC` | `Local MySQL lacks tushare.repurchase; external notice unverified in ASDC` | UNKNOWN |
| **限售股解禁批次** | 暂无流通批次 | `UNVERIFIED_SCENARIO` | `No formal lockup expiration batch found in ASDC share_float; scenario assumption only` | UNKNOWN |

## 2. 真实交易日历跨度 (动态读取自 dim_trade_cal)

- **1个月窗口 (至 2026-12-24)**: `22` 个交易日
- **2个月窗口 (至 2027-01-24)**: `42` 个交易日
- **2027年1月底窗口 (至 2027-01-31)**: `47` 个交易日
- **3个月窗口 (至 2027-02-24)**: `60` 个交易日
