"""JoinQuant 策略本地化适配器（chenyiyun1.py）。

核心目标：
1) 从 `tushare_stock` 数仓读取稳定字段；
2) 保留原策略选股链路（高股息→高波动→低杠杆→小市值）；
3) 支持离线信号输出及落库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd
import pymysql


@dataclass
class DBConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "tushare_stock"


@dataclass
class StrategyConfig:
    stock_num: int = 10
    new_stock_days: int = 375
    dividend_top_pct: float = 0.50
    turnover_keep_pct: float = 0.80
    mlev_keep_pct: float = 0.50
    preselect_size: int = 15


class TushareWarehouseProvider:
    """Read-only provider for tushare_stock warehouse."""

    REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
        "dwd_daily": {"trade_date"},
        "dwd_stock_label_daily": {"ts_code", "trade_date", "is_st", "list_days"},
        "dwd_daily_basic": {"ts_code", "trade_date", "circ_mv", "dv_ratio"},
        "dwd_fina_indicator": {"ts_code", "ann_date", "mlev"},
        "dws_liquidity_factor": {"ts_code", "trade_date", "turnover_volatility"},
    }

    def __init__(self, cfg: DBConfig):
        self.cfg = cfg

    def _conn(self):
        return pymysql.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.user,
            password=self.cfg.password,
            database=self.cfg.database,
            charset="utf8mb4",
        )

    def _columns(self, table: str) -> set[str]:
        sql = (
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s"
        )
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[self.cfg.database, table])
        return set(df["COLUMN_NAME"].tolist())

    def validate_schema(self) -> None:
        """Fail-fast schema validation for stable warehouse fields."""
        for table, required_cols in self.REQUIRED_TABLE_COLUMNS.items():
            got = self._columns(table)
            missing = sorted(required_cols - got)
            if missing:
                raise RuntimeError(f"表 {table} 缺少字段: {missing}")

    def get_trade_date(self, input_date: Optional[date] = None) -> date:
        input_date = input_date or date.today()
        sql = "SELECT MAX(trade_date) AS d FROM dwd_daily WHERE trade_date<=%s"
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[input_date])
        if df.empty or pd.isna(df.iloc[0]["d"]):
            raise RuntimeError("dwd_daily 无可用交易日，请先同步 ODS/DWD 数据")
        return pd.Timestamp(df.iloc[0]["d"]).date()

    def get_universe(self, trade_date: date) -> pd.DataFrame:
        sql = (
            "SELECT ts_code, trade_date, is_st, list_days "
            "FROM dwd_stock_label_daily WHERE trade_date=%s"
        )
        with self._conn() as conn:
            return pd.read_sql(sql, conn, params=[trade_date])

    def get_factor_frame(self, trade_date: date) -> pd.DataFrame:
        # 市值 + 股息率
        with self._conn() as conn:
            basic = pd.read_sql(
                "SELECT ts_code, trade_date, circ_mv, dv_ratio "
                "FROM dwd_daily_basic WHERE trade_date=%s",
                conn,
                params=[trade_date],
            )

            # 杠杆因子取截至 trade_date 最新披露
            fina = pd.read_sql(
                "SELECT ts_code, ann_date, mlev "
                "FROM dwd_fina_indicator WHERE ann_date<=%s",
                conn,
                params=[trade_date],
            )

            # 换手波动
            liq = pd.read_sql(
                "SELECT ts_code, turnover_volatility "
                "FROM dws_liquidity_factor WHERE trade_date=%s",
                conn,
                params=[trade_date],
            )

        fina_latest = (
            fina.sort_values(["ts_code", "ann_date"]).groupby("ts_code", as_index=False).tail(1)[["ts_code", "mlev"]]
            if not fina.empty
            else pd.DataFrame(columns=["ts_code", "mlev"])
        )

        out = basic.merge(fina_latest, on="ts_code", how="left").merge(liq, on="ts_code", how="left")
        out = out.rename(
            columns={
                "dv_ratio": "dividend_ratio",
                "circ_mv": "circulating_market_cap",
            }
        )
        return out


class LocalHighDividendStrategy:
    def __init__(self, provider: TushareWarehouseProvider, config: Optional[StrategyConfig] = None):
        self.provider = provider
        self.config = config or StrategyConfig()

    @staticmethod
    def _filter_kcbj(ts_codes: list[str]) -> list[str]:
        out: list[str] = []
        for code in ts_codes:
            raw = code.split(".")[0]
            if raw.startswith(("4", "8", "68")):
                continue
            out.append(code)
        return out

    @staticmethod
    def _pct_slice(df: pd.DataFrame, col: str, ascending: bool, start: float, end: float) -> pd.DataFrame:
        if col not in df.columns:
            return df.iloc[0:0]
        x = df[["ts_code", col]].dropna().sort_values(col, ascending=ascending)
        return x.iloc[int(start * len(x)): int(end * len(x))]

    def pick(self, asof: Optional[date] = None) -> pd.DataFrame:
        self.provider.validate_schema()
        trade_date = self.provider.get_trade_date(asof)
        universe = self.provider.get_universe(trade_date)
        factors = self.provider.get_factor_frame(trade_date)

        df = universe.merge(factors, on=["ts_code", "trade_date"], how="inner")
        df = df[df["is_st"] == 0]
        df = df[df["list_days"] >= self.config.new_stock_days]
        df = df[df["ts_code"].isin(self._filter_kcbj(df["ts_code"].tolist()))]

        dr = self._pct_slice(df, "dividend_ratio", ascending=False, start=0, end=self.config.dividend_top_pct)
        tv_base = df[df["ts_code"].isin(dr["ts_code"])]
        tv = self._pct_slice(tv_base, "turnover_volatility", ascending=False, start=0, end=self.config.turnover_keep_pct)
        lev_base = df[df["ts_code"].isin(tv["ts_code"])]
        lev = self._pct_slice(lev_base, "mlev", ascending=True, start=0, end=self.config.mlev_keep_pct)

        final = (
            df[df["ts_code"].isin(lev["ts_code"])]
            .sort_values("circulating_market_cap", ascending=True)
            .head(self.config.preselect_size)
            .loc[:, ["trade_date", "ts_code", "dividend_ratio", "turnover_volatility", "mlev", "circulating_market_cap"]]
            .reset_index(drop=True)
        )
        return final

    def build_daily_signals(self, asof: Optional[date] = None) -> pd.DataFrame:
        picked = self.pick(asof).head(self.config.stock_num).copy()
        if picked.empty:
            return pd.DataFrame(columns=["trade_date", "ts_code", "signal", "target_weight", "rank"])

        picked["rank"] = range(1, len(picked) + 1)
        picked["signal"] = "BUY"
        picked["target_weight"] = 1.0 / len(picked)
        return picked[["trade_date", "ts_code", "signal", "target_weight", "rank"]]

    def save_daily_signals(self, signals: pd.DataFrame, table: str = "ads_local_strategy_signals") -> None:
        if signals is None or signals.empty:
            return
        if not re.match(r"^[A-Za-z0-9_]+$", table):
            raise ValueError("signal table 名称仅允许字母/数字/下划线")

        with self.provider._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table}
                    (
                        trade_date DATE,
                        ts_code VARCHAR(16),
                        signal VARCHAR(16),
                        target_weight DOUBLE,
                        rank_num INT,
                        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (trade_date, ts_code)
                    )
                    """
                )
                sql = (
                    f"INSERT INTO {table} (trade_date, ts_code, signal, target_weight, rank_num) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE signal=VALUES(signal), target_weight=VALUES(target_weight), rank_num=VALUES(rank_num)"
                )
                rows = [
                    (r.trade_date, r.ts_code, r.signal, float(r.target_weight), int(r.rank))
                    for r in signals.itertuples(index=False)
                ]
                cur.executemany(sql, rows)
            conn.commit()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run local-adapted high-dividend strategy")
    parser.add_argument("--date", default=None, help="as-of date, e.g. 2026-02-17")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="tushare_stock")
    parser.add_argument("--top", type=int, default=10, help="target holding count")
    parser.add_argument("--emit-signals", action="store_true", help="write daily signals into ADS table")
    parser.add_argument("--signal-table", default="ads_local_strategy_signals")
    args = parser.parse_args()

    cfg = DBConfig(args.host, args.port, args.user, args.password, args.database)
    strategy = LocalHighDividendStrategy(TushareWarehouseProvider(cfg), StrategyConfig(stock_num=args.top))
    asof = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    picked = strategy.pick(asof)
    print(picked.head(args.top).to_string(index=False))

    if args.emit_signals:
        signals = strategy.build_daily_signals(asof)
        strategy.save_daily_signals(signals, table=args.signal_table)
        print(f"saved {len(signals)} rows into {args.signal_table}")


if __name__ == "__main__":
    main()
