import argparse
import json
import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Sequence

import pandas as pd
import pymysql

from AkShareController import (
    fetch_chip_distribution,
    fetch_em_fund_flow,
    fetch_ths_fund_flow,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


DATE_COLUMNS = ("date", "trade_date", "日期", "交易日期")
FUND_FLOW_COLUMN_MAP = {
    "trade_date": ("日期", "trade_date", "date", "交易日期"),
    "net_inflow": ("主力净流入", "净流入", "net_inflow", "主力净额"),
    "super_large_inflow": ("超大单净流入", "超大单净额", "超大单"),
    "large_inflow": ("大单净流入", "大单净额", "大单"),
    "medium_inflow": ("中单净流入", "中单净额", "中单"),
    "small_inflow": ("小单净流入", "小单净额", "小单"),
}
CHIP_COLUMN_MAP = {
    "trade_date": ("日期", "trade_date", "date", "交易日期"),
    "chip_concentration": ("集中度", "筹码集中度", "集中度(%)"),
    "chip_pct_90": ("90集中度", "90%集中度", "90%筹码集中度"),
    "avg_cost": ("平均成本", "平均成本(元)", "平均成本(元/股)", "平均成本"),
}

FUND_FLOW_SQL = """
INSERT INTO a_share_daily_fund_flow (
    stock_code, trade_date, net_inflow, super_large_inflow, large_inflow, medium_inflow, small_inflow
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    super_large_inflow = VALUES(super_large_inflow),
    large_inflow = VALUES(large_inflow),
    medium_inflow = VALUES(medium_inflow),
    small_inflow = VALUES(small_inflow),
    updated_at = CURRENT_TIMESTAMP
"""

CHIP_SQL = """
INSERT INTO a_share_daily_chip (
    stock_code, trade_date, chip_concentration, chip_pct_90, avg_cost, chip_distribution
) VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    chip_concentration = VALUES(chip_concentration),
    chip_pct_90 = VALUES(chip_pct_90),
    avg_cost = VALUES(avg_cost),
    chip_distribution = VALUES(chip_distribution),
    updated_at = CURRENT_TIMESTAMP
"""

STOCK_LIST_SQL = """
INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
VALUES (%s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    stock_name = VALUES(stock_name),
    exchange = VALUES(exchange),
    list_date = VALUES(list_date),
    is_active = VALUES(is_active),
    updated_at = CURRENT_TIMESTAMP
"""


def get_trade_dates(lookback_days: int) -> List[date]:
    import akshare as ak

    trade_df = ak.tool_trade_date_hist_sina()
    if "trade_date" not in trade_df.columns:
        raise ValueError("tool_trade_date_hist_sina 返回数据缺少 trade_date 列")
    trade_dates = pd.to_datetime(trade_df["trade_date"]).dt.date.tolist()
    if lookback_days <= 0:
        return trade_dates
    return trade_dates[-lookback_days:]


def is_trade_day(target: date, trade_dates: Optional[Iterable[date]] = None) -> bool:
    if trade_dates is None:
        trade_dates = get_trade_dates(0)
    return target in set(trade_dates)


def pick_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    for column in candidates:
        if column in df.columns:
            return column
    return None


def parse_trade_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def filter_by_dates(df: pd.DataFrame, targets: Iterable[date]) -> pd.DataFrame:
    for column in DATE_COLUMNS:
        if column in df.columns:
            parsed = pd.to_datetime(df[column], errors="coerce").dt.date
            return df.loc[parsed.isin(set(targets))]
    return df


def normalize_numeric(value):
    if value is None or value == "":
        return None
    return pd.to_numeric(value, errors="coerce")


def derive_exchange(stock_code: str) -> str:
    if stock_code.startswith("6"):
        return "SH"
    if stock_code.startswith(("0", "3")):
        return "SZ"
    if stock_code.startswith(("4", "8")):
        return "BJ"
    return "UNKNOWN"


def ensure_mysql_config(args: argparse.Namespace) -> dict:
    return {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "19871019",
        "database": "chenyiyun",
        "charset": "utf8mb4",
        "autocommit": True,
    }


def upsert_stock_list(cursor, stock_df: pd.DataFrame) -> None:
    if stock_df.empty:
        return
    rows = []
    for _, row in stock_df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        name = row.get("name") or row.get("股票简称") or ""
        exchange = derive_exchange(code)
        rows.append((code, name, exchange, None, True))
    if rows:
        cursor.executemany(STOCK_LIST_SQL, rows)


def prepare_fund_flow_rows(symbol: str, df: pd.DataFrame) -> List[tuple]:
    if df.empty:
        return []
    date_col = pick_column(df, FUND_FLOW_COLUMN_MAP["trade_date"])
    if date_col is None:
        return []
    columns = {
        key: pick_column(df, candidates)
        for key, candidates in FUND_FLOW_COLUMN_MAP.items()
        if key != "trade_date"
    }
    rows = []
    for _, row in df.iterrows():
        trade_date = parse_trade_date(row.get(date_col))
        if trade_date is None:
            continue
        rows.append(
            (
                symbol,
                trade_date,
                normalize_numeric(row.get(columns["net_inflow"])) if columns["net_inflow"] else None,
                normalize_numeric(row.get(columns["super_large_inflow"]))
                if columns["super_large_inflow"]
                else None,
                normalize_numeric(row.get(columns["large_inflow"])) if columns["large_inflow"] else None,
                normalize_numeric(row.get(columns["medium_inflow"])) if columns["medium_inflow"] else None,
                normalize_numeric(row.get(columns["small_inflow"])) if columns["small_inflow"] else None,
            )
        )
    return rows


def prepare_chip_rows(symbol: str, df: pd.DataFrame) -> List[tuple]:
    if df.empty:
        return []
    date_col = pick_column(df, CHIP_COLUMN_MAP["trade_date"])
    if date_col is None:
        return []
    chip_cols = {
        key: pick_column(df, candidates)
        for key, candidates in CHIP_COLUMN_MAP.items()
        if key != "trade_date"
    }
    rows = []
    for trade_date, group in df.groupby(df[date_col]):
        parsed_date = parse_trade_date(trade_date)
        if parsed_date is None:
            continue
        sample = group.iloc[0]
        distribution = group.drop(columns=[date_col], errors="ignore").to_dict(orient="records")
        rows.append(
            (
                symbol,
                parsed_date,
                normalize_numeric(sample.get(chip_cols["chip_concentration"]))
                if chip_cols["chip_concentration"]
                else None,
                normalize_numeric(sample.get(chip_cols["chip_pct_90"])) if chip_cols["chip_pct_90"] else None,
                normalize_numeric(sample.get(chip_cols["avg_cost"])) if chip_cols["avg_cost"] else None,
                json.dumps(distribution, ensure_ascii=False),
            )
        )
    return rows


def fetch_fund_flow(symbol: str) -> pd.DataFrame:
    import akshare as ak

    try:
        return fetch_ths_fund_flow(ak, symbol)
    except Exception as exc:
        logger.warning("THS 资金流获取失败 %s: %s，尝试 EM 数据源", symbol, exc)
        return fetch_em_fund_flow(ak, symbol)


def fetch_chip(symbol: str) -> pd.DataFrame:
    import akshare as ak

    return fetch_chip_distribution(ak, symbol)


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def process_symbols(
    symbols: Sequence[str],
    trade_dates: Iterable[date],
    mysql_config: dict,
    request_sleep_s: float,
    batch_sleep_s: float,
    batch_size: int,
) -> None:
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")
    trade_dates_set = set(trade_dates)
    total_batches = math.ceil(len(symbols) / batch_size) if batch_size else 1
    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            for batch_index, batch in enumerate(chunked(list(symbols), batch_size), start=1):
                logger.info("处理批次 %s/%s，股票数 %s", batch_index, total_batches, len(batch))
                for symbol in batch:
                    fund_df = fetch_fund_flow(symbol)
                    chip_df = fetch_chip(symbol)

                    fund_df = filter_by_dates(fund_df, trade_dates_set)
                    chip_df = filter_by_dates(chip_df, trade_dates_set)

                    fund_rows = prepare_fund_flow_rows(symbol, fund_df)
                    chip_rows = prepare_chip_rows(symbol, chip_df)

                    if fund_rows:
                        cursor.executemany(FUND_FLOW_SQL, fund_rows)
                    if chip_rows:
                        cursor.executemany(CHIP_SQL, chip_rows)

                    if request_sleep_s > 0:
                        time.sleep(request_sleep_s)
                if batch_sleep_s > 0:
                    time.sleep(batch_sleep_s)


def load_stock_list() -> pd.DataFrame:
    import akshare as ak

    return ak.stock_info_a_code_name()


def run_for_dates(
    trade_dates: List[date],
    mysql_config: dict,
    request_sleep_s: float,
    batch_sleep_s: float,
    batch_size: int,
) -> None:
    stock_df = load_stock_list()
    if stock_df.empty:
        logger.warning("未获取到股票列表，跳过执行")
        return
    symbols = stock_df["code"].astype(str).str.zfill(6).tolist()
    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            upsert_stock_list(cursor, stock_df)

    logger.info("开始处理交易日 %s，股票数量 %s", trade_dates, len(symbols))
    process_symbols(
        symbols=symbols,
        trade_dates=trade_dates,
        mysql_config=mysql_config,
        request_sleep_s=request_sleep_s,
        batch_sleep_s=batch_sleep_s,
        batch_size=batch_size,
    )
    logger.info("完成交易日 %s 数据入库", trade_dates)


def backfill_recent_days(
    days: int,
    mysql_config: dict,
    request_sleep_s: float,
    batch_sleep_s: float,
    batch_size: int,
) -> None:
    trade_dates = get_trade_dates(days)
    run_for_dates(
        trade_dates=trade_dates,
        mysql_config=mysql_config,
        request_sleep_s=request_sleep_s,
        batch_sleep_s=batch_sleep_s,
        batch_size=batch_size,
    )


def run_daily_after_close(
    close_hour: int,
    close_minute: int,
    mysql_config: dict,
    request_sleep_s: float,
    batch_sleep_s: float,
    batch_size: int,
) -> None:
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
        trade_dates = get_trade_dates(0)
        if not is_trade_day(target_date, trade_dates):
            logger.info("非交易日 %s，跳过执行", target_date)
            continue

        run_for_dates(
            trade_dates=[target_date],
            mysql_config=mysql_config,
            request_sleep_s=request_sleep_s,
            batch_sleep_s=batch_sleep_s,
            batch_size=batch_size,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Akshare 全量股票数据调度入口")
    parser.add_argument(
        "--mode",
        choices=["once", "backfill", "schedule"],
        default="schedule",
        help="执行模式: once(指定日期)、backfill(补全历史)、schedule(每日收盘后)",
    )
    parser.add_argument("--date", help="执行日期 (YYYY-MM-DD)，仅 mode=once 使用")
    parser.add_argument("--backfill-days", type=int, default=20, help="回补最近交易日数量")
    parser.add_argument("--close-hour", type=int, default=15, help="收盘小时")
    parser.add_argument("--close-minute", type=int, default=30, help="收盘分钟")
    parser.add_argument(
        "--request-sleep",
        type=float,
        default=0.3,
        help="单次请求间隔(秒)，用于满足 Akshare 访问频率限制",
    )
    parser.add_argument(
        "--batch-sleep",
        type=float,
        default=2.0,
        help="批次间隔(秒)，用于降低触发限流风险",
    )
    parser.add_argument("--batch-size", type=int, default=50, help="批量股票数量")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mysql_config = ensure_mysql_config(args)

    if args.mode == "once":
        if not args.date:
            raise ValueError("mode=once 需要提供 --date (YYYY-MM-DD)")
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        run_for_dates(
            trade_dates=[target],
            mysql_config=mysql_config,
            request_sleep_s=args.request_sleep,
            batch_sleep_s=args.batch_sleep,
            batch_size=args.batch_size,
        )
        return

    if args.mode == "backfill":
        backfill_recent_days(
            args.backfill_days,
            mysql_config=mysql_config,
            request_sleep_s=args.request_sleep,
            batch_sleep_s=args.batch_sleep,
            batch_size=args.batch_size,
        )
        return

    run_daily_after_close(
        close_hour=args.close_hour,
        close_minute=args.close_minute,
        mysql_config=mysql_config,
        request_sleep_s=args.request_sleep,
        batch_sleep_s=args.batch_sleep,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
