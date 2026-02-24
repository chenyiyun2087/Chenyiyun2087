"""JoinQuant 策略本地化适配器（chenyiyun1.py）。

核心目标：
1) 从 `tushare_stock` 数仓读取稳定字段；
2) 保留原策略选股链路（高股息→高波动→低杠杆→小市值）；
3) 支持离线信号输出及落库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    index_code: Optional[str] = None
    holding_days: int = 20


class TushareWarehouseProvider:
    """Read-only provider for tushare_stock warehouse."""

    REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
        "dwd_daily": {"trade_date"},
        "dwd_stock_label_daily": {"ts_code", "trade_date", "is_st"},
        "dim_stock": {"ts_code", "list_date"},
        "dwd_daily_basic": {"ts_code", "trade_date", "circ_mv", "dv_ratio"},
        "dwd_fina_indicator": {"ts_code", "ann_date", "debt_to_assets"},
        "dws_liquidity_factor": {"ts_code", "trade_date", "turnover_vol_20"},
        "ods_index_weight": {"trade_date", "index_code", "con_code"},
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
        # Ensure input_date is converted to int YYYYMMDD for comparison with int column
        date_int = int(input_date.strftime("%Y%m%d"))
        
        sql = "SELECT MAX(trade_date) AS d FROM dwd_daily WHERE trade_date<=%s"
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[date_int])
        
        if df.empty or pd.isna(df.iloc[0]["d"]):
            raise RuntimeError(f"dwd_daily 无可用交易日 (<= {input_date})，请先同步 ODS/DWD 数据")
        
        # Return as date object for internal consistency
        # df.iloc[0]["d"] is int YYYYMMDD
        return pd.to_datetime(str(df.iloc[0]["d"]), format="%Y%m%d").date()

    def get_universe(self, trade_date: date) -> pd.DataFrame:
        date_int = int(trade_date.strftime("%Y%m%d"))
        sql = (
            "SELECT t1.ts_code, t1.trade_date, t1.is_st, t2.list_date "
            "FROM dwd_stock_label_daily t1 "
            "JOIN dim_stock t2 ON t1.ts_code = t2.ts_code "
            "WHERE t1.trade_date=%s"
        )
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[date_int])
        
        # Calculate list_days
        df['trade_date_dt'] = pd.to_datetime(df['trade_date'].astype(str), format='%Y%m%d', errors='coerce')
        df['list_date_dt'] = pd.to_datetime(df['list_date'].astype(str), format='%Y%m%d', errors='coerce')
        df['list_days'] = (df['trade_date_dt'] - df['list_date_dt']).dt.days
        return df

    def get_all_trading_days(self, start: date, end: date) -> list[date]:
        start_int = int(start.strftime("%Y%m%d"))
        end_int = int(end.strftime("%Y%m%d"))
        sql = "SELECT DISTINCT trade_date FROM dwd_daily WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date"
        with self._conn() as conn:
            df = pd.read_sql(sql, conn, params=[start_int, end_int])
        return [pd.to_datetime(str(d), format="%Y%m%d").date() for d in df["trade_date"]]

    def get_index_members(self, index_code: str, trade_date: date) -> list[str]:
        date_int = int(trade_date.strftime("%Y%m%d"))
        # Use ods_index_weight since ods_index_member is empty. 
        # Find members on the given trade_date or the latest available before it.
        with self._conn() as conn:
            # First check if there is data for the exact date
            sql_exact = "SELECT con_code FROM ods_index_weight WHERE index_code=%s AND trade_date=%s"
            df = pd.read_sql(sql_exact, conn, params=[index_code, date_int])
            
            if df.empty:
                # Fallback: get the latest date before or on trade_date
                sql_latest_date = "SELECT MAX(trade_date) FROM ods_index_weight WHERE index_code=%s AND trade_date<=%s"
                with conn.cursor() as cur:
                    cur.execute(sql_latest_date, (index_code, date_int))
                    latest_date = cur.fetchone()[0]
                
                if latest_date:
                    sql_fallback = "SELECT con_code FROM ods_index_weight WHERE index_code=%s AND trade_date=%s"
                    df = pd.read_sql(sql_fallback, conn, params=[index_code, latest_date])
                    
        return df["con_code"].tolist()

    def get_factor_frame(self, trade_date: date) -> pd.DataFrame:
        date_int = int(trade_date.strftime("%Y%m%d"))
        one_year_ago = int((trade_date - timedelta(days=365)).strftime("%Y%m%d"))

        with self._conn() as conn:
            # 市值 (万元) + 价格 (用于计算股息率)
            basic = pd.read_sql(
                "SELECT ts_code, trade_date, circ_mv, total_mv, close "
                "FROM dwd_daily_basic WHERE trade_date=%s",
                conn,
                params=[date_int],
            )

            # 股息 (元) - TTM (Past 1 year based on ex_date)
            div = pd.read_sql(
                "SELECT ts_code, SUM(cash_div_tax) as total_div_ttm "
                "FROM ods_dividend "
                "WHERE ex_date <= %s AND ex_date > %s AND div_proc = '实施' "
                "GROUP BY ts_code",
                conn,
                params=[date_int, one_year_ago],
            )

            # 杠杆因子: 取截至 trade_date 最新披露的 total_assets 和 total_hldr_eqy
            fina = pd.read_sql(
                "SELECT ts_code, ann_date, total_assets, total_hldr_eqy "
                "FROM dwd_fina_indicator WHERE ann_date<=%s",
                conn,
                params=[date_int],
            )

            # 换手波动
            liq = pd.read_sql(
                "SELECT ts_code, turnover_vol_20 as turnover_volatility "
                "FROM dws_liquidity_factor WHERE trade_date=%s",
                conn,
                params=[date_int],
            )

        fina_latest = (
            fina.sort_values(["ts_code", "ann_date"]).groupby("ts_code", as_index=False).tail(1)[["ts_code", "total_assets", "total_hldr_eqy"]]
            if not fina.empty
            else pd.DataFrame(columns=["ts_code", "total_assets", "total_hldr_eqy"])
        )

        out = basic.merge(fina_latest, on="ts_code", how="left").merge(liq, on="ts_code", how="left").merge(div, on="ts_code", how="left")

        # Compute MLEV = (Total Market Cap + Total Liabilities) / Total Market Cap
        # total_mv is in 万元, total_assets/hldr_eqy are in 元
        out["total_liab_wan"] = (out["total_assets"] - out["total_hldr_eqy"]) / 10000.0
        out["mlev"] = (out["total_mv"] + out["total_liab_wan"]) / out["total_mv"]

        # Compute Dividend Ratio (Yield)
        # total_div_ttm (元/股) / close (元/股)
        out["dividend_ratio"] = out["total_div_ttm"] / out["close"]
        
        # Fill NaNs
        out["dividend_ratio"] = out["dividend_ratio"].fillna(0)
        out["mlev"] = out["mlev"].fillna(out["mlev"].median()) # Handle missing debt data

        out = out.rename(
            columns={
                "circ_mv": "circulating_market_cap",
            }
        )
        return out[["ts_code", "trade_date", "circulating_market_cap", "dividend_ratio", "mlev", "turnover_volatility"]]



class LocalHighDividendStrategy:
    def __init__(self, provider: TushareWarehouseProvider, config: Optional[StrategyConfig] = None):
        self.provider = provider
        self.config = config or StrategyConfig()

    @staticmethod
    def _filter_kcbj(ts_codes: list[str]) -> list[str]:
        out: list[str] = []
        for code in ts_codes:
            if code.endswith(".BJ"):
                continue
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
        
        # Filter by index members if configured
        if self.config.index_code:
            members = self.provider.get_index_members(self.config.index_code, trade_date)
            df = df[df["ts_code"].isin(members)]

        df = df[df["is_st"] == 0]
        # df = df[df["list_days"] >= self.config.new_stock_days] # logic inside get_universe? No, logic is here in code but I need to make sure column exists
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
        if not re.match(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?$", table):
            raise ValueError("signal table 名称仅允许 table 或 schema.table")

        with self.provider._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table}
                    (
                        trade_date DATE,
                        ts_code VARCHAR(16),
                        `signal` VARCHAR(16),
                        target_weight DOUBLE,
                        rank_num INT,
                        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (trade_date, ts_code)
                    )
                    """
                )
                sql = (
                    f"INSERT INTO {table} (trade_date, ts_code, `signal`, target_weight, rank_num) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE `signal`=VALUES(`signal`), target_weight=VALUES(target_weight), rank_num=VALUES(rank_num)"
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
    parser.add_argument("--index", default=None, help="filter pool by index code, e.g. 000300.SH")
    parser.add_argument("--top", type=int, default=10, help="target holding count")
    parser.add_argument("--holding-days", type=int, default=20, help="target holding horizon")
    parser.add_argument("--emit-signals", action="store_true", help="write daily signals into ADS table")
    parser.add_argument("--signal-table", default="ads_local_strategy_signals")
    args = parser.parse_args()

    cfg = DBConfig(args.host, args.port, args.user, args.password, args.database)
    strategy = LocalHighDividendStrategy(
        TushareWarehouseProvider(cfg), 
        StrategyConfig(stock_num=args.top, index_code=args.index, holding_days=args.holding_days)
    )
    asof = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None

    picked = strategy.pick(asof)
    print(picked.head(args.top).to_string(index=False))

    if args.emit_signals:
        signals = strategy.build_daily_signals(asof)
        strategy.save_daily_signals(signals, table=args.signal_table)
        print(f"saved {len(signals)} rows into {args.signal_table}")


if __name__ == "__main__":
    main()
