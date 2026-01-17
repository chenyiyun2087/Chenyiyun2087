import argparse
import logging
import os
import shutil
import zipfile
from datetime import datetime, timedelta

from BSpointChecker import main as capture_main
from SinaBSDetector import batch_process_images, get_base_dir


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def archive_old_folders(base_dir, archive_dir=None, days=30):
    if archive_dir is None:
        archive_dir = os.path.join(base_dir, "archive")

    os.makedirs(archive_dir, exist_ok=True)
    cutoff_date = datetime.now() - timedelta(days=days)

    for name in os.listdir(base_dir):
        if name in {"archive", "result"}:
            continue

        batch_dir = os.path.join(base_dir, name)
        if not os.path.isdir(batch_dir):
            continue

        for date_name in os.listdir(batch_dir):
            folder_path = os.path.join(batch_dir, date_name)
            if not os.path.isdir(folder_path):
                continue

            if not date_name.isdigit() or len(date_name) != 8:
                continue

            try:
                folder_date = datetime.strptime(date_name, "%Y%m%d")
            except ValueError:
                continue

            if folder_date >= cutoff_date:
                continue

            batch_archive_dir = os.path.join(archive_dir, name)
            os.makedirs(batch_archive_dir, exist_ok=True)
            zip_path = os.path.join(batch_archive_dir, f"{date_name}.zip")
            if os.path.exists(zip_path):
                logger.info("归档已存在，跳过: %s", zip_path)
                continue

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(folder_path):
                    for file_name in files:
                        file_path = os.path.join(root, file_name)
                        arcname = os.path.relpath(file_path, start=folder_path)
                        zipf.write(file_path, os.path.join(date_name, arcname))

            shutil.rmtree(folder_path)
            logger.info("已归档并清理文件夹: %s", folder_path)


def run_pipeline(excel_file, screenshot_workers, detect_workers, db_path, archive_days, mysql_config=None):
    save_dir = capture_main(excel_file, screenshot_workers)
    if not save_dir:
        logger.error("截图阶段失败，未生成目录")
        return 1

    base_dir = get_base_dir()
    date_folder = os.path.relpath(save_dir, base_dir)
    logger.info("开始检测日期文件夹: %s", date_folder)

    batch_process_images(
        date_folder=date_folder,
        max_workers=detect_workers,
        db_path=db_path,
        mysql_config=mysql_config,
    )

    if archive_days > 0:
        archive_old_folders(get_base_dir(), days=archive_days)

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Sina B/S 点位检测调度入口")
    parser.add_argument("--excel-file", default="stock_codes.xlsx", help="股票代码Excel文件路径")
    parser.add_argument("--screenshot-workers", type=int, default=20, help="截图线程数")
    parser.add_argument("--detect-workers", type=int, default=4, help="检测线程数")
    parser.add_argument("--db-path", default=os.path.join(os.path.dirname(__file__), "bs_detection.db"), help="数据库路径")
    parser.add_argument("--archive-days", type=int, default=30, help="归档天数阈值，0表示不归档")
    parser.add_argument("--mysql-host", help="MySQL主机地址")
    parser.add_argument("--mysql-port", type=int, default=3306, help="MySQL端口")
    parser.add_argument("--mysql-user", help="MySQL用户名")
    parser.add_argument("--mysql-password", help="MySQL密码")
    parser.add_argument("--mysql-db", help="MySQL数据库名")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    mysql_config = None
    if args.mysql_host and args.mysql_user and args.mysql_db:
        mysql_config = {
            "host": args.mysql_host,
            "port": args.mysql_port,
            "user": args.mysql_user,
            "password": args.mysql_password or "",
            "database": args.mysql_db,
            "charset": "utf8mb4",
            "autocommit": True,
        }
    raise SystemExit(
        run_pipeline(
            excel_file=args.excel_file,
            screenshot_workers=args.screenshot_workers,
            detect_workers=args.detect_workers,
            db_path=args.db_path,
            archive_days=args.archive_days,
            mysql_config=mysql_config,
        )
    )
