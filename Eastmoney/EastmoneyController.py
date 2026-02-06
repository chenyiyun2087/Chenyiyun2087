import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Iterable, List, Optional

import pandas as pd
import pymysql
import requests

DEFAULT_MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
}

FUND_FLOW_SQL = """
INSERT INTO em_individual_fund_flow (
    stock_code,
    trade_date,
    close_price,
    pct_change,
    main_net_amount,
    main_net_ratio,
    super_net_amount,
    super_net_ratio,
    big_net_amount,
    big_net_ratio,
    mid_net_amount,
    mid_net_ratio,
    small_net_amount,
    small_net_ratio,
    raw_json,
    created_at,
    updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
)
ON DUPLICATE KEY UPDATE
    close_price = VALUES(close_price),
    pct_change = VALUES(pct_change),
    main_net_amount = VALUES(main_net_amount),
    main_net_ratio = VALUES(main_net_ratio),
    super_net_amount = VALUES(super_net_amount),
    super_net_ratio = VALUES(super_net_ratio),
    big_net_amount = VALUES(big_net_amount),
    big_net_ratio = VALUES(big_net_ratio),
    mid_net_amount = VALUES(mid_net_amount),
    mid_net_ratio = VALUES(mid_net_ratio),
    small_net_amount = VALUES(small_net_amount),
    small_net_ratio = VALUES(small_net_ratio),
    raw_json = VALUES(raw_json),
    updated_at = CURRENT_TIMESTAMP
"""

MARGIN_TRADING_SQL = """
INSERT INTO em_individual_margin_trading (
    stock_code,
    trade_date,
    close_price,
    change_pct,
    rzye,
    rzye_ratio,
    rzmre,
    rzche,
    rzjme,
    rqye,
    rqyl,
    rqmcl,
    rqchl,
    rqjmg,
    rzrqye,
    rzrqye_diff,
    created_at,
    updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW()
)
ON DUPLICATE KEY UPDATE
    close_price = VALUES(close_price),
    change_pct = VALUES(change_pct),
    rzye = VALUES(rzye),
    rzye_ratio = VALUES(rzye_ratio),
    rzmre = VALUES(rzmre),
    rzche = VALUES(rzche),
    rzjme = VALUES(rzjme),
    rqye = VALUES(rqye),
    rqyl = VALUES(rqyl),
    rqmcl = VALUES(rqmcl),
    rqchl = VALUES(rqchl),
    rqjmg = VALUES(rqjmg),
    rzrqye = VALUES(rzrqye),
    rzrqye_diff = VALUES(rzrqye_diff),
    updated_at = CURRENT_TIMESTAMP
"""

COLUMN_ALIASES = {
    "trade_date": ["日期"],
    "close_price": ["收盘价"],
    "change_pct": ["涨跌幅"],
    "main_net_amount": ["主力净流入_净额", "主力净流入净额"],
    "main_net_pct": ["主力净流入_占比", "主力净流入净占比"],
    "super_large_net_amount": ["超大单净流入_净额", "超大单净流入净额"],
    "super_large_net_pct": ["超大单净流入_占比", "超大单净流入净占比"],
    "large_net_amount": ["大单净流入_净额", "大单净流入净额"],
    "large_net_pct": ["大单净流入_占比", "大单净流入净占比"],
    "medium_net_amount": ["中单净流入_净额", "中单净流入净额"],
    "medium_net_pct": ["中单净流入_占比", "中单净流入净占比"],
    "small_net_amount": ["小单净流入_净额", "小单净流入净额"],
    "small_net_pct": ["小单净流入_占比", "小单净流入净占比"],
}


logger = logging.getLogger(__name__)


class EastmoneyController:
    """东方财富资金流向与融资融券数据抓取与入库。"""

    def __init__(self, mysql_config: Optional[dict] = None) -> None:
        self.mysql_config = mysql_config or DEFAULT_MYSQL_CONFIG

    def fetch_fund_flow(self, stock_code: str) -> pd.DataFrame:
        url = f"https://data.eastmoney.com/zjlx/{stock_code}.html"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        try:
            tables = pd.read_html(StringIO(response.text), attrs={"id": "table_ls"})
        except ValueError:
            tables = []
        if tables:
            raw_df = tables[0]
            normalized_df = self._normalize_columns(raw_df)
            return self._standardize_dataframe(normalized_df)

        api_df = self._fetch_fund_flow_from_api(stock_code)
        if api_df.empty:
            raise ValueError(f"未找到资金流向数据表: {url}")
        return self._standardize_dataframe(api_df)

    def sync_stock(self, stock_code: str) -> int:
        try:
            fund_df = self.fetch_fund_flow(stock_code)
        except Exception as e:
            logger.error("同步股票 %s 失败: %s", stock_code, e)
            return 0

        if fund_df.empty:
            return 0
        last_date = self._get_latest_trade_date(stock_code)
        if last_date:
            fund_df = fund_df[fund_df["trade_date"] > last_date]
        if fund_df.empty:
            return 0
        rows = [self._row_from_series(stock_code, row) for _, row in fund_df.iterrows()]
        self._upsert_rows(rows)
        return len(rows)

    def sync_batch(self, stock_codes: Iterable[str]) -> int:
        total_inserted = 0
        for stock_code in stock_codes:
            inserted = self.sync_stock(stock_code)
            if inserted > 0:
                logger.info("股票 %s 新增 %d 条记录", stock_code, inserted)
            total_inserted += inserted
        return total_inserted

    def sync_existing_stocks(self) -> int:
        stock_codes = self._get_existing_stock_codes()
        return self.sync_batch(stock_codes)

    # --- Margin Trading Methods ---

    def sync_margin_trading(self, stock_code: str) -> int:
        """同步单只股票的融资融券数据"""
        try:
            df = self.fetch_margin_trading(stock_code)
        except Exception as e:
            logger.error("同步股票 %s 融资融券失败: %s", stock_code, e)
            return 0

        if df.empty:
            return 0
        
        last_date = self._get_latest_rzrq_date(stock_code)
        if last_date:
            df = df[df["trade_date"] > last_date]
        
        if df.empty:
            return 0
            
        rows = [self._rzrq_row_from_series(stock_code, row) for _, row in df.iterrows()]
        self._upsert_rzrq_rows(rows)
        return len(rows)

    def sync_batch_margin_trading(self, stock_codes: Iterable[str]) -> int:
        """批量同步融资融券数据"""
        total_inserted = 0
        for stock_code in stock_codes:
            inserted = self.sync_margin_trading(stock_code)
            if inserted > 0:
                logger.info("股票 %s 融资融券新增 %d 条记录", stock_code, inserted)
            total_inserted += inserted
        return total_inserted

    def fetch_margin_trading(self, stock_code: str) -> pd.DataFrame:
        """从API获取融资融券数据 (15字段)"""
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPTA_WEB_RZRQ_GGMX",
            "columns": "ALL",
            "source": "WEB",
            "sortColumns": "DATE",
            "sortTypes": "1",
            "p": 1,
            "ps": 5000,
            "filter": f'(SCODE="{stock_code}")'
        }
        
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        result = data.get("result")
        if not result or not result.get("data"):
            return pd.DataFrame()
            
        df = pd.DataFrame(result["data"])
        
        col_map = {
            "DATE": "trade_date",
            "SPJ": "close_price",
            "ZDF": "change_pct",
            "RZYE": "rzye",
            "RZYEZB": "rzye_ratio",
            "RZMRE": "rzmre",
            "RZCHE": "rzche",
            "RZJME": "rzjme",
            "RQYE": "rqye",
            "RQYL": "rqyl",
            "RQMCL": "rqmcl",
            "RQCHL": "rqchl",
            "RQJMG": "rqjmg",
            "RZRQYE": "rzrqye",
            "RZRQYECZ": "rzrqye_diff",
        }
        df = df.rename(columns=col_map)
        df["trade_date"] = df["trade_date"].apply(self._parse_date)
        
        numeric_cols = [
            "close_price", "change_pct", "rzye", "rzye_ratio", "rzmre", "rzche", "rzjme",
            "rqye", "rqyl", "rqmcl", "rqchl", "rqjmg", "rzrqye", "rzrqye_diff"
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        return df.sort_values("trade_date")

    def _get_latest_rzrq_date(self, stock_code: str) -> Optional[date]:
        sql = "SELECT MAX(trade_date) FROM em_individual_margin_trading WHERE stock_code = %s"
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (stock_code,))
                result = cursor.fetchone()
        if not result or not result[0]:
            return None
        if isinstance(result[0], datetime):
            return result[0].date()
        if isinstance(result[0], str):
            try:
                text = result[0].split(" ")[0] if " " in result[0] else result[0]
                return datetime.strptime(text, "%Y-%m-%d").date()
            except ValueError:
                return None 
        return result[0]

    def _upsert_rzrq_rows(self, rows: Iterable[tuple]) -> None:
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(MARGIN_TRADING_SQL, list(rows))
            conn.commit()

    def _rzrq_row_from_series(self, stock_code: str, row: pd.Series) -> tuple:
        return (
            stock_code,
            row["trade_date"],
            row.get("close_price"),
            row.get("change_pct"),
            row.get("rzye"),
            row.get("rzye_ratio"),
            row.get("rzmre"),
            row.get("rzche"),
            row.get("rzjme"),
            row.get("rqye"),
            row.get("rqyl"),
            row.get("rqmcl"),
            row.get("rqchl"),
            row.get("rqjmg"),
            row.get("rzrqye"),
            row.get("rzrqye_diff"),
        )

    def run_daily_after_close(
        self,
        stock_codes: Optional[List[str]] = None,
        close_hour: int = 15,
        close_minute: int = 30,
    ) -> None:
        target_str = "现有库存股票" if stock_codes is None else f"指定 {len(stock_codes)} 只股票"
        logger.info("进入每日收盘后调度模式 (%s)，目标时间 %02d:%02d", target_str, close_hour, close_minute)
        
        while True:
            now = datetime.now()
            target_time = datetime.combine(
                now.date(),
                datetime.min.time().replace(hour=close_hour, minute=close_minute),
            )
            if now >= target_time:
                target_time += timedelta(days=1)

            sleep_seconds = max((target_time - now).total_seconds(), 0)
            logger.info("下一次执行时间: %s (等待 %.0f 秒)", target_time, sleep_seconds)
            time.sleep(sleep_seconds)

            target_date = target_time.date() - timedelta(days=1)
            
            if stock_codes:
                # 1. Fund Flow
                inserted_flow = self.sync_batch(stock_codes)
                logger.info("资金流向: 新增 %d 条", inserted_flow)
                
                # 2. Margin Trading
                inserted_rzrq = self.sync_batch_margin_trading(stock_codes)
                logger.info("融资融券: 新增 %d 条", inserted_rzrq)
            else:
                inserted_flow = self.sync_existing_stocks()
                logger.info("资金流向(现有): 新增 %d 条", inserted_flow)
                
                codes = self._get_existing_stock_codes()
                inserted_rzrq = self.sync_batch_margin_trading(codes)
                logger.info("融资融券(现有): 新增 %d 条", inserted_rzrq)
            
            logger.info("本次每日任务全部完成")

    def _get_existing_stock_codes(self) -> List[str]:
        sql = "SELECT DISTINCT stock_code FROM em_individual_fund_flow"
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
        return [str(row[0]).zfill(6) for row in rows]

    def _get_latest_trade_date(self, stock_code: str) -> Optional[date]:
        sql = "SELECT MAX(trade_date) FROM em_individual_fund_flow WHERE stock_code = %s"
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (stock_code,))
                result = cursor.fetchone()
        if not result or not result[0]:
            return None
        if isinstance(result[0], datetime):
            return result[0].date()
        if isinstance(result[0], str):
            try:
                text = result[0].split(" ")[0] if " " in result[0] else result[0]
                return datetime.strptime(text, "%Y-%m-%d").date()
            except ValueError:
                return datetime.strptime(result[0], "%Y-%m-%d").date()
        return result[0]

    def _upsert_rows(self, rows: Iterable[tuple]) -> None:
        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(FUND_FLOW_SQL, list(rows))
            conn.commit()

    def _normalize_columns(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        columns = []
        for col in dataframe.columns:
            if isinstance(col, tuple):
                top, sub = col
                sub = "" if sub.startswith("Unnamed") else sub
                if sub:
                    columns.append(f"{top}_{sub}")
                else:
                    columns.append(str(top))
            else:
                columns.append(str(col))
        dataframe = dataframe.copy()
        dataframe.columns = columns
        return dataframe

    def _standardize_dataframe(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        dataframe = dataframe.copy()
        logger.debug("原始表头: %s", list(dataframe.columns))
        rename_map = {}
        for target, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in dataframe.columns:
                    rename_map[alias] = target
                    break
        logger.debug("字段映射: %s", rename_map)
        dataframe = dataframe.rename(columns=rename_map)
        logger.debug("映射后表头: %s", list(dataframe.columns))
        required_columns = list(COLUMN_ALIASES.keys())
        missing = [name for name in required_columns if name not in dataframe.columns]
        if missing:
            if len(dataframe.columns) == len(required_columns):
                logger.debug("按列顺序回退映射字段: %s", required_columns)
                dataframe = dataframe.copy()
                dataframe.columns = required_columns
            else:
                raise ValueError(f"缺少字段 {missing}，请检查东方财富表结构变更")

        dataframe["trade_date"] = dataframe["trade_date"].apply(self._parse_date)
        dataframe["close_price"] = dataframe["close_price"].apply(self._parse_number)
        dataframe["change_pct"] = dataframe["change_pct"].apply(self._normalize_percent)

        for column in (
            "main_net_amount",
            "super_large_net_amount",
            "large_net_amount",
            "medium_net_amount",
            "small_net_amount",
        ):
            dataframe[column] = dataframe[column].apply(self._parse_number)

        for column in (
            "main_net_pct",
            "super_large_net_pct",
            "large_net_pct",
            "medium_net_pct",
            "small_net_pct",
        ):
            dataframe[column] = dataframe[column].apply(self._parse_percent)

        dataframe = dataframe.sort_values("trade_date")
        return dataframe

    @staticmethod
    def _parse_date(value) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if " " in text:
            text = text.split(" ")[0]
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return datetime.strptime(text, "%Y/%m/%d").date()

    @staticmethod
    def _parse_number(value) -> Optional[float]:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        if text in {"", "-", "--"}:
            return None
        multiplier = 1.0
        if text.endswith("亿"):
            multiplier = 1e8
            text = text[:-1]
        elif text.endswith("万"):
            multiplier = 1e4
            text = text[:-1]
        text = text.replace(",", "")
        try:
            return float(text) * multiplier
        except ValueError:
            return None

    @staticmethod
    def _parse_percent(value) -> Optional[float]:
        if value is None or pd.isna(value):
            return None
        text = str(value).strip()
        if text in {"", "-", "--"}:
            return None
        if text.endswith("%"):
            text = text[:-1]
        text = text.replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_percent(value) -> Optional[float]:
        percent = EastmoneyController._parse_percent(value)
        if percent is None:
            return None
        if abs(percent) > 1000:
            for _ in range(3):
                percent /= 100
                if abs(percent) <= 1000:
                    break
        if abs(percent) > 1000:
            return None
        return percent

    def _row_from_series(self, stock_code: str, row: pd.Series) -> tuple:
        return (
            stock_code,
            row["trade_date"],
            row["close_price"],
            row["change_pct"],
            row["main_net_amount"],
            self._pct_to_ratio(row["main_net_pct"]),
            row["super_net_amount"], 
            self._pct_to_ratio(row["super_large_net_pct"]),
            row["large_net_amount"],
            self._pct_to_ratio(row["large_net_pct"]),
            row["medium_net_amount"],
            self._pct_to_ratio(row["medium_net_pct"]),
            row["small_net_amount"],
            self._pct_to_ratio(row["small_net_pct"]),
            row.to_json(force_ascii=False),
        )

    @staticmethod
    def _pct_to_ratio(value: Optional[float]) -> Optional[float]:
        if value is None or pd.isna(value):
            return None
        ratio = float(value)
        if abs(ratio) > 1:
            for _ in range(6):
                ratio /= 100
                if abs(ratio) <= 1:
                    break
        if abs(ratio) > 1:
            return None
        return ratio

    def _fetch_fund_flow_from_api(self, stock_code: str) -> pd.DataFrame:
        market = "1" if stock_code.startswith("6") else "0"
        secid = f"{market}.{stock_code}"
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "secid": secid,
            "klt": 101,
            "lmt": 0,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            return pd.DataFrame()

        rows = [item.split(",") for item in klines]
        columns = [
            "trade_date",
            "main_net_amount",
            "small_net_amount",
            "medium_net_amount",
            "large_net_amount",
            "super_large_net_amount",
            "main_net_pct",
            "small_net_pct",
            "medium_net_pct",
            "large_net_pct",
            "super_large_net_pct",
            "close_price",
            "change_pct",
        ]
        dataframe = pd.DataFrame(rows, columns=columns)
        return dataframe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="东方财富资金流向抓取")
    parser.add_argument("--stock", help="股票代码，例如 301251")
    parser.add_argument("--config", help="配置文件路径，例如 Eastmoney/config/config_1.json")
    parser.add_argument(
        "--mode",
        choices=["once", "schedule"],
        default="schedule",
        help="执行模式: once(指定股票补全)、schedule(每日收盘后)",
    )
    parser.add_argument("--close-hour", type=int, default=15, help="收盘小时")
    parser.add_argument("--close-minute", type=int, default=30, help="收盘分钟")
    return parser.parse_args()


def load_stock_codes_from_config(config_path: str) -> List[str]:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件未找到: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    codes_file = config.get("stock_codes_file") or config.get("excel_file")
    if not codes_file:
        raise ValueError("配置文件中未指定 stock_codes_file")
        
    if not os.path.isabs(codes_file):
        codes_file = os.path.join(os.path.dirname(config_path), codes_file)
        
    if not os.path.exists(codes_file):
        raise FileNotFoundError(f"股票列表文件未找到: {codes_file}")
        
    codes = []
    # Try reading as text first, assuming one code per line
    try:
        with open(codes_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    codes.append(line.split(".")[0])
    except UnicodeDecodeError:
        # Fallback to reading as Excel if binary
        try:
            df = pd.read_excel(codes_file)
            pass
        except Exception:
            pass
            
    return codes


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()
    
    mysql_config = None
    stock_codes = []
    
    if args.config:
        if not os.path.exists(args.config):
             raise FileNotFoundError(f"Config file not found: {args.config}")
        with open(args.config, 'r') as f:
            cfg = json.load(f)
            mysql_config = cfg.get('mysql')
        
        try:
            stock_codes = load_stock_codes_from_config(args.config)
            logger.info("已从配置文件加载 %d 只股票", len(stock_codes))
        except Exception as e:
            logger.error("加载配置文件失败: %s", e)
            return

    controller = EastmoneyController(mysql_config=mysql_config)

    if args.mode == "once":
        if args.stock:
            inserted = controller.sync_stock(args.stock)
            logger.info("股票 %s 资金流向同步完成，新增 %d 条", args.stock, inserted)
        elif stock_codes:
            inserted = controller.sync_batch(stock_codes)
            logger.info("批量同步完成，总新增 %d 条记录", inserted)
        else:
            raise ValueError("mode=once 需要提供 --stock 或 --config")
        return

    # Schedule mode
    controller.run_daily_after_close(
        stock_codes=stock_codes if stock_codes else None,
        close_hour=args.close_hour,
        close_minute=args.close_minute
    )


if __name__ == "__main__":
    main()
