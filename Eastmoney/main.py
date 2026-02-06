"""Eastmoney 多空扫描入口 (Multi-Short Scanner CLI)。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, date

# Add project root to sys.path to allow imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Eastmoney.long_short_scanner_selenium import scan_stocks_batch, save_results_to_mysql

logger = logging.getLogger("Eastmoney.main")


def load_stock_codes(file_path: str) -> list[str]:
    """从文件加载股票代码。"""
    if not os.path.exists(file_path):
        # 尝试相对于 config 目录查找
        config_dir = os.path.join(CURRENT_DIR, "config")
        alt_path = os.path.join(config_dir, os.path.basename(file_path))
        if os.path.exists(alt_path):
            file_path = alt_path
        else:
            raise FileNotFoundError(f"股票列表文件未找到: {file_path}")

    codes = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                # 兼容 "688158" 或 "688158.SH" 格式
                code = line.split(".")[0]
                codes.append(code)
    return codes


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
    parser.add_argument("--stock", help="指定单只股票代码 (覆盖配置文件)")
    parser.add_argument("--stock-codes", nargs="+", help="指定多只股票代码 (覆盖配置文件)")
    parser.add_argument("--max-workers", type=int, default=3, help="并发数量")
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=["scan", "fund_flow", "margin_trading"],
        default=["scan", "fund_flow", "margin_trading"],
        help="执行步骤 (默认全部)",
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

    # 3. 确定股票列表
    stock_codes = []
    if args.stock:
        stock_codes = [args.stock]
    elif args.stock_codes:
        stock_codes = args.stock_codes
    else:
        # 从配置文件读取股票列表文件
        codes_file = config.get("stock_codes_file")
        if not codes_file:
            # 兼容旧配置字段
            codes_file = config.get("excel_file")

        if not codes_file:
            logger.error("配置文件中未指定 stock_codes_file")
            sys.exit(1)
        
        # 处理相对路径，配置文件中的路径通常是相对于 Eastmoney/config 的
        # 或者我们假设它就在 config 目录下
        if not os.path.isabs(codes_file):
             # 尝试相对于 config_path 所在目录
             codes_file = os.path.join(os.path.dirname(config_path), codes_file)
        
        try:
            stock_codes = load_stock_codes(codes_file)
        except Exception as e:
            logger.error("加载股票列表失败: %s", e)
            sys.exit(1)

    logger.info("日期: %s, 股票数量: %d, 并发: %d, 步骤: %s", trade_date, len(stock_codes), args.max_workers, args.steps)
    
    # Track stats
    stats = {
        "scan": {"time": 0.0, "status": "Skipped"},
        "fund_flow": {"time": 0.0, "status": "Skipped"},
        "margin_trading": {"time": 0.0, "status": "Skipped"},
        "total_time": 0.0
    }
    
    global_start_time = time.time()
    
    # 4. 执行扫描 (Sentiment Scan)
    if "scan" in args.steps:
        print("\n" + "=" * 60)
        logger.info("Step 1: Starting Multi-Short Sentiment Scan...")
        scan_start_time = time.time()
        results = scan_stocks_batch(stock_codes, max_workers=args.max_workers)
        scan_duration = time.time() - scan_start_time
        stats["scan"]["time"] = scan_duration
        
        # 5. 打印结果
        print("-" * 60)
        print(f"{'Code':<10} | {'Bull %':<10} | {'Bear %':<10} | {'Result':<10} | {'Time(s)':<8}")
        print("-" * 60)
        
        scan_success_count = 0
        failed_codes = []
        
        for res in results:
            duration_str = f"{res.duration_seconds:.2f}"
            if res.snapshot:
                scan_success_count += 1
                bull = f"{res.snapshot.bulls_percent}%"
                bear = f"{res.snapshot.bears_percent}%"
                print(f"{res.stock_code:<10} | {bull:<10} | {bear:<10} | {'OK':<10} | {duration_str:<8}")
            else:
                failed_codes.append(res.stock_code)
                error_msg = (res.error[:8] + "..") if res.error and len(res.error) > 10 else (res.error or "Error")
                print(f"{res.stock_code:<10} | {'-':<10} | {'-':<10} | {error_msg:<10} | {duration_str:<8}")
                
        print("-" * 60)
        stats["scan"]["status"] = f"{scan_success_count}/{len(stock_codes)} OK"

        if failed_codes:
            print("!!! 部分股票扫描失败，可使用以下命令重试: !!!")
            retry_codes_str = " ".join(failed_codes)
            cmd = f"python -m Eastmoney.main {args.config_file} {args.trade_date} --stock-codes {retry_codes_str}"
            print(cmd)
            print("-" * 60)

        # 6. 入库 (Sentiment)
        mysql_config = config.get("mysql")
        if mysql_config:
            try:
                inserted = save_results_to_mysql(results, mysql_config, trade_date)
                logger.info("Sentiment Data Saved: %d records", inserted)
            except Exception as e:
                logger.error("Sentiment Save Failed: %s", e)
        else:
            logger.warning("MySQL not configured, skipping save.")
    
    # Initialize controller if needed for subsequent steps
    if "fund_flow" in args.steps or "margin_trading" in args.steps:
        # Prepare Controller
        controller = None
        mysql_config = config.get("mysql") # Reload in case skipped scan block
        try:
            from Eastmoney.EastmoneyController import EastmoneyController
            controller = EastmoneyController(mysql_config=mysql_config)
        except ImportError:
            logger.error("Cannot import EastmoneyController")
        except Exception as e:
            logger.error("Controller Init Failed: %s", e)

        if controller and stock_codes:
            # 7. 资金流向数据同步 (Fund Flow Sync)
            if "fund_flow" in args.steps:
                print("\n" + "-" * 60)
                logger.info("Step 2: Syncing Fund Flow Data...")
                ff_start = time.time()
                try:
                    ff_inserted = controller.sync_batch(stock_codes)
                    stats["fund_flow"]["status"] = f"{ff_inserted} records"
                except Exception as e:
                    logger.error("Fund Flow Sync Failed: %s", e)
                    stats["fund_flow"]["status"] = "Failed"
                stats["fund_flow"]["time"] = time.time() - ff_start

            # 8. 融资融券数据同步 (Margin Trading Sync)
            if "margin_trading" in args.steps:
                print("\n" + "-" * 60)
                logger.info("Step 3: Syncing Margin Trading Data (RZRQ)...")
                rzrq_start = time.time()
                try:
                    rzrq_inserted = controller.sync_batch_margin_trading(stock_codes)
                    stats["margin_trading"]["status"] = f"{rzrq_inserted} records"
                except Exception as e:
                    logger.error("RZRQ Sync Failed: %s", e)
                    stats["margin_trading"]["status"] = "Failed"
                stats["margin_trading"]["time"] = time.time() - rzrq_start

    total_time = time.time() - global_start_time
    stats["total_time"] = total_time

    # 9. Final Execution Summary
    print("\n" + "=" * 60)
    print("Execution Summary")
    print("-" * 60)
    print(f"{'Step':<20} | {'Time (s)':<10} | {'Status'}")
    print("-" * 60)
    print(f"{'Sentiment Scan':<20} | {stats['scan']['time']:<10.2f} | {stats['scan']['status']}")
    print(f"{'Fund Flow Sync':<20} | {stats['fund_flow']['time']:<10.2f} | {stats['fund_flow']['status']}")
    print(f"{'Margin Trading':<20} | {stats['margin_trading']['time']:<10.2f} | {stats['margin_trading']['status']}")
    print("-" * 60)
    print(f"Total Execution Time  : {total_time:.2f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import time  # Ensure time is imported if not already at top
    '''usage:
    1):config_1.json 20260206 --stock-codes 000025 000029 000410 000528 000529 000555 000568 000596 000628 000650 000661 000686 000700 000723 000733 000735 000738 000777 000782 000791 000799 000810 000816 000821 000831 000839 000858 000868 000905 000932 000963 000980 001259 001299 001337 001914 002006 002007 002008 002014 002023 002035 002037 002043 002049 002067 002074 002081 002086 002095 002104 002112 002120 002127 002128 002130 002131 002153 002160 002163 002167 002169 002183 002185 002190 002195 002204 002218 002223 002233 002236 002237 002239 002272 002276 002291 002292 002294 002306 002320 002326 002328 002343 002347 002353 002355 002358 002369 002396 002430 002439 002445 002448 002458 002460 002466 002475 002497 002530 002545 002548 002549 002555 002559 002572 002573 002600 002601 002602 002628 002629 002646 002648 002651 002654 002655 002660 002669 002673 002677 002745 002747 002756 002795 002812 002821 002843 002903 002908 002909 002932 002936 002940 002943 002959 002965 002972 002985 003001 003025 300001 300008 300017 300020 300024 300051 300055 300056 300061 300083 300084 300122 300145 300146 300149 300166 300175 300180 300198 300212 300221 300243 300271 300278 300285 300315 300316 300322 300339 300343 300346 300355 300383 300397 300407 300416 300424 300430 300444 300450 300454 300458 300461 300467 300469 300471 300491 300496 300503 300506 300525 300529 300542 300564 300569 300579 300596 300601 300618 300624 300625 300628 300636 300648 300664 300671 300674 300676 300677 300682 300686 300693 300705 300710 300711 300713 300735 300741 300747 300751 300767 300769 300773 300796 300807 300809 300829 300832 300833 300836 300842 300869 300943 300963 300995 301107 301227 301234 301251 301257 301287 301359 301388 301503 301512 600176 600185 600757 601665 601669
    2):config_1.json 20260206
    '''
    main()
