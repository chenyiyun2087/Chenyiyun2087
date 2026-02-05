"""东方财富多空扫描任务入口（配置方式参考 Sina/main.py）。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime

if __package__:
    from .long_short_scanner_selenium import save_results_to_mysql, scan_stocks_batch
else:
    # 兼容脚本直接运行: python Eastmoney/main.py ...
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    if CURRENT_DIR not in sys.path:
        sys.path.insert(0, CURRENT_DIR)
    from long_short_scanner_selenium import save_results_to_mysql, scan_stocks_batch

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


def _normalize_codes(raw_values) -> list[str]:
    codes = []
    for value in raw_values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.startswith("#"):
            continue
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            continue
        codes.append(digits[-6:].zfill(6))
    return codes


def load_stock_codes(config: dict, overrides: list[str] | None = None) -> list[str]:
    if overrides:
        return _normalize_codes(overrides)

    inline_codes = config.get("stock_codes")
    if inline_codes:
        return _normalize_codes(inline_codes)

    code_file = config.get("stock_codes_file") or config.get("excel_file")
    if not code_file:
        raise ValueError("配置中必须提供 stock_codes 或 stock_codes_file/excel_file")

    code_path = code_file if os.path.isabs(code_file) else os.path.abspath(os.path.join(CONFIG_DIR, code_file))
    extension = os.path.splitext(code_path)[1].lower()

    if extension in {".txt", ".csv"}:
        with open(code_path, "r", encoding="utf-8") as file_handle:
            lines = [line.strip() for line in file_handle]
        if extension == ".csv":
            values = []
            for line in lines:
                if not line:
                    continue
                values.extend([part.strip() for part in line.split(",")])
            return _normalize_codes(values)
        return _normalize_codes(lines)

    import pandas as pd

    data = pd.read_excel(code_path)
    first_col = data.columns[0]
    return _normalize_codes(data[first_col].dropna().tolist())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="东方财富多空扫描")
    parser.add_argument("config_name", help="配置文件名称（位于 Eastmoney/config）")
    parser.add_argument("date", nargs="?", help="交易日期(YYYYMMDD)，兼容旧调用方式")
    parser.add_argument("--date", dest="date_opt", help="交易日期(YYYYMMDD)，默认今天")
    parser.add_argument("--stock", help="单只股票代码，例如 688158")
    parser.add_argument("--stock-codes", nargs="*", help="覆盖配置中的多只股票列表")
    parser.add_argument("--max-workers", type=int, default=None, help="并发数")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    return parser.parse_args()


def run_pipeline(config_data: dict, args: argparse.Namespace) -> int:
    date_text = args.date_opt or args.date
    trade_date = datetime.strptime(date_text, "%Y%m%d").date() if date_text else datetime.now().date()
    override_codes = None
    if args.stock:
        override_codes = [args.stock]
    elif args.stock_codes:
        override_codes = args.stock_codes

    stock_codes = load_stock_codes(config_data, override_codes)
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
    _, config_data = load_config(args.config_name)
    raise SystemExit(run_pipeline(config_data, args))


if __name__ == "__main__":
    main()
