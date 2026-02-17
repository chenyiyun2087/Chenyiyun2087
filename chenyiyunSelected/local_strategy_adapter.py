"""Local adapter for JoinQuant strategy `chenyiyun1.py`.

目标：把聚宽平台接口（get_price/get_fundamentals/get_factor_values）
迁移为本地 MySQL `tushare_stock` 数仓读取。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

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
    """Read-only provider against tushare_stock warehouse tables.

    说明：由于不同环境字段可能略有差异，这里优先使用信息_schema探测字段，
    能取到就取，取不到就降级（避免一次性强耦合导致策略无法启动）。
    """

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

    def get_trade_date(self, input_date: Optional[date] = None) -> date:
        """Return latest trade_date <= input_date in dwd_daily."""
        input_date = input_date or date.today()
        sql = "SELECT MAX(trade_date) AS d FROM dwd_daily WHERE trade_date<=%s"
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[input_date])
        if df.empty or pd.isna(df.iloc[0]["d"]):
            raise RuntimeError("dwd_daily 无可用交易日，请先同步 ODS/DWD 数据")
        return pd.Timestamp(df.iloc[0]["d"]).date()

    def get_universe(self, trade_date: date) -> pd.DataFrame:
        """Universe with listing and ST flags.

        优先 dwd_stock_label_daily；若不可用降级为 dwd_daily_basic + ts_code。
        """
        label_cols = self._columns("dwd_stock_label_daily")
        if {"ts_code", "trade_date"}.issubset(label_cols):
            sel = ["ts_code", "trade_date"]
            for c in ("is_st", "is_new", "list_days"):
                if c in label_cols:
                    sel.append(c)
            sql = f"SELECT {','.join(sel)} FROM dwd_stock_label_daily WHERE trade_date=%s"
            with self._conn() as conn:
                return pd.read_sql(sql, conn, params=[trade_date])

        basic_cols = self._columns("dwd_daily_basic")
        if "ts_code" not in basic_cols:
            raise RuntimeError("无法构建股票池：dwd_stock_label_daily/dwd_daily_basic 都缺少 ts_code")

        sql = "SELECT ts_code, trade_date FROM dwd_daily_basic WHERE trade_date=%s"
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[trade_date])
        df["is_st"] = 0
        df["is_new"] = 0
        df["list_days"] = 9999
        return df

    def get_factor_frame(self, trade_date: date) -> pd.DataFrame:
        """Build factor frame for selection chain.

        mapping to JQ factors:
        - dividend_ratio -> dwd_daily_basic.dv_ratio 或 dws_value_score.dividend_yield
        - turnover_volatility -> dws_liquidity_factor.turnover_volatility
        - MLEV -> dwd_fina_indicator.mlev
        - circulating_market_cap -> dwd_daily_basic.circ_mv
        """
        parts: List[pd.DataFrame] = []

        # market cap + dividend
        basic_cols = self._columns("dwd_daily_basic")
        basic_fields = ["ts_code", "trade_date"]
        for c in ("circ_mv", "dv_ratio"):
            if c in basic_cols:
                basic_fields.append(c)
        with self._conn() as conn:
            parts.append(pd.read_sql(
                f"SELECT {','.join(basic_fields)} FROM dwd_daily_basic WHERE trade_date=%s",
                conn,
                params=[trade_date],
            ))

        # leverage
        fina_cols = self._columns("dwd_fina_indicator")
        if {"ts_code", "ann_date"}.issubset(fina_cols):
            chosen = ["ts_code", "ann_date"]
            if "mlev" in fina_cols:
                chosen.append("mlev")
            elif "debt_to_assets" in fina_cols:
                chosen.append("debt_to_assets")
            with self._conn() as conn:
                fdf = pd.read_sql(
                    f"SELECT {','.join(chosen)} FROM dwd_fina_indicator WHERE ann_date<=%s",
                    conn,
                    params=[trade_date],
                )
            if not fdf.empty:
                fdf = fdf.sort_values(["ts_code", "ann_date"]).groupby("ts_code").tail(1)
                if "mlev" not in fdf.columns and "debt_to_assets" in fdf.columns:
                    fdf = fdf.rename(columns={"debt_to_assets": "mlev"})
                parts.append(fdf[["ts_code", "mlev"]])

        # turnover volatility
        liq_cols = self._columns("dws_liquidity_factor")
        if {"ts_code", "trade_date", "turnover_volatility"}.issubset(liq_cols):
            with self._conn() as conn:
                parts.append(pd.read_sql(
                    "SELECT ts_code, turnover_volatility FROM dws_liquidity_factor WHERE trade_date=%s",
                    conn,
                    params=[trade_date],
                ))

        # merge
        out = parts[0]
        for p in parts[1:]:
            out = out.merge(p, on="ts_code", how="left")

        if "dv_ratio" in out.columns and "dividend_ratio" not in out.columns:
            out = out.rename(columns={"dv_ratio": "dividend_ratio"})
        if "circ_mv" in out.columns and "circulating_market_cap" not in out.columns:
            out = out.rename(columns={"circ_mv": "circulating_market_cap"})
        return out


class LocalHighDividendStrategy:
    def __init__(self, provider: TushareWarehouseProvider, config: Optional[StrategyConfig] = None):
        self.provider = provider
        self.config = config or StrategyConfig()

    @staticmethod
    def _filter_kcbj(ts_codes: List[str]) -> List[str]:
        out = []
        for c in ts_codes:
            raw = c.split(".")[0]
            if raw.startswith(("4", "8", "68")):
                continue
            out.append(c)
        return out

    @staticmethod
    def _pct_slice(df: pd.DataFrame, col: str, ascending: bool, start: float, end: float) -> pd.DataFrame:
        if col not in df.columns:
            return df.iloc[0:0]
        x = df[["ts_code", col]].dropna().sort_values(col, ascending=ascending)
        return x.iloc[int(start * len(x)): int(end * len(x))]

    def pick(self, asof: Optional[date] = None) -> pd.DataFrame:
        trade_date = self.provider.get_trade_date(asof)
        universe = self.provider.get_universe(trade_date)
        fac = self.provider.get_factor_frame(trade_date)

        df = universe.merge(fac, on=["ts_code", "trade_date"], how="inner")
        if "is_st" in df.columns:
            df = df[df["is_st"] == 0]
        if "list_days" in df.columns:
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
        )
        return final.reset_index(drop=True)

    def build_daily_signals(self, asof: Optional[date] = None) -> pd.DataFrame:
        """Generate local daily signals (Phase-B) from selected candidates.

        Output schema:
        - trade_date
        - ts_code
        - signal (BUY/HOLD)
        - target_weight
        - rank
        """
        picked = self.pick(asof).head(self.config.stock_num).copy()
        if picked.empty:
            return pd.DataFrame(
                columns=["trade_date", "ts_code", "signal", "target_weight", "rank"]
            )
        picked["rank"] = range(1, len(picked) + 1)
        picked["signal"] = "BUY"
        picked["target_weight"] = 1.0 / len(picked)
        return picked[["trade_date", "ts_code", "signal", "target_weight", "rank"]]

    def save_daily_signals(self, signals: pd.DataFrame, table: str = "ads_local_strategy_signals") -> None:
        if signals is None or signals.empty:
            return

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


def main():
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
    scfg = StrategyConfig(stock_num=args.top)
    strategy = LocalHighDividendStrategy(TushareWarehouseProvider(cfg), scfg)
    asof = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    picked = strategy.pick(asof)
    print(picked.head(args.top).to_string(index=False))

    if args.emit_signals:
        signals = strategy.build_daily_signals(asof)
        strategy.save_daily_signals(signals, table=args.signal_table)
        print(f"saved {len(signals)} rows into {args.signal_table}")


if __name__ == "__main__":
    main()
