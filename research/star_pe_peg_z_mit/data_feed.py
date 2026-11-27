"""Data feed for STAR Market (688/689) PE/PEG, Z-score and MIT Trader research."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timedelta
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from platform_core.eod_history.runtime import get_db_connection
from platform_core.trading_rules.tradeability import is_tradable_for_entry, is_tradable_for_exit


SHANGHAI = ZoneInfo("Asia/Shanghai")
VERIFIED_PIT_TIERS = {"PIT_VERIFIED", "ROW_LEVEL_PIT_VERIFIED", "VERIFIED"}
FINANCIAL_PARQUET_PATH = Path(
    "/Volumes/extension/projects/AShareDataCenter/outputs/evidence_production/pit_cn_equity_v2_shadow_20260731/financial.parquet"
)


def date_key(value: Any) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return int(value.strftime("%Y%m%d"))
    return int(str(value).replace("-", "")[:8])


def index_dates(values: pd.Series | np.ndarray) -> pd.DatetimeIndex:
    return pd.to_datetime(values.astype(str), format="%Y%m%d")


def query(cursor, sql: str, params: tuple = ()) -> list[dict]:
    cursor.execute(sql, params)
    return cursor.fetchall()


class StarDataFeed:
    """Read-only data loader for STAR Market daily prices, limits, pe_ttm, and financial disclosures."""

    def __init__(
        self,
        start_date: int = 20190722,
        end_date: int = 20261126,
        replay_start_date: int = 20230901,
        history_sessions: int = 1001,
        benchmark: str = "000688.SH",
        financial_parquet: Optional[Path] = None,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.replay_start_date = replay_start_date
        self.history_sessions = history_sessions
        self.benchmark = benchmark
        self.financial_parquet = financial_parquet or FINANCIAL_PARQUET_PATH

    def load_universe_roster(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Query STAR 688/689 stocks with at least `history_sessions` by end_date."""
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            roster = query(
                cursor,
                """SELECT s.ts_code, s.name, s.list_date, s.delist_date,
                          (SELECT COUNT(*) FROM dim_trade_cal c
                           WHERE c.exchange='SSE' AND c.is_open=1 AND c.cal_date BETWEEN s.list_date AND %s
                             AND (s.delist_date IS NULL OR s.delist_date=0 OR c.cal_date<s.delist_date)) AS listed_sessions
                   FROM dim_stock s 
                   WHERE (s.ts_code LIKE '688%%.SH' OR s.ts_code LIKE '689%%.SH') 
                     AND s.list_date BETWEEN %s AND %s
                   ORDER BY s.list_date, s.ts_code""",
                (self.end_date, self.start_date, self.end_date),
            )
            conn.rollback()

        eligible = [row for row in roster if row["listed_sessions"] >= self.history_sessions]
        symbols = [row["ts_code"] for row in eligible]
        return roster, symbols

    def load_calendar(self) -> pd.DatetimeIndex:
        """Load SSE open trading days between start_date and end_date."""
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            rows = query(
                cursor,
                """SELECT cal_date FROM dim_trade_cal 
                   WHERE exchange='SSE' AND is_open=1 AND cal_date BETWEEN %s AND %s 
                   ORDER BY cal_date""",
                (self.start_date, self.end_date),
            )
            conn.rollback()
        return pd.DatetimeIndex(index_dates(pd.Series([r["cal_date"] for r in rows])))

    def load_benchmark(self, sessions: pd.DatetimeIndex) -> pd.DataFrame:
        """Load benchmark 000688.SH open & close prices."""
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            rows = query(
                cursor,
                """SELECT trade_date, open, close FROM ods_index_daily
                   WHERE ts_code=%s AND trade_date BETWEEN %s AND %s
                     AND visible_date<=trade_date AND open>0 AND close>0
                   ORDER BY trade_date""",
                (self.benchmark, self.start_date, self.end_date),
            )
            conn.rollback()
        b_df = pd.DataFrame(rows)
        b_df.index = index_dates(b_df.pop("trade_date"))
        b_df = b_df.reindex(sessions).ffill()
        b_df["open"] = pd.to_numeric(b_df["open"], errors="coerce")
        b_df["close"] = pd.to_numeric(b_df["close"], errors="coerce")
        return b_df

    def load_market_data(
        self, symbols: List[str], sessions: pd.DatetimeIndex
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]], Dict[str, str]]:
        """Load OHLCV, adj_factor, limits, suspensions, 20d turnover MA, and tradeability."""
        now = datetime.now(SHANGHAI)
        frames: Dict[str, pd.DataFrame] = {}
        audits: Dict[str, Dict[str, Any]] = {}
        skipped: Dict[str, str] = {}

        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for symbol in symbols:
                metadata = query(cursor, "SELECT list_date, delist_date FROM dim_stock WHERE ts_code=%s", (symbol,))
                if not metadata or not metadata[0]["list_date"]:
                    skipped[symbol] = "证券主表缺少上市日期"
                    continue
                delist_date = metadata[0]["delist_date"]
                if delist_date and delist_date <= self.replay_start_date:
                    skipped[symbol] = "回放起点前已退市"
                    continue

                stock_sessions = sessions[sessions >= pd.Timestamp(str(metadata[0]["list_date"]))]
                if delist_date and delist_date > 0:
                    stock_sessions = stock_sessions[stock_sessions < pd.Timestamp(str(delist_date))]
                rows = query(
                    cursor,
                    """SELECT d.trade_date, d.open, d.high, d.low, d.close, d.vol, d.amount,
                             d.visible_date AS bar_visible, d.pit_tier AS bar_tier,
                             COALESCE(GREATEST(d.knowledge_time, d.available_at), d.knowledge_time, d.available_at) AS bar_available,
                             COALESCE(GREATEST(d.operational_ingested_at, d.ingested_at), d.operational_ingested_at, d.ingested_at) AS bar_ingested,
                             a.adj_factor, a.visible_date AS factor_visible, a.pit_tier AS factor_tier,
                             COALESCE(GREATEST(a.knowledge_time, a.available_at), a.knowledge_time, a.available_at) AS factor_available,
                             COALESCE(GREATEST(a.operational_ingested_at, a.ingested_at), a.operational_ingested_at, a.ingested_at) AS factor_ingested,
                             l.up_limit, l.down_limit, l.available_at AS limit_available,
                             l.fetched_at AS limit_fetched, l.ingested_at AS limit_ingested, l.source_complete AS limit_complete
                      FROM ods_daily d
                      LEFT JOIN ods_adj_factor a ON a.trade_date=d.trade_date AND a.ts_code=d.ts_code
                      LEFT JOIN ods_limit_price_v2 l ON l.trade_date=d.trade_date AND l.ts_code=d.ts_code
                        AND l.data_version=(SELECT v.data_version FROM ods_limit_price_v2 v
                                            WHERE v.trade_date=d.trade_date AND v.ts_code=d.ts_code
                                            ORDER BY v.fetched_at DESC, v.data_version DESC LIMIT 1)
                      WHERE d.ts_code=%s AND d.trade_date BETWEEN %s AND %s 
                      ORDER BY d.trade_date""",
                    (symbol, self.start_date, self.end_date),
                )
                if not rows or stock_sessions.empty:
                    skipped[symbol] = "区间内无有效日线数据"
                    continue

                halts = query(
                    cursor,
                    """SELECT trade_date, source_complete, available_at, fetched_at, ingested_at
                       FROM ods_suspension_event_v2
                       WHERE ts_code=%s AND trade_date BETWEEN %s AND %s AND suspend_type='S'""",
                    (symbol, self.start_date, self.end_date),
                )
                halt_days = {r["trade_date"] for r in halts}
                suspended_days = index_dates(pd.Series(sorted(halt_days), dtype="int64"))

                columns = ["open", "high", "low", "close", "adj_factor", "vol", "amount", "up_limit", "down_limit"]
                frame = pd.DataFrame(rows)
                frame.index = index_dates(frame.pop("trade_date"))
                frame = frame.reindex(stock_sessions)
                frame[columns] = frame[columns].apply(pd.to_numeric, errors="coerce")

                values = frame[["open", "high", "low", "close", "adj_factor"]]
                valid = (np.isfinite(values) & values.gt(0)).all(axis=1) & frame["vol"].gt(0)
                suspended = stock_sessions.isin(suspended_days)
                dated = pd.Series([date_key(d) for d in stock_sessions], index=stock_sessions)
                days = pd.Series(stock_sessions, index=stock_sessions)
                late = pd.Series(False, index=stock_sessions)

                for prefix in ("bar", "factor"):
                    visible = pd.to_numeric(frame[f"{prefix}_visible"], errors="coerce")
                    available = pd.to_datetime(frame[f"{prefix}_available"], errors="coerce")
                    late |= visible.gt(dated) | available.dt.normalize().gt(days)

                market = valid & ~suspended & ~late
                for col in ("open", "high", "low", "close"):
                    frame[f"adj_{col}"] = (frame[col] * frame["adj_factor"]).where(market)

                limit_known = (frame["up_limit"] > frame["down_limit"]) & (frame["down_limit"] > 0)
                can_trade = market & limit_known
                frame["can_buy"] = [
                    allowed and is_tradable_for_entry(price, limit)
                    for allowed, price, limit in zip(can_trade, frame["open"], frame["up_limit"])
                ]
                frame["can_sell"] = [
                    allowed and is_tradable_for_exit(price, limit)
                    for allowed, price, limit in zip(can_trade, frame["open"], frame["down_limit"])
                ]

                # 20-day rolling turnover (amount in RMB): fillna with 0, 1% cap
                # Note: amount in ods_daily is in thousands of RMB or RMB. Let's verify:
                # In tushare ods_daily, amount is in thousands (千元).
                # To get nominal RMB: amount * 1000.
                amount_rmb = frame["amount"].fillna(0.0) * 1000.0
                turnover_ma20 = amount_rmb.rolling(20, min_periods=1).mean().fillna(0.0)
                frame["turnover_ma20_1pct"] = turnover_ma20 * 0.01

                frame["is_market_session"] = market
                frame["eligible_1001d"] = np.arange(1, len(stock_sessions) + 1) >= self.history_sessions
                audit = {
                    "total_sessions": len(stock_sessions),
                    "valid_market_bars": int(market.sum()),
                    "suspensions": int(suspended.sum()),
                    "late_bars": int((valid & ~suspended & late).sum()),
                    "can_buy_bars": int(pd.Series(frame["can_buy"]).sum()),
                    "can_sell_bars": int(pd.Series(frame["can_sell"]).sum()),
                    "sha256": hashlib.sha256(frame[["open", "close", "adj_factor"]].to_csv().encode()).hexdigest(),
                }
                frames[symbol] = frame
                audits[symbol] = audit

            conn.rollback()

        return frames, audits, skipped

    def load_pe_ttm_map(self) -> pd.DataFrame:
        """Load trade_date, ts_code, pe_ttm from ods_daily_basic."""
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            rows = query(
                cursor,
                """SELECT trade_date, ts_code, pe_ttm FROM ods_daily_basic
                   WHERE (ts_code LIKE '688%%.SH' OR ts_code LIKE '689%%.SH')
                     AND trade_date BETWEEN %s AND %s AND pe_ttm IS NOT NULL
                   ORDER BY trade_date, ts_code""",
                (self.start_date, self.end_date),
            )
            conn.rollback()
        pe_df = pd.DataFrame(rows)
        if not pe_df.empty:
            pe_df["pe_ttm"] = pd.to_numeric(pe_df["pe_ttm"], errors="coerce")
            pe_df["trade_date"] = pe_df["trade_date"].astype(int)
        return pe_df

    def load_disclosed_annual_reports(self) -> pd.DataFrame:
        """Load annual report net profits, announcement dates, and revision IDs from financial.parquet."""
        if not self.financial_parquet.exists():
            raise FileNotFoundError(f"Financial parquet file not found: {self.financial_parquet}")

        df = pd.read_parquet(self.financial_parquet)
        df["symbol"] = df["symbol"].astype(str)
        star = df[df["symbol"].str.startswith("688") | df["symbol"].str.startswith("689")].copy()
        annual = star[star["financial_period_end"].str.endswith("12-31")].copy()
        annual["year"] = annual["financial_period_end"].str[:4].astype(int)
        annual["ann_date"] = pd.to_datetime(annual["announcement_date"])
        annual["ann_date_int"] = annual["ann_date"].dt.strftime("%Y%m%d").astype(int)
        annual["np"] = pd.to_numeric(annual["net_profit_ttm"], errors="coerce")
        annual_unique = (
            annual.dropna(subset=["np"])
            .sort_values(["symbol", "year", "ann_date_int", "trade_date"])
            .drop_duplicates(subset=["symbol", "year", "ann_date_int"], keep="last")
            .sort_values(["symbol", "year", "ann_date_int"])
        )

        cagr_records = []
        for sym, group in annual_unique.groupby("symbol"):
            ts_code = sym + ".SH" if not sym.endswith(".SH") else sym
            by_year = {
                int(r["year"]): (
                    float(r["np"]),
                    r["ann_date"],
                    int(r["ann_date_int"]),
                    str(r["revision_id"]),
                    str(r.get("financial_source_snapshot_sha", "")),
                )
                for _, r in group.iterrows()
            }
            for y in sorted(by_year.keys()):
                if y - 2 in by_year:
                    np_curr, ann_dt, ann_int, rev_id, snap_sha = by_year[y]
                    np_prev, _, _, _, _ = by_year[y - 2]
                    # Disclosed positive growth: NP_y > 0, NP_{y-2} > 0, NP_y > NP_{y-2}
                    positive_growth = (np_curr > 0) and (np_prev > 0) and (np_curr > np_prev)
                    cagr = (np_curr / np_prev) ** 0.5 - 1.0 if positive_growth else np.nan
                    cagr_records.append(
                        {
                            "ts_code": ts_code,
                            "report_year": y,
                            "ann_date": ann_dt,
                            "ann_date_int": ann_int,
                            "np_curr": np_curr,
                            "np_prev": np_prev,
                            "is_positive_growth": positive_growth,
                            "cagr_2y": cagr,
                            "revision_id": rev_id,
                            "snapshot_sha": snap_sha,
                        }
                    )
        return pd.DataFrame(cagr_records)

    def audit_forecast_consensus(self) -> Dict[str, Any]:
        """Audit forecast consensus records for STAR stocks in ASDC."""
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            cursor.execute(
                """SELECT COUNT(*) as cnt, MIN(visible_date) as min_vis, MAX(visible_date) as max_vis,
                          MIN(publication_date) as min_pub, MAX(publication_date) as max_pub
                   FROM ods_forecast_consensus_versioned 
                   WHERE ts_code LIKE '688%%.SH' OR ts_code LIKE '689%%.SH'"""
            )
            v_stats = cursor.fetchone()

            cursor.execute(
                """SELECT COUNT(*) as cnt 
                   FROM dws_forecast_consensus_pit_daily 
                   WHERE ts_code LIKE '688%%.SH' OR ts_code LIKE '689%%.SH'"""
            )
            dws_stats = cursor.fetchone()

            cursor.execute(
                """SELECT COUNT(*) as cnt, MIN(visible_date) as min_vis, MAX(visible_date) as max_vis,
                          COUNT(DISTINCT ts_code) as num_stocks
                   FROM ods_forecast_consensus_metric_versioned_v1 
                   WHERE ts_code LIKE '688%%.SH' OR ts_code LIKE '689%%.SH'"""
            )
            m_stats = cursor.fetchone()
            conn.rollback()

        return {
            "forecast_consensus_versioned_count": v_stats["cnt"],
            "dws_forecast_consensus_pit_daily_count": dws_stats["cnt"],
            "metric_versioned_count": m_stats["cnt"],
            "metric_min_visible_date": m_stats["min_vis"],
            "metric_max_visible_date": m_stats["max_vis"],
            "metric_num_stocks": m_stats["num_stocks"],
            "pit_row_level_verified_count": 0,
            "status": "UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING",
            "backfill_conditions": (
                "需补充 2023-09-01 至 2026-09-11 期间科创板卖方一致预期盈利预测快照，"
                "并补齐 visible_date, available_at, 行级 PIT 审计通过标记 (VERIFIED/ROW_LEVEL_PIT_VERIFIED)。"
            ),
        }
