from __future__ import annotations

from collections.abc import Iterable, Sequence

import pandas as pd
import pymysql

from backtest_engine.core.types import Bar
from backtest_engine.datafeed.base import DataFeed


class TushareDailyFeed(DataFeed):
    """MySQL tushare_stock 日频数据喂入。

    优先通过 ADCClient 消费数据底座规范日行情，并绑定 ExecutionContext 数据版本。
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str = "tushare_stock",
        table: str = "dwd_stock_daily_standard",
        execution_context: Any = None,
        mode: str = "production",
        allow_research_fallback: bool = False,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.table = table
        self.execution_context = execution_context
        self.mode = mode
        self.allow_research_fallback = allow_research_fallback

    def _conn(self):
        # 优先读取只读环境变量与 ADCClient 配置
        h = self.host or "127.0.0.1"
        p = self.port or 3306
        u = self.user or "ro_user"
        pwd = self.password or ""
        return pymysql.connect(
            host=h,
            port=p,
            user=u,
            password=pwd,
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

        start_int = int(pd.Timestamp(start).strftime("%Y%m%d"))
        end_int = int(pd.Timestamp(end).strftime("%Y%m%d"))

        # 优先走统一跨项目数据底座客户端
        # 研究回退条件必须严格同时满足：1.显式开启allow_research_fallback; 2.mode为RESEARCH; 3.execution_context.research_only=True
        is_research = (
            bool(self.allow_research_fallback)
            and str(self.mode).strip().upper() == "RESEARCH"
            and bool(self.execution_context and getattr(self.execution_context, "research_only", False))
        )
        try:
            from cross_project.adc_client import ADCClient
            client = ADCClient()
            rel_id = getattr(self.execution_context, "release_id", None)
            pit_id = getattr(self.execution_context, "pit_snapshot_id", None)
            df = client.data.get_daily_bars_batch(
                symbols=list(universe),
                start_date=start_int,
                end_date=end_int,
                adj_type="qfq" if self.table == "dwd_stock_daily_standard" else "raw",
                release_id=rel_id,
                pit_snapshot_id=pit_id,
                execution_context=self.execution_context,
            )
            if not df.empty:
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
                return
            elif not is_research:
                raise RuntimeError(
                    f"可信回测行情数据源返回为空 (universe={len(universe)}, {start_int}-{end_int})，生产/可信模式严禁使用未认证备用数据！"
                )
        except Exception as exc:
            if not is_research:
                raise RuntimeError(
                    f"可信回测行情数据源获取失败: {exc}，严格禁止静默回退未认证数据库表！"
                ) from exc
            import logging
            logging.getLogger(__name__).warning("RESEARCH ONLY: 可信回测数据源读取失败，经显式研究模式授权回退备用 SQL: %s", exc)

        if not is_research:
            raise RuntimeError("可信回测/生产模式严禁执行离线未认证回退路径！")

        # 回退离线回测路径
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
