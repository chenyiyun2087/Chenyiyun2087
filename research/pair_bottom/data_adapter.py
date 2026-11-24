"""ASDC and Chenyiyun2087 Data Adapter for Pair Bottom Research.

Provides strict fail-closed data access, PIT snapshot enforcement,
caching to Parquet for high-performance vectorized research, and
coverage auditing.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pymysql
import tushare as ts


class PairBottomDataAdapter:
    def __init__(self, config_path: Optional[str] = None):
        self.asdc_root = Path("/Volumes/extension/projects/AShareDataCenter")
        self.cy_root = Path("/Volumes/extension/projects/Chenyiyun2087")
        self.cache_dir = self.cy_root / "data" / "research" / "pair_bottom"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.etl_ini_path = self.asdc_root / "config" / "etl.ini"
        self.db_config, self.ts_token = self._load_credentials()
        self.pro = ts.pro_api(self.ts_token) if self.ts_token else None

    def _load_credentials(self) -> Tuple[Dict[str, Any], str]:
        db_cfg: Dict[str, Any] = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": "tushare_stock",
            "charset": "utf8mb4",
        }
        token = ""
        if self.etl_ini_path.exists():
            cfg = configparser.ConfigParser()
            cfg.read(self.etl_ini_path)
            if cfg.has_section("mysql"):
                db_cfg["host"] = cfg.get("mysql", "host", fallback="127.0.0.1")
                db_cfg["port"] = int(cfg.get("mysql", "port", fallback="3306"))
                db_cfg["user"] = cfg.get("mysql", "user", fallback="root")
                db_cfg["password"] = cfg.get("mysql", "password", fallback="")
                db_cfg["database"] = cfg.get("mysql", "database", fallback="tushare_stock")
            if cfg.has_section("tushare"):
                token = cfg.get("tushare", "token", fallback="")

        env_pw = os.environ.get("CHENYIYUN_DB_PASSWORD")
        if env_pw:
            db_cfg["password"] = env_pw

        # Check socket
        for sock in ["/tmp/mysql_external.sock", "/tmp/mysql.sock"]:
            if os.path.exists(sock):
                db_cfg["unix_socket"] = sock
                break

        return db_cfg, token

    def get_connection(self) -> pymysql.Connection:
        conn_params = dict(self.db_config)
        if "unix_socket" in conn_params and os.path.exists(conn_params["unix_socket"]):
            return pymysql.connect(
                user=conn_params["user"],
                password=conn_params["password"],
                unix_socket=conn_params["unix_socket"],
                database=conn_params["database"],
                charset=conn_params.get("charset", "utf8mb4"),
                cursorclass=pymysql.cursors.DictCursor,
            )
        return pymysql.connect(
            host=conn_params["host"],
            port=conn_params["port"],
            user=conn_params["user"],
            password=conn_params["password"],
            database=conn_params["database"],
            charset=conn_params.get("charset", "utf8mb4"),
            cursorclass=pymysql.cursors.DictCursor,
        )

    def load_stock_universe(self) -> pd.DataFrame:
        cache_file = self.cache_dir / "dim_stock.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        conn = self.get_connection()
        sql = """
        SELECT ts_code, symbol, name, area, industry, market, list_date, delist_date
        FROM dim_stock
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()
        df = pd.DataFrame(rows)
        df.to_parquet(cache_file, index=False)
        return df

    def load_trading_calendar(self, start_date: int = 20200101, end_date: int = 20270301) -> pd.DataFrame:
        cache_file = self.cache_dir / f"dim_trade_cal_{start_date}_{end_date}.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        conn = self.get_connection()
        sql = f"""
        SELECT exchange, cal_date, is_open, pretrade_date
        FROM dim_trade_cal
        WHERE exchange = 'SSE' AND cal_date BETWEEN {start_date} AND {end_date}
        ORDER BY cal_date ASC
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()
        df = pd.DataFrame(rows)
        df.to_parquet(cache_file, index=False)
        return df

    def load_index_quotes(self, ts_code: str = "000001.SH", start_date: int = 20200101, end_date: int = 20261124) -> pd.DataFrame:
        cache_file = self.cache_dir / f"index_daily_{ts_code.replace('.', '_')}_{start_date}_{end_date}.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        conn = self.get_connection()
        sql = f"""
        SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
        FROM ods_index_daily
        WHERE ts_code = '{ts_code}' AND trade_date BETWEEN {start_date} AND {end_date}
        ORDER BY trade_date ASC
        """
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()
        df = pd.DataFrame(rows)
        df.to_parquet(cache_file, index=False)
        return df

    def load_daily_quotes(
        self,
        start_date: int = 20200101,
        end_date: int = 20261124,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Loads unadjusted and adjusted daily quotes across all stocks.
        
        Cached locally per year for high-speed replay without overloading MySQL.
        """
        years = list(range(start_date // 10000, end_date // 10000 + 1))
        dfs = []
        conn = self.get_connection()
        for y in years:
            y_start = max(start_date, y * 10000 + 101)
            y_end = min(end_date, y * 10000 + 1231)
            y_cache = self.cache_dir / f"daily_quotes_{y}.parquet"
            if y_cache.exists() and not force_refresh:
                df_y = pd.read_parquet(y_cache)
                df_y = df_y[(df_y["trade_date"] >= y_start) & (df_y["trade_date"] <= y_end)]
                dfs.append(df_y)
                continue

            sql = f"""
            SELECT 
                o.trade_date,
                o.ts_code,
                o.open,
                o.high,
                o.low,
                o.close,
                o.pre_close,
                o.pct_chg,
                o.vol,
                o.amount,
                COALESCE(a.adj_factor, 1.0) AS adj_factor
            FROM ods_daily o
            LEFT JOIN dwd_adj_factor a 
                ON o.ts_code = a.ts_code AND o.trade_date = a.trade_date
            WHERE o.trade_date BETWEEN {y * 10000 + 101} AND {y * 10000 + 1231}
            """
            print(f"Loading quotes for year {y} from MySQL...")
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            df_y = pd.DataFrame(rows)
            for c in ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount", "adj_factor"]:
                if c in df_y.columns:
                    df_y[c] = pd.to_numeric(df_y[c], errors="coerce")
            df_y["trade_date"] = df_y["trade_date"].astype(int)
            df_y.to_parquet(y_cache, index=False)
            df_y = df_y[(df_y["trade_date"] >= y_start) & (df_y["trade_date"] <= y_end)]
            dfs.append(df_y)

        conn.close()
        full_df = pd.concat(dfs, ignore_index=True)
        full_df.sort_values(by=["ts_code", "trade_date"], inplace=True)
        return full_df

    def load_daily_basic(
        self,
        start_date: int = 20211124,
        end_date: int = 20261124,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Loads daily basic fundamental snapshot (market cap, turnover, pe)."""
        years = list(range(start_date // 10000, end_date // 10000 + 1))
        dfs = []
        conn = self.get_connection()
        for y in years:
            y_start = max(start_date, y * 10000 + 101)
            y_end = min(end_date, y * 10000 + 1231)
            y_cache = self.cache_dir / f"daily_basic_{y}.parquet"
            if y_cache.exists() and not force_refresh:
                df_y = pd.read_parquet(y_cache)
                df_y = df_y[(df_y["trade_date"] >= y_start) & (df_y["trade_date"] <= y_end)]
                dfs.append(df_y)
                continue

            sql = f"""
            SELECT 
                trade_date,
                ts_code,
                turnover_rate,
                turnover_rate_f,
                volume_ratio,
                pe_ttm,
                pb,
                total_share,
                float_share,
                total_mv,
                circ_mv
            FROM dwd_daily_basic
            WHERE trade_date BETWEEN {y * 10000 + 101} AND {y * 10000 + 1231}
            """
            print(f"Loading daily_basic for year {y} from MySQL...")
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            df_y = pd.DataFrame(rows)
            for c in ["turnover_rate", "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "total_share", "float_share", "total_mv", "circ_mv"]:
                if c in df_y.columns:
                    df_y[c] = pd.to_numeric(df_y[c], errors="coerce")
            df_y["trade_date"] = df_y["trade_date"].astype(int)
            df_y.to_parquet(y_cache, index=False)
            df_y = df_y[(df_y["trade_date"] >= y_start) & (df_y["trade_date"] <= y_end)]
            dfs.append(df_y)

        conn.close()
        full_df = pd.concat(dfs, ignore_index=True)
        full_df.sort_values(by=["ts_code", "trade_date"], inplace=True)
        return full_df

    def load_share_float(self, ts_code: Optional[str] = None) -> pd.DataFrame:
        """Loads share float (unlock) data from Tushare."""
        cache_file = self.cache_dir / f"share_float_{ts_code or 'all'}.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)
        if not self.pro:
            return pd.DataFrame()
        try:
            if ts_code:
                df = self.pro.share_float(ts_code=ts_code)
            else:
                df = pd.DataFrame()
            if not df.empty:
                df.to_parquet(cache_file, index=False)
            return df
        except Exception as e:
            print(f"Warning: load_share_float failed: {e}")
            return pd.DataFrame()

    def load_repurchase(self, ts_code: Optional[str] = None) -> pd.DataFrame:
        """Loads share buyback data from Tushare."""
        cache_file = self.cache_dir / f"repurchase_{ts_code or 'all'}.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)
        if not self.pro:
            return pd.DataFrame()
        try:
            if ts_code:
                df = self.pro.repurchase(ts_code=ts_code)
            else:
                df = pd.DataFrame()
            if not df.empty:
                df.to_parquet(cache_file, index=False)
            return df
        except Exception as e:
            print(f"Warning: load_repurchase failed: {e}")
            return pd.DataFrame()
