"""Bulk data loader for scoreRank (baseline_full_liquidity_detail_vol_position) and chenyiyunSelected (LocalHighDividendStrategy)."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from platform_core.eod_history.runtime import get_db_connection
from platform_core.trading_rules.tradeability import is_tradable_for_entry, is_tradable_for_exit
from research.star_pe_peg_z_mit.data_feed import date_key, index_dates, query
from research.star_pe_peg_z_mit.valuation_service import MonthEndValuationSnapshot


class LocalStrategyDataLoader:
    """Loads month-end watchlists for scoreRank & chenyiyunSelected and bulk market frames."""

    def __init__(
        self,
        start_date: int = 20230103,
        end_date: int = 20261124,
        replay_start_date: int = 20230901,
        benchmark: str = "000300.SH",
        watchlist_size: int = 30,
        top_n_only: int = 3,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.replay_start_date = replay_start_date
        self.benchmark = benchmark
        self.watchlist_size = watchlist_size
        self.top_n_only = top_n_only

    def load_calendar(self) -> pd.DatetimeIndex:
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            rows = query(
                cursor,
                """SELECT cal_date FROM tushare_stock.dim_trade_cal
                   WHERE exchange='SSE' AND is_open=1 AND cal_date BETWEEN %s AND %s
                   ORDER BY cal_date""",
                (self.start_date, self.end_date),
            )
            conn.rollback()
        return pd.DatetimeIndex(index_dates(pd.Series([r["cal_date"] for r in rows])))

    def load_benchmark(self, sessions: pd.DatetimeIndex) -> pd.DataFrame:
        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            rows = query(
                cursor,
                """SELECT trade_date, open, close FROM tushare_stock.ods_index_daily
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

    @staticmethod
    def _symbol_to_ts_code(sym: str) -> str:
        s = str(sym).zfill(6)
        if s.startswith(("6", "9")):
            return f"{s}.SH"
        if s.startswith(("4", "8")):
            return f"{s}.BJ"
        return f"{s}.SZ"

    def build_scorerank_snapshots(
        self, month_ends: List[int], next_open_dates: List[int], sessions: pd.DatetimeIndex
    ) -> Tuple[Dict[int, MonthEndValuationSnapshot], Set[str]]:
        """Build monthly candidate snapshots using baseline_full_liquidity_detail_vol_position logic."""
        snapshots: Dict[int, MonthEndValuationSnapshot] = {}
        all_symbols: Set[str] = set()
        date_ints = [int(d.strftime("%Y%m%d")) for d in sessions]

        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for me_date, eff_open in zip(month_ends, next_open_dates):
                if me_date not in date_ints:
                    continue
                idx = date_ints.index(me_date)
                win_start = date_ints[max(0, idx - 25)]
                me_iso = f"{str(me_date)[:4]}-{str(me_date)[4:6]}-{str(me_date)[6:8]}"

                # 1. Query score_rank_daily on me_date
                sr_rows = query(
                    cursor,
                    """SELECT s.symbol, s.s_liquidity, s.score
                       FROM chenyiyun.score_rank_daily s
                       WHERE s.trade_date = %s AND s.s_liquidity IS NOT NULL""",
                    (me_iso,),
                )
                if not sr_rows:
                    snapshots[me_date] = MonthEndValuationSnapshot(
                        month_end_date=me_date, effective_open_date=eff_open,
                        universe_count=0, eligible_count=0,
                    )
                    continue

                sr_df = pd.DataFrame(sr_rows)
                sr_df["ts_code"] = sr_df["symbol"].map(self._symbol_to_ts_code)
                sr_df["s_liquidity"] = pd.to_numeric(sr_df["s_liquidity"], errors="coerce")

                # 2. Filter non-ST, listed >= 375 calendar days, not delisted
                meta_rows = query(
                    cursor,
                    """SELECT l.ts_code, l.is_st, ds.list_date, ds.delist_date
                       FROM tushare_stock.dwd_stock_label_daily l
                       JOIN tushare_stock.dim_stock ds ON ds.ts_code = l.ts_code
                       WHERE l.trade_date = %s AND COALESCE(l.is_st, 0) = 0
                         AND ds.list_date IS NOT NULL
                         AND DATEDIFF(%s, STR_TO_DATE(CAST(ds.list_date AS CHAR), '%%Y%%m%%d')) >= 375
                         AND (ds.delist_date IS NULL OR ds.delist_date = 0 OR ds.delist_date > %s)""",
                    (me_date, me_iso, me_date),
                )
                valid_codes = {r["ts_code"] for r in meta_rows}
                sr_df = sr_df[sr_df["ts_code"].isin(valid_codes)].copy()
                if sr_df.empty:
                    snapshots[me_date] = MonthEndValuationSnapshot(
                        month_end_date=me_date, effective_open_date=eff_open,
                        universe_count=0, eligible_count=0,
                    )
                    continue

                # 3. Trailing 25d bars for liquidity_detail_score
                bar_rows = query(
                    cursor,
                    """SELECT trade_date, ts_code, high, low, close, amount
                       FROM tushare_stock.ods_daily
                       WHERE trade_date BETWEEN %s AND %s AND vol > 0""",
                    (win_start, me_date),
                )
                bars = pd.DataFrame(bar_rows)
                for col in ("high", "low", "close", "amount"):
                    bars[col] = pd.to_numeric(bars[col], errors="coerce")
                bars = bars.sort_values(["ts_code", "trade_date"])
                g = bars.groupby("ts_code", group_keys=False)
                bars["amount_ma5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=3).mean())
                bars["amount_ma20"] = g["amount"].transform(lambda s: s.rolling(20, min_periods=10).mean())
                bars["relative_amount"] = bars["amount"] / bars["amount_ma20"].replace(0, np.nan)
                bars["amount_ratio_5_20"] = bars["amount_ma5"] / bars["amount_ma20"].replace(0, np.nan)
                bars["amplitude"] = (bars["high"] - bars["low"]) / bars["close"].replace(0, np.nan)
                bars["impact_cost_raw"] = bars["amplitude"] / bars["amount"].replace(0, np.nan)
                amt_chg = g["amount"].transform(lambda s: s.pct_change(fill_method=None))
                bars["amount_stability_raw"] = 1.0 / (
                    amt_chg.groupby(bars["ts_code"]).transform(lambda s: s.rolling(20, min_periods=10).std()) + 1e-9
                )

                me_bars = bars[bars["trade_date"] == me_date].copy()
                merged = sr_df.merge(
                    me_bars[["ts_code", "relative_amount", "amount_ratio_5_20", "impact_cost_raw", "amount_stability_raw"]],
                    on="ts_code", how="inner",
                )
                if merged.empty:
                    snapshots[me_date] = MonthEndValuationSnapshot(
                        month_end_date=me_date, effective_open_date=eff_open,
                        universe_count=len(sr_df), eligible_count=0,
                    )
                    continue

                merged["s_relative_amount"] = merged["relative_amount"].rank(pct=True, ascending=True) * 100.0
                merged["s_amount_ratio_5_20"] = merged["amount_ratio_5_20"].rank(pct=True, ascending=True) * 100.0
                merged["s_low_impact_cost"] = (1.0 - merged["impact_cost_raw"].rank(pct=True, ascending=True)) * 100.0
                merged["s_amount_stability"] = merged["amount_stability_raw"].rank(pct=True, ascending=True) * 100.0
                norm_liq = (merged["s_liquidity"] / 30.0 * 100.0).clip(0, 100)
                merged["liquidity_detail_score"] = (
                    0.40 * norm_liq
                    + 0.20 * merged["s_relative_amount"].fillna(50.0)
                    + 0.15 * merged["s_amount_ratio_5_20"].fillna(50.0)
                    + 0.15 * merged["s_low_impact_cost"].fillna(50.0)
                    + 0.10 * merged["s_amount_stability"].fillna(50.0)
                ).clip(0, 100)

                ranked = merged.sort_values(["liquidity_detail_score", "ts_code"], ascending=[False, True])
                watchlist = ranked["ts_code"].head(self.watchlist_size).tolist()
                top3 = watchlist[: self.top_n_only]
                all_symbols.update(watchlist)

                snapshots[me_date] = MonthEndValuationSnapshot(
                    month_end_date=me_date,
                    effective_open_date=eff_open,
                    universe_count=len(sr_df),
                    eligible_count=len(watchlist),
                    top3_valuation_only=top3,
                    watchlist_valuation_plus_z=watchlist,
                )
            conn.rollback()
        return snapshots, all_symbols

    def build_chenyiyun_selected_snapshots(
        self, month_ends: List[int], next_open_dates: List[int]
    ) -> Tuple[Dict[int, MonthEndValuationSnapshot], Set[str]]:
        """Build monthly candidate snapshots using LocalHighDividendStrategy (excluding 68/4/8/.BJ)."""
        snapshots: Dict[int, MonthEndValuationSnapshot] = {}
        all_symbols: Set[str] = set()

        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for me_date, eff_open in zip(month_ends, next_open_dates):
                me_iso = f"{str(me_date)[:4]}-{str(me_date)[4:6]}-{str(me_date)[6:8]}"
                rows = query(
                    cursor,
                    """SELECT b.ts_code, b.circ_mv, COALESCE(b.dv_ttm, b.dv_ratio, 0) AS dividend_ratio,
                              f.turnover_vol_20 AS turnover_volatility
                       FROM tushare_stock.dwd_daily_basic b
                       JOIN tushare_stock.dwd_stock_label_daily l
                         ON l.ts_code = b.ts_code AND l.trade_date = b.trade_date
                       JOIN tushare_stock.dim_stock ds
                         ON ds.ts_code = b.ts_code
                       LEFT JOIN tushare_stock.dws_liquidity_factor f
                         ON f.ts_code = b.ts_code AND f.trade_date = b.trade_date
                       WHERE b.trade_date = %s AND COALESCE(l.is_st, 0) = 0
                         AND b.circ_mv > 0
                         AND ds.list_date IS NOT NULL
                         AND DATEDIFF(%s, STR_TO_DATE(CAST(ds.list_date AS CHAR), '%%Y%%m%%d')) >= 375
                         AND (ds.delist_date IS NULL OR ds.delist_date = 0 OR ds.delist_date > %s)""",
                    (me_date, me_iso, me_date),
                )
                if not rows:
                    snapshots[me_date] = MonthEndValuationSnapshot(
                        month_end_date=me_date, effective_open_date=eff_open,
                        universe_count=0, eligible_count=0,
                    )
                    continue

                df = pd.DataFrame(rows)
                # Exclude STAR (68*) and BSE (.BJ, 4*, 8*) per LocalHighDividendStrategy._filter_kcbj
                df = df[
                    ~df["ts_code"].str.endswith(".BJ")
                    & ~df["ts_code"].str.startswith(("68", "4", "8"))
                ].copy()
                for col in ("circ_mv", "dividend_ratio", "turnover_volatility"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                univ_cnt = len(df)
                # Step 1: top 50% dividend_ratio
                dr = df.dropna(subset=["dividend_ratio"]).sort_values("dividend_ratio", ascending=False)
                dr = dr.iloc[: int(0.50 * len(dr))]
                # Step 2: top 80% turnover_volatility
                tv = dr.dropna(subset=["turnover_volatility"]).sort_values("turnover_volatility", ascending=False)
                tv = tv.iloc[: int(0.80 * len(tv))]
                # Step 3: smallest circ_mv (mlev unavailable in empty dwd_fina_indicator -> uniform median pass-through)
                final = tv.sort_values(["circ_mv", "ts_code"], ascending=[True, True])
                watchlist = final["ts_code"].head(self.watchlist_size).tolist()
                top3 = watchlist[: self.top_n_only]
                all_symbols.update(watchlist)

                snapshots[me_date] = MonthEndValuationSnapshot(
                    month_end_date=me_date,
                    effective_open_date=eff_open,
                    universe_count=univ_cnt,
                    eligible_count=len(watchlist),
                    top3_valuation_only=top3,
                    watchlist_valuation_plus_z=watchlist,
                )
            conn.rollback()
        return snapshots, all_symbols

    def load_market_frames_bulk(
        self, symbols: Set[str], sessions: pd.DatetimeIndex
    ) -> Dict[str, pd.DataFrame]:
        """Bulk load daily OHLCV, adj_factor, limits, suspensions for all candidate symbols."""
        if not symbols:
            return {}
        sym_list = sorted(symbols)
        placeholders = ",".join(["%s"] * len(sym_list))

        with closing(get_db_connection()) as conn, conn.cursor() as cursor:
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            meta_rows = query(
                cursor,
                f"SELECT ts_code, list_date, delist_date FROM tushare_stock.dim_stock WHERE ts_code IN ({placeholders})",
                tuple(sym_list),
            )
            meta_map = {r["ts_code"]: r for r in meta_rows}

            daily_rows = query(
                cursor,
                f"""SELECT d.trade_date, d.ts_code, d.open, d.high, d.low, d.close, d.vol, d.amount,
                           d.visible_date AS bar_visible,
                           COALESCE(GREATEST(d.knowledge_time, d.available_at), d.knowledge_time, d.available_at) AS bar_available,
                           a.adj_factor, a.visible_date AS factor_visible,
                           COALESCE(GREATEST(a.knowledge_time, a.available_at), a.knowledge_time, a.available_at) AS factor_available
                    FROM tushare_stock.ods_daily d
                    LEFT JOIN tushare_stock.ods_adj_factor a ON a.trade_date=d.trade_date AND a.ts_code=d.ts_code
                    WHERE d.ts_code IN ({placeholders}) AND d.trade_date BETWEEN %s AND %s
                    ORDER BY d.ts_code, d.trade_date""",
                tuple(sym_list) + (self.start_date, self.end_date),
            )
            limit_rows = query(
                cursor,
                f"""SELECT trade_date, ts_code, up_limit, down_limit
                    FROM tushare_stock.ods_limit_price_v2
                    WHERE ts_code IN ({placeholders}) AND trade_date BETWEEN %s AND %s""",
                tuple(sym_list) + (self.start_date, self.end_date),
            )
            halt_rows = query(
                cursor,
                f"""SELECT trade_date, ts_code
                    FROM tushare_stock.ods_suspension_event_v2
                    WHERE ts_code IN ({placeholders}) AND trade_date BETWEEN %s AND %s AND suspend_type='S'""",
                tuple(sym_list) + (self.start_date, self.end_date),
            )
            conn.rollback()

        daily_df = pd.DataFrame(daily_rows)
        limit_df = pd.DataFrame(limit_rows).drop_duplicates(subset=["trade_date", "ts_code"], keep="last") if limit_rows else pd.DataFrame(columns=["trade_date", "ts_code", "up_limit", "down_limit"])
        halt_set = {(r["ts_code"], int(r["trade_date"])) for r in halt_rows}

        if not limit_df.empty:
            daily_df = daily_df.merge(limit_df, on=["trade_date", "ts_code"], how="left")
        else:
            daily_df["up_limit"] = np.nan
            daily_df["down_limit"] = np.nan

        frames: Dict[str, pd.DataFrame] = {}
        for sym, grp in daily_df.groupby("ts_code"):
            meta = meta_map.get(sym)
            if not meta or not meta["list_date"]:
                continue
            stock_sessions = sessions[sessions >= pd.Timestamp(str(meta["list_date"]))]
            delist_d = meta["delist_date"]
            if delist_d and delist_d > 0:
                stock_sessions = stock_sessions[stock_sessions < pd.Timestamp(str(delist_d))]
            if stock_sessions.empty:
                continue

            f = grp.copy()
            f.index = index_dates(f.pop("trade_date"))
            f = f.reindex(stock_sessions)
            num_cols = ["open", "high", "low", "close", "adj_factor", "vol", "amount", "up_limit", "down_limit"]
            f[num_cols] = f[num_cols].apply(pd.to_numeric, errors="coerce")

            values = f[["open", "high", "low", "close", "adj_factor"]]
            valid = (np.isfinite(values) & values.gt(0)).all(axis=1) & f["vol"].gt(0)
            dated = pd.Series([date_key(d) for d in stock_sessions], index=stock_sessions)
            days = pd.Series(stock_sessions, index=stock_sessions)
            suspended = pd.Series([(sym, int(d)) in halt_set for d in dated], index=stock_sessions)
            late = pd.Series(False, index=stock_sessions)
            for prefix in ("bar", "factor"):
                vis = pd.to_numeric(f[f"{prefix}_visible"], errors="coerce")
                avail = pd.to_datetime(f[f"{prefix}_available"], errors="coerce")
                late |= vis.gt(dated) | avail.dt.normalize().gt(days)

            market = valid & ~suspended & ~late
            for col in ("open", "high", "low", "close"):
                f[f"adj_{col}"] = (f[col] * f["adj_factor"]).where(market)

            limit_known = (f["up_limit"] > f["down_limit"]) & (f["down_limit"] > 0)
            can_trade = market & limit_known
            f["can_buy"] = [
                allowed and is_tradable_for_entry(px, lim)
                for allowed, px, lim in zip(can_trade, f["open"], f["up_limit"])
            ]
            f["can_sell"] = [
                allowed and is_tradable_for_exit(px, lim)
                for allowed, px, lim in zip(can_trade, f["open"], f["down_limit"])
            ]
            amount_rmb = f["amount"].fillna(0.0) * 1000.0
            f["turnover_ma20_1pct"] = amount_rmb.rolling(20, min_periods=1).mean().fillna(0.0) * 0.01
            f["is_market_session"] = market
            frames[sym] = f

        return frames
