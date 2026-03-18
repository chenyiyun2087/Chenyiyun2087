from datetime import datetime, timedelta
from typing import Any, List

import numpy as np
import pandas as pd

from scoreRank.core.db_io import query_df
from scoreRank.core.logging_utils import get_score_rank_logger
from scoreRank.strategies.base import BaseScorer

logger = get_score_rank_logger(__name__)


class ClaudeScorer(BaseScorer):
    """
    Multi-factor scorer with six dimensions:
    momentum / value / quality / technical / capital / chip.

    Total score = 100 points.
    """

    def _query_df(self, db_conf: dict, sql: str, params=None) -> pd.DataFrame:
        return query_df(db_conf, sql, params)

    def _in_clause(self, values: list[str]) -> str:
        return ", ".join(["%s"] * len(values))

    def score(self, symbols: List[str], asof_date: pd.Timestamp, engine: Any) -> pd.DataFrame:
        symbols = [str(s).zfill(6) for s in symbols if s]
        if not symbols:
            return pd.DataFrame(columns=["symbol", "trade_date", "score"])

        trade_date_int = int(asof_date.strftime("%Y%m%d"))
        ts_code_map = self._get_ts_code_map(engine, symbols)

        if not ts_code_map:
            logger.warning("ClaudeScorer: no ts_code mapping found for %d symbols", len(symbols))
            return pd.DataFrame({
                "symbol": symbols,
                "trade_date": [asof_date.normalize()] * len(symbols),
                "score": [0.0] * len(symbols),
            })

        # 1) fetch raw dimensions
        df_tech_mom = self._fetch_technical_momentum(engine, ts_code_map, trade_date_int)
        df_value = self._fetch_value(engine, ts_code_map, trade_date_int)
        df_quality = self._fetch_quality(engine, ts_code_map, trade_date_int)
        df_capital = self._fetch_capital(engine, ts_code_map, trade_date_int)
        df_chip = self._fetch_chip(engine, ts_code_map, trade_date_int)

        # 2) merge by symbol
        df = pd.DataFrame({"symbol": symbols})
        for sub_df in (df_tech_mom, df_value, df_quality, df_capital, df_chip):
            if not sub_df.empty:
                df = df.merge(sub_df, on="symbol", how="left")

        # 3) score each dimension
        df["score_momentum"] = self._score_momentum(df)
        df["score_value"] = self._score_value(df)
        df["score_quality"] = self._score_quality(df)
        df["score_technical"] = self._score_technical(df)
        df["score_capital"] = self._score_capital(df)
        df["score_chip"] = self._score_chip(df)

        # 4) total + date (keep BaseScorer contract)
        sub_cols = [
            "score_momentum",
            "score_value",
            "score_quality",
            "score_technical",
            "score_capital",
            "score_chip",
        ]
        df["score"] = df[sub_cols].fillna(0.0).sum(axis=1)
        df["score"] = df["score"].clip(0.0, 100.0)
        df["trade_date"] = asof_date.normalize()

        return df[
            [
                "symbol",
                "trade_date",
                "score",
                "score_momentum",
                "score_value",
                "score_quality",
                "score_technical",
                "score_capital",
                "score_chip",
            ]
        ]

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_technical_momentum(self, engine: Any, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        dt = datetime.strptime(str(trade_date_int), "%Y%m%d")
        start_date_int = int((dt - timedelta(days=180)).strftime("%Y%m%d"))

        sql = f"""
            SELECT trade_date, ts_code, adj_close, adj_high, adj_low, vol
            FROM tushare_stock.dwd_stock_daily_standard
            WHERE trade_date BETWEEN %s AND %s
              AND ts_code IN ({self._in_clause(ts_codes)})
            ORDER BY ts_code ASC, trade_date ASC
        """

        try:
            params = [start_date_int, trade_date_int]
            params.extend(ts_codes)
            df = self._query_df(engine, sql, tuple(params))
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch technical/momentum data")
            return pd.DataFrame(columns=["symbol"])

        if df.empty:
            return pd.DataFrame(columns=["symbol"])

        for col in ("adj_close", "adj_high", "adj_low", "vol"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["trade_date"]).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        if df.empty:
            return pd.DataFrame(columns=["symbol"])

        g = df.groupby("ts_code", sort=False)

        df["ret_5"] = g["adj_close"].pct_change(5)
        df["ret_20"] = g["adj_close"].pct_change(20)
        df["ret_60"] = g["adj_close"].pct_change(60)

        vol_ma5_prev = g["vol"].transform(lambda s: s.shift(1).rolling(5).mean())
        df["vol_ratio"] = np.where(vol_ma5_prev > 0, df["vol"] / vol_ma5_prev, np.nan)

        ema12 = g["adj_close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
        ema26 = g["adj_close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
        df["macd"] = ema12 - ema26
        df["macd_signal"] = df.groupby("ts_code", sort=False)["macd"].transform(
            lambda s: s.ewm(span=9, adjust=False).mean()
        )

        delta = g["adj_close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        gain_avg = gain.groupby(df["ts_code"], sort=False).transform(lambda s: s.rolling(6).mean())
        loss_avg = loss.groupby(df["ts_code"], sort=False).transform(lambda s: s.rolling(6).mean())
        rs = gain_avg / loss_avg.replace(0, np.nan)
        df["rsi_6"] = 100 - (100 / (1 + rs))

        low_9 = g["adj_low"].transform(lambda s: s.rolling(9).min())
        high_9 = g["adj_high"].transform(lambda s: s.rolling(9).max())
        rsv = (df["adj_close"] - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
        df["k"] = rsv.groupby(df["ts_code"], sort=False).transform(lambda s: s.ewm(alpha=1 / 3, adjust=False).mean())
        df["d"] = df["k"].groupby(df["ts_code"], sort=False).transform(lambda s: s.ewm(alpha=1 / 3, adjust=False).mean())
        df["j"] = 3 * df["k"] - 2 * df["d"]

        tp = (df["adj_high"] + df["adj_low"] + df["adj_close"]) / 3
        ma_tp = tp.groupby(df["ts_code"], sort=False).transform(lambda s: s.rolling(14).mean())
        md = (tp - ma_tp).abs().groupby(df["ts_code"], sort=False).transform(lambda s: s.rolling(14).mean())
        df["cci"] = (tp - ma_tp) / (0.015 * md.replace(0, np.nan))

        ma20 = g["adj_close"].transform(lambda s: s.rolling(20).mean())
        df["bias"] = (df["adj_close"] - ma20) / ma20.replace(0, np.nan)

        target_date = pd.to_datetime(str(trade_date_int), format="%Y%m%d")
        latest_trade_date = g["trade_date"].transform("max")
        latest_rows = df[(df["trade_date"] == target_date) & (latest_trade_date == target_date)].copy()
        if latest_rows.empty:
            return pd.DataFrame(columns=["symbol"])

        latest_rows["symbol"] = latest_rows["ts_code"].astype(str).str.slice(0, 6)
        latest_rows["close"] = latest_rows["adj_close"]
        return latest_rows[
            [
                "symbol",
                "ret_5",
                "ret_20",
                "ret_60",
                "vol_ratio",
                "macd",
                "macd_signal",
                "rsi_6",
                "k",
                "d",
                "j",
                "cci",
                "bias",
                "close",
            ]
        ]

    def _fetch_value(self, engine: Any, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql = f"""
            SELECT ts_code, pe_ttm, pb, ps_ttm, turnover_rate_f
            FROM tushare_stock.dwd_daily_basic
            WHERE trade_date = %s
              AND ts_code IN ({self._in_clause(ts_codes)})
        """

        try:
            params = [trade_date_int]
            params.extend(ts_codes)
            df = self._query_df(engine, sql, tuple(params))
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "pe_ttm", "pb", "ps_ttm", "turnover_rate_f"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch value factors")
            return pd.DataFrame(columns=["symbol"])

    def _fetch_quality(self, engine: Any, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        start_ann_date = trade_date_int - 10000
        sql = f"""
            SELECT ts_code, ann_date, roe, grossprofit_margin, debt_to_assets
            FROM tushare_stock.dwd_fina_indicator
            WHERE ann_date BETWEEN %s AND %s
              AND ts_code IN ({self._in_clause(ts_codes)})
        """

        try:
            params = [start_ann_date, trade_date_int]
            params.extend(ts_codes)
            df = self._query_df(engine, sql, tuple(params))
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df = df.sort_values("ann_date").groupby("ts_code", as_index=False).last()
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "roe", "grossprofit_margin", "debt_to_assets"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch quality factors")
            return pd.DataFrame(columns=["symbol"])

    def _fetch_capital(self, engine: Any, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql_mf = f"""
            SELECT ts_code, buy_elg_amount, buy_lg_amount
            FROM tushare_stock.ods_moneyflow
            WHERE trade_date = %s
              AND ts_code IN ({self._in_clause(ts_codes)})
        """

        sql_mg = f"""
            SELECT ts_code, rzmre, rzche, rzye
            FROM tushare_stock.ods_margin_detail
            WHERE trade_date = %s
              AND ts_code IN ({self._in_clause(ts_codes)})
        """

        try:
            params = [trade_date_int]
            params.extend(ts_codes)
            df_mf = self._query_df(engine, sql_mf, tuple(params))
            df_mg = self._query_df(engine, sql_mg, tuple(params))

            df = pd.DataFrame({"symbol": [c[:6] for c in ts_codes]})

            if not df_mf.empty:
                df_mf["symbol"] = df_mf["ts_code"].str.slice(0, 6)
                df_mf["big_order_flow"] = df_mf["buy_elg_amount"].fillna(0) + df_mf["buy_lg_amount"].fillna(0)
                df = df.merge(df_mf[["symbol", "big_order_flow"]], on="symbol", how="left")

            if not df_mg.empty:
                df_mg["symbol"] = df_mg["ts_code"].str.slice(0, 6)
                margin_net_buy = df_mg["rzmre"].fillna(0) - df_mg["rzche"].fillna(0)
                df_mg["margin_ratio"] = np.where(df_mg["rzye"] > 0, margin_net_buy / df_mg["rzye"], np.nan)
                df = df.merge(df_mg[["symbol", "margin_ratio"]], on="symbol", how="left")

            return df
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch capital factors")
            return pd.DataFrame(columns=["symbol"])

    def _fetch_chip(self, engine: Any, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql = f"""
            SELECT ts_code, winner_rate, cost_50pct
            FROM tushare_stock.ods_cyq_perf
            WHERE trade_date = %s
              AND ts_code IN ({self._in_clause(ts_codes)})
        """

        try:
            params = [trade_date_int]
            params.extend(ts_codes)
            df = self._query_df(engine, sql, tuple(params))
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "winner_rate", "cost_50pct"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch chip factors")
            return pd.DataFrame(columns=["symbol"])

    def _get_ts_code_map(self, engine: Any, symbols: List[str]) -> dict:
        if not symbols:
            return {}

        sql = f"""
            SELECT symbol, ts_code
            FROM tushare_stock.dim_stock
            WHERE symbol IN ({self._in_clause(symbols)})
        """

        try:
            df = self._query_df(engine, sql, tuple(symbols))
            if df.empty:
                return {}
            return dict(zip(df["symbol"].astype(str).str.zfill(6), df["ts_code"].astype(str)))
        except Exception:
            logger.exception("ClaudeScorer: failed to map symbol -> ts_code")
            return {}

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile_points(series: pd.Series, max_points: float, higher_is_better: bool = True) -> np.ndarray:
        s = pd.to_numeric(series, errors="coerce")
        valid = s.notna()
        out = np.zeros(len(s), dtype=float)
        if valid.sum() == 0:
            return out

        ranks = s[valid].rank(pct=True)
        if not higher_is_better:
            ranks = 1.0 - ranks
        out[valid.to_numpy()] = np.clip(ranks.to_numpy(), 0.0, 1.0) * max_points
        return out

    @staticmethod
    def _numeric_series(df: pd.DataFrame, key: str) -> pd.Series:
        raw = df.get(key, pd.Series(index=df.index, dtype=float))
        if isinstance(raw, pd.DataFrame):
            raw = raw.iloc[:, 0]
        elif not isinstance(raw, pd.Series):
            raw = pd.Series([raw] * len(df), index=df.index)
        else:
            raw = raw.reindex(df.index)
        return pd.to_numeric(raw, errors="coerce")

    def _score_momentum(self, df: pd.DataFrame) -> np.ndarray:
        # 25 pts = ret_5(5) + ret_20(6) + ret_60(7) + vol_ratio(4) + turnover(3)
        score = np.zeros(len(df), dtype=float)
        score += self._percentile_points(df.get("ret_5", pd.Series(index=df.index)), 5, higher_is_better=True)
        score += self._percentile_points(df.get("ret_20", pd.Series(index=df.index)), 6, higher_is_better=True)
        score += self._percentile_points(df.get("ret_60", pd.Series(index=df.index)), 7, higher_is_better=True)
        score += self._percentile_points(df.get("vol_ratio", pd.Series(index=df.index)), 4, higher_is_better=True)
        score += self._percentile_points(df.get("turnover_rate_f", pd.Series(index=df.index)), 3, higher_is_better=True)
        return np.clip(score, 0.0, 25.0)

    def _score_value(self, df: pd.DataFrame) -> np.ndarray:
        # 20 pts = PE(7) + PB(7) + PS(6), lower is better
        score = np.zeros(len(df), dtype=float)
        score += self._percentile_points(df.get("pe_ttm", pd.Series(index=df.index)), 7, higher_is_better=False)
        score += self._percentile_points(df.get("pb", pd.Series(index=df.index)), 7, higher_is_better=False)
        score += self._percentile_points(df.get("ps_ttm", pd.Series(index=df.index)), 6, higher_is_better=False)
        return np.clip(score, 0.0, 20.0)

    def _score_quality(self, df: pd.DataFrame) -> np.ndarray:
        # 20 pts = ROE(8) + gross margin(6) + debt_to_assets(6, lower better)
        score = np.zeros(len(df), dtype=float)
        score += self._percentile_points(df.get("roe", pd.Series(index=df.index)), 8, higher_is_better=True)
        score += self._percentile_points(df.get("grossprofit_margin", pd.Series(index=df.index)), 6, higher_is_better=True)
        score += self._percentile_points(df.get("debt_to_assets", pd.Series(index=df.index)), 6, higher_is_better=False)
        return np.clip(score, 0.0, 20.0)

    def _score_technical(self, df: pd.DataFrame) -> np.ndarray:
        # 15 pts via deterministic indicator rules (no unconditional base score)
        score = np.zeros(len(df), dtype=float)

        macd = self._numeric_series(df, "macd")
        macd_signal = self._numeric_series(df, "macd_signal")
        score += np.where((macd > 0) & (macd > macd_signal), 4, np.where(macd > 0, 2, 0))

        rsi = self._numeric_series(df, "rsi_6")
        score += np.where((rsi >= 40) & (rsi <= 65), 3, np.where((rsi >= 30) & (rsi < 40), 1.5, 0))

        k = self._numeric_series(df, "k")
        d = self._numeric_series(df, "d")
        score += np.where((k > d) & (k < 80), 3, np.where((k > d), 1.5, 0))

        cci = self._numeric_series(df, "cci")
        score += np.where((cci > -100) & (cci < 150), 3, np.where((cci >= 150), 1, 0))

        bias = self._numeric_series(df, "bias").abs()
        score += np.where(bias <= 0.05, 2, np.where(bias <= 0.1, 1, 0))

        return np.clip(score, 0.0, 15.0)

    def _score_capital(self, df: pd.DataFrame) -> np.ndarray:
        # 10 pts = big_order_flow(6) + margin_ratio(4)
        score = np.zeros(len(df), dtype=float)
        score += self._percentile_points(df.get("big_order_flow", pd.Series(index=df.index)), 6, higher_is_better=True)
        score += self._percentile_points(df.get("margin_ratio", pd.Series(index=df.index)), 4, higher_is_better=True)
        return np.clip(score, 0.0, 10.0)

    def _score_chip(self, df: pd.DataFrame) -> np.ndarray:
        # 10 pts = winner_rate near 20-40 better (6) + cost deviation (4)
        score = np.zeros(len(df), dtype=float)

        wr = self._numeric_series(df, "winner_rate")
        # triangular preference centered at 30, range +/-30
        wr_pts = (1 - (wr - 30).abs() / 30).clip(lower=0, upper=1) * 6
        score += wr_pts.fillna(0).to_numpy()

        cost = self._numeric_series(df, "cost_50pct").replace(0, np.nan)
        close = self._numeric_series(df, "close")
        ratio = close / cost
        ratio_pts = np.where(ratio > 1.1, 4, np.where(ratio > 1.03, 2.5, np.where(ratio > 0.97, 1, 0)))
        score += np.nan_to_num(ratio_pts, nan=0.0)

        return np.clip(score, 0.0, 10.0)
