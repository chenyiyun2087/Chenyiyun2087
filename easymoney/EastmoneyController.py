import argparse
import logging
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
    super_large_net_amount,
    super_large_net_ratio,
    large_net_amount,
    large_net_ratio,
    medium_net_amount,
    medium_net_ratio,
    small_net_amount,
    small_net_ratio
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    close_price = VALUES(close_price),
    pct_change = VALUES(pct_change),
    main_net_amount = VALUES(main_net_amount),
    main_net_ratio = VALUES(main_net_ratio),
    super_large_net_amount = VALUES(super_large_net_amount),
    super_large_net_ratio = VALUES(super_large_net_ratio),
    large_net_amount = VALUES(large_net_amount),
    large_net_ratio = VALUES(large_net_ratio),
    medium_net_amount = VALUES(medium_net_amount),
    medium_net_ratio = VALUES(medium_net_ratio),
    small_net_amount = VALUES(small_net_amount),
    small_net_ratio = VALUES(small_net_ratio),
    updated_at = CURRENT_TIMESTAMP
"""

COLUMN_ALIASES = {
    "trade_date": ["日期"],
    "close_price": ["收盘价"],
    "pct_change": ["涨跌幅"],
    "main_net_amount": ["主力净流入_净额"],
    "main_net_pct": ["主力净流入_占比"],
    "super_large_net_amount": ["超大单净流入_净额"],
    "super_large_net_pct": ["超大单净流入_占比"],
    "large_net_amount": ["大单净流入_净额"],
    "large_net_pct": ["大单净流入_占比"],
    "medium_net_amount": ["中单净流入_净额"],
    "medium_net_pct": ["中单净流入_占比"],
    "small_net_amount": ["小单净流入_净额"],
    "small_net_pct": ["小单净流入_占比"],
}


logger = logging.getLogger(__name__)


class EastmoneyController:
    """东方财富资金流向数据抓取与入库。"""

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
        fund_df = self.fetch_fund_flow(stock_code)
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

    def sync_existing_stocks(self) -> int:
        stock_codes = self._get_existing_stock_codes()
        inserted = 0
        for stock_code in stock_codes:
            inserted += self.sync_stock(stock_code)
        return inserted

    def run_daily_after_close(self, close_hour: int = 15, close_minute: int = 30) -> None:
        logger.info("进入每日收盘后调度模式，目标时间 %02d:%02d", close_hour, close_minute)
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
            if target_date.weekday() >= 5:
                logger.info("非交易日 %s，跳过执行", target_date)
                continue

            inserted = self.sync_existing_stocks()
            logger.info("本次补充完成，新增 %d 条资金流向记录", inserted)

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
        rename_map = {}
        for target, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in dataframe.columns:
                    rename_map[alias] = target
                    break
        dataframe = dataframe.rename(columns=rename_map)
        for required in COLUMN_ALIASES:
            if required not in dataframe.columns:
                raise ValueError(f"缺少字段 {required}，请检查东方财富表结构变更")

        dataframe["trade_date"] = dataframe["trade_date"].apply(self._parse_date)
        dataframe["close_price"] = dataframe["close_price"].apply(self._parse_number)
        dataframe["pct_change"] = dataframe["pct_change"].apply(self._parse_percent)

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
        if isinstance(value, date):
            return value
        return datetime.strptime(str(value), "%Y-%m-%d").date()

    @staticmethod
    def _parse_number(value) -> Optional[float]:
        if value is None:
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
        if value is None:
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

    def _row_from_series(self, stock_code: str, row: pd.Series) -> tuple:
        return (
            stock_code,
            row["trade_date"],
            row["close_price"],
            row["pct_change"],
            row["main_net_amount"],
            self._pct_to_ratio(row["main_net_pct"]),
            row["super_large_net_amount"],
            self._pct_to_ratio(row["super_large_net_pct"]),
            row["large_net_amount"],
            self._pct_to_ratio(row["large_net_pct"]),
            row["medium_net_amount"],
            self._pct_to_ratio(row["medium_net_pct"]),
            row["small_net_amount"],
            self._pct_to_ratio(row["small_net_pct"]),
        )

    @staticmethod
    def _pct_to_ratio(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return value / 100

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
            "main_net_pct",
            "super_large_net_amount",
            "super_large_net_pct",
            "large_net_amount",
            "large_net_pct",
            "medium_net_amount",
            "medium_net_pct",
            "small_net_amount",
            "small_net_pct",
            "close_price",
            "pct_change",
        ]
        dataframe = pd.DataFrame(rows, columns=columns)
        return dataframe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="东方财富资金流向抓取")
    parser.add_argument("--stock", help="股票代码，例如 301251")
    parser.add_argument(
        "--mode",
        choices=["once", "schedule"],
        default="schedule",
        help="执行模式: once(指定股票补全)、schedule(每日收盘后)",
    )
    parser.add_argument("--close-hour", type=int, default=15, help="收盘小时")
    parser.add_argument("--close-minute", type=int, default=30, help="收盘分钟")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()
    controller = EastmoneyController()

    if args.mode == "once":
        if not args.stock:
            raise ValueError("mode=once 需要提供 --stock")
        inserted = controller.sync_stock(args.stock)
        logger.info("股票 %s 资金流向同步完成，新增 %d 条", args.stock, inserted)
        return

    controller.run_daily_after_close(close_hour=args.close_hour, close_minute=args.close_minute)


if __name__ == "__main__":
    main()
