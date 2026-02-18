from __future__ import annotations

from collections.abc import Iterable, Sequence

import pandas as pd
import pymysql

from backtest_engine.core.types import Bar
from backtest_engine.datafeed.base import DataFeed


class TushareDailyFeed(DataFeed):
    """MySQL tushare_stock 日频数据喂入。

    默认读取 dwd_stock_daily_standard；若字段不存在则尝试 dwd_daily。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "tushare_stock",
        table: str = "dwd_stock_daily_standard",
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.table = table

    def _conn(self):
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
        )

    def _columns(self, table: str) -> set[str]:
        sql = (
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s"
        )
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[self.database, table])
        return set(df["COLUMN_NAME"].tolist())

    def _resolve_table(self) -> tuple[str, dict[str, str]]:
        candidates = [self.table, "dwd_daily"]
        for t in candidates:
            cols = self._columns(t)
            if {"ts_code", "trade_date", "open", "high", "low", "close"}.issubset(cols):
                mapping = {
                    "ts_code": "ts_code",
                    "trade_date": "trade_date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "vol": "vol" if "vol" in cols else ("volume" if "volume" in cols else None),
                }
                return t, mapping
        raise RuntimeError("无法解析行情表，至少需要 ts_code/trade_date/open/high/low/close")

    def iter_bars(
        self,
        start: str,
        end: str,
        universe: Sequence[str],
        fields: Sequence[str] | None,
        freq: str,
    ) -> Iterable[Bar]:
        if freq != "1d":
            raise ValueError("TushareDailyFeed 仅支持 1d")
        if not universe:
            return

        table, mp = self._resolve_table()
        vol_expr = f", {mp['vol']} AS volume" if mp.get("vol") else ""
        in_placeholders = ",".join(["%s"] * len(universe))
        sql = (
            f"SELECT {mp['trade_date']} AS trade_date, {mp['ts_code']} AS ts_code, "
            f"{mp['open']} AS open, {mp['high']} AS high, {mp['low']} AS low, {mp['close']} AS close"
            f"{vol_expr} "
            f"FROM {table} WHERE trade_date BETWEEN %s AND %s AND ts_code IN ({in_placeholders}) "
            "ORDER BY trade_date, ts_code"
        )

        # Date conversion for integer trade_date column
        start_int = int(pd.Timestamp(start).strftime("%Y%m%d"))
        end_int = int(pd.Timestamp(end).strftime("%Y%m%d"))
        
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[start_int, end_int, *universe])

        if df.empty:
            return

        for r in df.itertuples(index=False):
            yield Bar(
                ts=str(pd.to_datetime(str(r.trade_date), format="%Y%m%d").date()),
                symbol=r.ts_code,
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=float(getattr(r, "volume", 0.0) or 0.0),
            )
