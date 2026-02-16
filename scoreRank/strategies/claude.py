import logging
from datetime import datetime, timedelta
from typing import List

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from scoreRank.strategies.base import BaseScorer

logger = logging.getLogger(__name__)


class ClaudeScorer(BaseScorer):
    """
    Multi-factor scorer with six dimensions:
    momentum / value / quality / technical / capital / chip.

    Total score = 100 points.
    """

    def score(self, symbols: List[str], asof_date: pd.Timestamp, engine: Engine) -> pd.DataFrame:
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

    def _fetch_technical_momentum(self, engine: Engine, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        dt = datetime.strptime(str(trade_date_int), "%Y%m%d")
        start_date_int = int((dt - timedelta(days=180)).strftime("%Y%m%d"))

        sql = text(
            """
            SELECT trade_date, ts_code, adj_close, adj_high, adj_low, vol
            FROM tushare_stock.dwd_stock_daily_standard
            WHERE trade_date BETWEEN :start_date AND :end_date
              AND ts_code IN :ts_codes
            ORDER BY ts_code ASC, trade_date ASC
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        try:
            df = pd.read_sql(
                sql,
                engine,
                params={"start_date": start_date_int, "end_date": trade_date_int, "ts_codes": ts_codes},
            )
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch technical/momentum data")
            return pd.DataFrame(columns=["symbol"])

        if df.empty:
            return pd.DataFrame(columns=["symbol"])

        results = []
        target_date = pd.to_datetime(str(trade_date_int))
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str))

        for ts_code, group in df.groupby("ts_code"):
            group = group.sort_values("trade_date").reset_index(drop=True)
            if group.iloc[-1]["trade_date"] != target_date:
                continue

            close = group["adj_close"]
            high = group["adj_high"]
            low = group["adj_low"]

            curr_close = close.iloc[-1]

            def get_ret(days: int):
                if len(group) <= days:
                    return np.nan
                prev = close.iloc[-(days + 1)]
                if pd.isna(prev) or prev == 0:
                    return np.nan
                return (curr_close - prev) / prev

            ret_5 = get_ret(5)
            ret_20 = get_ret(20)
            ret_60 = get_ret(60)

            if len(group) >= 6:
                ma5_vol = group.iloc[-6:-1]["vol"].mean()
                vol_ratio = group.iloc[-1]["vol"] / ma5_vol if ma5_vol and ma5_vol > 0 else np.nan
            else:
                vol_ratio = np.nan

            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            macd_signal = macd.ewm(span=9, adjust=False).mean()

            # RSI(6)
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(window=6).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=6).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_6 = 100 - (100 / (1 + rs))

            # KDJ(9,3,3)
            low_9 = low.rolling(window=9).min()
            high_9 = high.rolling(window=9).max()
            rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
            k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
            d = k.ewm(alpha=1 / 3, adjust=False).mean()
            j = 3 * k - 2 * d

            # CCI(14)
            tp = (high + low + close) / 3
            ma_tp = tp.rolling(window=14).mean()
            md = (tp - ma_tp).abs().rolling(window=14).mean()
            cci = (tp - ma_tp) / (0.015 * md.replace(0, np.nan))

            ma20 = close.rolling(window=20).mean()
            bias = (close - ma20) / ma20.replace(0, np.nan)

            results.append(
                {
                    "symbol": ts_code[:6],
                    "ret_5": ret_5,
                    "ret_20": ret_20,
                    "ret_60": ret_60,
                    "vol_ratio": vol_ratio,
                    "macd": macd.iloc[-1],
                    "macd_signal": macd_signal.iloc[-1],
                    "rsi_6": rsi_6.iloc[-1],
                    "k": k.iloc[-1],
                    "d": d.iloc[-1],
                    "j": j.iloc[-1],
                    "cci": cci.iloc[-1],
                    "bias": bias.iloc[-1],
                    "close": curr_close,
                }
            )

        return pd.DataFrame(results)

    def _fetch_value(self, engine: Engine, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql = text(
            """
            SELECT ts_code, pe_ttm, pb, ps_ttm, turnover_rate_f
            FROM tushare_stock.dwd_daily_basic
            WHERE trade_date = :trade_date
              AND ts_code IN :ts_codes
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        try:
            df = pd.read_sql(sql, engine, params={"trade_date": trade_date_int, "ts_codes": ts_codes})
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "pe_ttm", "pb", "ps_ttm", "turnover_rate_f"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch value factors")
            return pd.DataFrame(columns=["symbol"])

    def _fetch_quality(self, engine: Engine, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        start_ann_date = trade_date_int - 10000
        sql = text(
            """
            SELECT ts_code, ann_date, roe, grossprofit_margin, debt_to_assets
            FROM tushare_stock.dwd_fina_indicator
            WHERE ann_date BETWEEN :start_ann_date AND :trade_date
              AND ts_code IN :ts_codes
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        try:
            df = pd.read_sql(
                sql,
                engine,
                params={"start_ann_date": start_ann_date, "trade_date": trade_date_int, "ts_codes": ts_codes},
            )
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df = df.sort_values("ann_date").groupby("ts_code", as_index=False).last()
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "roe", "grossprofit_margin", "debt_to_assets"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch quality factors")
            return pd.DataFrame(columns=["symbol"])

    def _fetch_capital(self, engine: Engine, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql_mf = text(
            """
            SELECT ts_code, buy_elg_amount, buy_lg_amount
            FROM tushare_stock.ods_moneyflow
            WHERE trade_date = :trade_date
              AND ts_code IN :ts_codes
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        sql_mg = text(
            """
            SELECT ts_code, rzmre, rzche, rzye
            FROM tushare_stock.ods_margin_detail
            WHERE trade_date = :trade_date
              AND ts_code IN :ts_codes
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        try:
            df_mf = pd.read_sql(sql_mf, engine, params={"trade_date": trade_date_int, "ts_codes": ts_codes})
            df_mg = pd.read_sql(sql_mg, engine, params={"trade_date": trade_date_int, "ts_codes": ts_codes})

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

    def _fetch_chip(self, engine: Engine, ts_code_map: dict, trade_date_int: int) -> pd.DataFrame:
        ts_codes = list(ts_code_map.values())
        if not ts_codes:
            return pd.DataFrame(columns=["symbol"])

        sql = text(
            """
            SELECT ts_code, winner_rate, cost_50pct
            FROM tushare_stock.ods_cyq_perf
            WHERE trade_date = :trade_date
              AND ts_code IN :ts_codes
            """
        ).bindparams(bindparam("ts_codes", expanding=True))

        try:
            df = pd.read_sql(sql, engine, params={"trade_date": trade_date_int, "ts_codes": ts_codes})
            if df.empty:
                return pd.DataFrame(columns=["symbol"])
            df["symbol"] = df["ts_code"].str.slice(0, 6)
            return df[["symbol", "winner_rate", "cost_50pct"]]
        except Exception:
            logger.exception("ClaudeScorer: failed to fetch chip factors")
            return pd.DataFrame(columns=["symbol"])

    def _get_ts_code_map(self, engine: Engine, symbols: List[str]) -> dict:
        if not symbols:
            return {}

        sql = text(
            """
            SELECT symbol, ts_code
            FROM tushare_stock.dim_stock
            WHERE symbol IN :symbols
            """
        ).bindparams(bindparam("symbols", expanding=True))

        try:
            df = pd.read_sql(sql, engine, params={"symbols": symbols})
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

        macd = pd.to_numeric(df.get("macd"), errors="coerce")
        macd_signal = pd.to_numeric(df.get("macd_signal"), errors="coerce")
        score += np.where((macd > 0) & (macd > macd_signal), 4, np.where(macd > 0, 2, 0))

        rsi = pd.to_numeric(df.get("rsi_6"), errors="coerce")
        score += np.where((rsi >= 40) & (rsi <= 65), 3, np.where((rsi >= 30) & (rsi < 40), 1.5, 0))

        k = pd.to_numeric(df.get("k"), errors="coerce")
        d = pd.to_numeric(df.get("d"), errors="coerce")
        score += np.where((k > d) & (k < 80), 3, np.where((k > d), 1.5, 0))

        cci = pd.to_numeric(df.get("cci"), errors="coerce")
        score += np.where((cci > -100) & (cci < 150), 3, np.where((cci >= 150), 1, 0))

        bias = pd.to_numeric(df.get("bias"), errors="coerce").abs()
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

        wr = pd.to_numeric(df.get("winner_rate"), errors="coerce")
        # triangular preference centered at 30, range +/-30
        wr_pts = (1 - (wr - 30).abs() / 30).clip(lower=0, upper=1) * 6
        score += wr_pts.fillna(0).to_numpy()

        cost = pd.to_numeric(df.get("cost_50pct"), errors="coerce").replace(0, np.nan)
        close = pd.to_numeric(df.get("close"), errors="coerce")
        ratio = close / cost
        ratio_pts = np.where(ratio > 1.1, 4, np.where(ratio > 1.03, 2.5, np.where(ratio > 0.97, 1, 0)))
        score += np.nan_to_num(ratio_pts, nan=0.0)

        return np.clip(score, 0.0, 10.0)
