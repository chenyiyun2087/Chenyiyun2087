"""Eastmoney 多空扫描入口 (Multi-Short Scanner CLI)。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

# Add project root to sys.path to allow imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Eastmoney.data_controller import DataController

logger = logging.getLogger("Eastmoney.main")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Eastmoney 多空看盘批量扫描")
    parser.add_argument("config_file", help="配置文件路径 (例如 config_1.json)")
    parser.add_argument(
        "trade_date",
        nargs="?",
        default=datetime.now().strftime("%Y%m%d"),
        help="交易日期 YYYYMMDD (默认今日)",
    )
    parser.add_argument("--stock", help="指定单只股票代码 (覆盖数据库模式)")
    parser.add_argument("--stock-codes", nargs="+", help="指定多只股票代码 (覆盖数据库模式)")
    parser.add_argument("--max-workers", type=int, default=3, help="并发数量")
    parser.add_argument(
        "--task-type",
        choices=["all", "custom"],
        default="custom",
        help="任务类型: all(全市场), custom(自选股, 默认)",
    )
    
    args = parser.parse_args()

    # 1. 解析日期
    try:
        trade_date = datetime.strptime(args.trade_date, "%Y%m%d").date()
    except ValueError:
        logger.error("日期格式错误 should be YYYYMMDD, got: %s", args.trade_date)
        sys.exit(1)

    # 2. 加载配置
    config_path = args.config_file
    if not os.path.exists(config_path):
        # 尝试在 Eastmoney/config 下查找
        alt_path = os.path.join(CURRENT_DIR, "config", config_path)
        if os.path.exists(alt_path):
            config_path = alt_path
        else:
            logger.error("配置文件未找到: %s", config_path)
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Initialize Controller
    mysql_config = config.get("mysql")
    try:
        controller = DataController(mysql_config=mysql_config)
    except Exception as e:
        logger.error("Controller Init Failed: %s", e)
        sys.exit(1)

    # 3. 确定股票列表
    stock_codes = []
    if args.stock:
        stock_codes = [args.stock]
    elif args.stock_codes:
        stock_codes = args.stock_codes
    else:
        # 默认从数据库根据 task_type 获取
        logger.info("从数据库读取股票 (任务类型: %s)...", args.task_type)
        stock_codes = controller.get_all_stock_codes_from_db(task_type=args.task_type)

    if not stock_codes:
        logger.error("未找到任何股票代码 (数据库为空或连接失败?)")
        sys.exit(1)

    logger.info("日期: %s, 股票数量: %d, 并发: %d, 类型: %s", trade_date, len(stock_codes), args.max_workers, args.task_type)
    
    global_start_time = time.time()
    
    # 4. 执行扫描 (Sentiment Scan)
    print("\n" + "=" * 60)
    logger.info("Starting Multi-Short Sentiment Scan...")
    
    scan_output = controller.scan_sentiment(stock_codes, max_workers=args.max_workers)
    
    # 打印结果表
    print("-" * 60)
    print(f"{'Code':<10} | {'Bull %':<10} | {'Bear %':<10} | {'Result':<10} | {'Time(s)':<8}")
    print("-" * 60)
    
    failed_codes = []
    for res in scan_output["results"]:
        duration_str = f"{res.duration_seconds:.2f}"
        if res.snapshot:
            bull = f"{res.snapshot.bulls_percent}%"
            bear = f"{res.snapshot.bears_percent}%"
            print(f"{res.stock_code:<10} | {bull:<10} | {bear:<10} | {'OK':<10} | {duration_str:<8}")
        else:
            failed_codes.append(res.stock_code)
            error_msg = (res.error[:8] + "..") if res.error and len(res.error) > 10 else (res.error or "Error")
            print(f"{res.stock_code:<10} | {'-':<10} | {'-':<10} | {error_msg:<10} | {duration_str:<8}")
            
    print("-" * 60)

    if failed_codes:
        print("!!! 部分股票扫描失败 !!!")
        print(f"失败数量: {len(failed_codes)}")
        print("-" * 60)

    logger.info("Sentiment Data Saved: %d records", scan_output["saved"])
    
    total_time = time.time() - global_start_time

    # 5. Final Execution Summary
    print("\n" + "=" * 60)
    print("Execution Summary")
    print("-" * 60)
    print(f"Total Stocks          : {len(stock_codes)}")
    print(f"Scanning Success      : {scan_output['success']}")
    print(f"Data Saved            : {scan_output['saved']}")
    print(f"Total Execution Time  : {total_time:.2f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
