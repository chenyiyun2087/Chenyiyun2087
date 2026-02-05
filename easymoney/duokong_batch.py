"""Batch scanner for Eastmoney duokong (多空看盘) data."""

from __future__ import annotations

import argparse
import json
import logging
import os

import pandas as pd

from .duokong_scanner import DuokongSnapshot, fetch_duokong_snapshot
from .duokong_storage import save_snapshots_to_mysql

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def find_config_path(config_name: str) -> str:
    if not os.path.splitext(config_name)[1]:
        candidate = os.path.join(CONFIG_DIR, f"{config_name}.json")
    else:
        candidate = os.path.join(CONFIG_DIR, config_name)

    if os.path.exists(candidate):
        return candidate

    raise FileNotFoundError(f"未找到配置文件: {candidate}")


def load_config(config_name: str) -> tuple[str, dict]:
    config_path = find_config_path(config_name)
    with open(config_path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    resolved_name = os.path.splitext(os.path.basename(config_path))[0]
    return resolved_name, data


def normalize_stock_codes(stock_codes: list[str]) -> list[str]:
    return [code.zfill(6) if len(code) < 6 else code[-6:] for code in stock_codes]


def read_stock_codes(excel_file: str) -> list[str]:
    try:
        df = pd.read_excel(excel_file)
        if "stock_code" not in df.columns:
            logger.error("Excel文件中未找到 'stock_code' 列")
            return []
        stock_codes = df["stock_code"].astype(str).tolist()
        stock_codes = normalize_stock_codes(stock_codes)
        logger.info("从Excel文件读取到 %d 个股票代码", len(stock_codes))
        return stock_codes
    except Exception as exc:
        logger.error("读取Excel文件失败: %s", exc)
        return []


def run_batch(config_data: dict, overrides: dict | None = None) -> list[DuokongSnapshot]:
    overrides = overrides or {}
    excel_file = overrides.get("excel_file", config_data.get("excel_file", "stock_codes.xlsx"))
    if not os.path.isabs(excel_file):
        excel_file = os.path.abspath(os.path.join(CONFIG_DIR, excel_file))
    stock_codes = overrides.get("stock_codes")
    if not stock_codes:
        stock_codes = read_stock_codes(excel_file)

    snapshots: list[DuokongSnapshot] = []
    for code in stock_codes:
        try:
            snapshot = fetch_duokong_snapshot(code)
        except Exception as exc:
            logger.warning("扫描失败 %s: %s", code, exc)
            continue
        snapshots.append(snapshot)
    return snapshots


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量扫描东方财富多空看盘数据")
    parser.add_argument("config_name", help="配置文件名称（位于 easymoney/config）")
    parser.add_argument("--stock-codes", help="指定股票代码列表，逗号分隔")
    parser.add_argument("--mysql-host", default="localhost", help="MySQL主机地址")
    parser.add_argument("--mysql-port", type=int, default=3306, help="MySQL端口")
    parser.add_argument("--mysql-user", default="root", help="MySQL用户名")
    parser.add_argument("--mysql-password", default="", help="MySQL密码")
    parser.add_argument("--mysql-db", default="chenyiyun", help="MySQL数据库名")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _, config_data = load_config(args.config_name)
    overrides = {}
    if args.stock_codes:
        overrides["stock_codes"] = [code.strip() for code in args.stock_codes.split(",") if code.strip()]

    mysql_config = config_data.get("mysql", {})
    mysql_config.update(
        {
            "host": args.mysql_host or mysql_config.get("host", "localhost"),
            "port": args.mysql_port or mysql_config.get("port", 3306),
            "user": args.mysql_user or mysql_config.get("user", "root"),
            "password": args.mysql_password or mysql_config.get("password", ""),
            "database": args.mysql_db or mysql_config.get("database", "chenyiyun"),
            "charset": "utf8mb4",
            "autocommit": True,
        }
    )

    snapshots = run_batch(config_data, overrides=overrides)
    if not snapshots:
        logger.warning("未获取到多空看盘结果")
        return
    save_snapshots_to_mysql(snapshots, mysql_config)


if __name__ == "__main__":
    main()
