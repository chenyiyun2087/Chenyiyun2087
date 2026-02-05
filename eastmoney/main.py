"""东方财富多空扫描任务入口（配置方式参考 Sina/main.py）。"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime


from .long_short_scanner_selenium import save_results_to_mysql, scan_stocks_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def find_config_path(config_name: str) -> str:
    if not os.path.splitext(config_name)[1]:
        candidate = os.path.join(CONFIG_DIR, f"{config_name}.json")
    else:
        candidate = os.path.join(CONFIG_DIR, config_name)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"未找到配置文件: {candidate}")
    return candidate


def load_config(config_name: str) -> tuple[str, dict]:
    config_path = find_config_path(config_name)
    with open(config_path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    resolved_name = os.path.splitext(os.path.basename(config_path))[0]
    return resolved_name, data


def load_stock_codes(config: dict, overrides: list[str] | None = None) -> list[str]:
    if overrides:
        return [str(code).zfill(6) for code in overrides]

    inline_codes = config.get("stock_codes")
    if inline_codes:
        return [str(code).zfill(6) for code in inline_codes]

    excel_file = config.get("excel_file")
    if not excel_file:
        raise ValueError("配置中必须提供 stock_codes 或 excel_file")

    excel_path = excel_file if os.path.isabs(excel_file) else os.path.abspath(os.path.join(CONFIG_DIR, excel_file))
    import pandas as pd

    data = pd.read_excel(excel_path)
    first_col = data.columns[0]
    return [str(code).zfill(6) for code in data[first_col].dropna().tolist()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="东方财富多空扫描")
    parser.add_argument("config_name", help="配置文件名称（位于 eastmoney/config）")
    parser.add_argument("--date", help="交易日期(YYYYMMDD)，默认今天")
    parser.add_argument("--stock-codes", nargs="*", help="覆盖配置中的股票列表")
    parser.add_argument("--max-workers", type=int, default=None, help="并发数")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser.parse_args()


def run_pipeline(config_name: str, config_data: dict, args: argparse.Namespace) -> int:
    trade_date = datetime.strptime(args.date, "%Y%m%d").date() if args.date else datetime.now().date()
    stock_codes = load_stock_codes(config_data, args.stock_codes)
    max_workers = args.max_workers or config_data.get("max_workers", 4)

    logger.info("开始批量扫描，股票数=%d, trade_date=%s", len(stock_codes), trade_date)
    results = scan_stocks_batch(stock_codes, max_workers=max_workers, debug=args.debug)
    success_count = sum(1 for r in results if r.snapshot)
    fail_count = sum(1 for r in results if r.error)
    logger.info("扫描完成：成功=%d, 失败=%d", success_count, fail_count)

    mysql_config = config_data.get("mysql")
    if mysql_config:
        upserted = save_results_to_mysql(results, mysql_config=mysql_config, trade_date=trade_date)
        logger.info("数据库写入完成，upsert=%d", upserted)
    else:
        logger.warning("未配置 mysql，跳过入库")

    return 0 if success_count > 0 else 1


def main() -> None:
    args = parse_args()
    resolved_name, config_data = load_config(args.config_name)
    raise SystemExit(run_pipeline(resolved_name, config_data, args))


if __name__ == "__main__":
    main()
