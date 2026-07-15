import argparse
import json
import logging
import os
import shutil
import time
import zipfile
from datetime import datetime, timedelta

from BSpointChecker import main as capture_main
from SinaBSDetector import batch_process_images, get_base_dir
import pymysql
from scoreRank.core.db_config import require_pymysql_config


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


def find_config_path(config_name):
    if not os.path.splitext(config_name)[1]:
        candidate = os.path.join(CONFIG_DIR, f"{config_name}.json")
    else:
        candidate = os.path.join(CONFIG_DIR, config_name)

    if os.path.exists(candidate):
        return candidate

    raise FileNotFoundError(f"未找到配置文件: {candidate}")


def load_config(config_name):
    config_path = find_config_path(config_name)
    with open(config_path, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    resolved_name = os.path.splitext(os.path.basename(config_path))[0]
    return resolved_name, data


def resolve_path(value, base_dir):
    if not value:
        return value
    return value if os.path.isabs(value) else os.path.abspath(os.path.join(base_dir, value))


def _normalize_stock_code(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw.startswith(("sh", "sz", "bj")):
        raw = raw[2:]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def load_self_selected_from_db(mysql_config):
    if not mysql_config:
        return []

    sql = """
    SELECT stock_code
    FROM a_share_stock_list
    WHERE is_self_selected = 1
    ORDER BY stock_code
    """
    conn = None
    try:
        conn = pymysql.connect(**mysql_config)
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        normalized = []
        for row in rows:
            if isinstance(row, dict):
                value = row.get("stock_code")
            else:
                value = row[0] if row else None
            code = _normalize_stock_code(value)
            if code:
                normalized.append(code)
        return sorted(set(normalized))
    except Exception as exc:
        logger.warning("从数据库读取自选股失败，将回退Excel: %s", exc)
        return []
    finally:
        if conn:
            conn.close()


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


def run_pipeline(config_name, date_str, config_data, overrides=None):
    overrides = overrides or {}
    excel_file = resolve_path(config_data.get("excel_file", "stock_codes.xlsx"), CONFIG_DIR)
    screenshot_workers = overrides.get("screenshot_workers", config_data.get("screenshot_workers", 20))
    detect_workers = overrides.get("detect_workers", config_data.get("detect_workers", 4))
    skip_capture = overrides.get("skip_capture", config_data.get("skip_capture", False))
    capture_only = bool(overrides.get("capture_only", False))
    analyze_only = bool(overrides.get("analyze_only", False))
    stock_codes = overrides.get("stock_codes")
    base_dir = resolve_path(
        overrides.get("base_dir", config_data.get("base_dir", get_base_dir())),
        os.path.dirname(__file__),
    )
    fallback_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "SinaAppBS"))
    if base_dir != fallback_base_dir and not os.path.isdir(base_dir) and os.path.isdir(fallback_base_dir):
        logger.warning("基础目录不存在，改用默认目录: %s -> %s", base_dir, fallback_base_dir)
        base_dir = fallback_base_dir
    archive_days = overrides.get("archive_days", config_data.get("archive_days", 30))
    mysql_config = overrides.get("mysql_config", config_data.get("mysql"))

    if capture_only and analyze_only:
        logger.error("参数冲突：capture_only 与 analyze_only 不能同时开启")
        return 1
    if capture_only and skip_capture:
        logger.error("参数冲突：capture_only 与 skip_capture 不能同时开启")
        return 1
    if analyze_only:
        skip_capture = True

    def _png_mtime_map(folder: str) -> dict[str, float]:
        if not folder or not os.path.isdir(folder):
            return {}
        out = {}
        for name in os.listdir(folder):
            if not name.lower().endswith(".png"):
                continue
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                out[name] = os.path.getmtime(path)
            except OSError:
                continue
        return out

    # Default stock universe for Sina capture/analyse tasks: self-selected symbols from DB.
    # If DB is temporarily unavailable, fallback to Excel source.
    if not stock_codes:
        stock_codes = load_self_selected_from_db(mysql_config)
        if stock_codes:
            logger.info("默认使用数据库自选股，共 %d 只", len(stock_codes))
        else:
            logger.warning("数据库自选股为空，回退Excel股票列表: %s", excel_file)

    expected_dir = os.path.join(base_dir, config_name, date_str)
    pre_capture_map = _png_mtime_map(expected_dir) if not skip_capture else {}

    if skip_capture:
        save_dir = expected_dir
        if not os.path.isdir(save_dir):
            logger.error("跳过截图时目录不存在: %s", save_dir)
            return 1
        logger.info("跳过截图阶段，使用已存在目录: %s", save_dir)
    else:
        capture_start = time.perf_counter()
        save_dir = capture_main(
            excel_file,
            config_name,
            date_str,
            screenshot_workers,
            stock_codes=stock_codes,
        )
        logger.info("截图阶段耗时: %.2f 秒", time.perf_counter() - capture_start)
        if not save_dir:
            logger.error("截图阶段失败，未生成目录")
            return 1

    post_capture_map = _png_mtime_map(save_dir)
    if skip_capture:
        if not post_capture_map:
            logger.error("跳过截图阶段但目录无可用截图: %s", save_dir)
            return 2
        logger.info("跳过截图阶段，检测到现有截图: %d 张", len(post_capture_map))
    else:
        produced_count = sum(
            1
            for name, mtime in post_capture_map.items()
            if name not in pre_capture_map or mtime > pre_capture_map[name] + 1e-6
        )
        if produced_count == 0:
            logger.error("截图阶段无新增/更新产出，视为失败: %s", save_dir)
            return 2
        logger.info("截图阶段新增/更新产出: %d 张 (目录总数: %d)", produced_count, len(post_capture_map))

    if capture_only:
        logger.info("仅截图模式执行完成: %s/%s", config_name, date_str)
        return 0

    logger.info("开始检测日期文件夹: %s/%s", config_name, date_str)

    detect_start = time.perf_counter()
    batch_process_images(
        date_folder=os.path.join(config_name, date_str),
        base_dir=base_dir,
        max_workers=detect_workers,
        mysql_config=mysql_config,
        stock_codes=stock_codes,
    )
    logger.info("检测阶段耗时: %.2f 秒", time.perf_counter() - detect_start)

    if archive_days > 0:
        archive_start = time.perf_counter()
        archive_old_folders(base_dir, days=archive_days)
        logger.info("归档阶段耗时: %.2f 秒", time.perf_counter() - archive_start)

    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="sina B/S 点位检测调度入口")
    parser.add_argument("config_name", help="配置文件名称（位于Sina/config）")
    parser.add_argument("date", help="日期 (YYYYMMDD)")
    parser.add_argument("--screenshot-workers", type=int, default=3, help="截图线程数")
    parser.add_argument("--detect-workers", type=int, default=5, help="检测线程数")
    parser.add_argument("--skip-capture", action="store_true", help="跳过截图阶段，直接检测已存在图片")
    parser.add_argument("--capture-only", action="store_true", help="仅执行截图阶段，不执行检测")
    parser.add_argument("--analyze-only", action="store_true", help="仅执行检测阶段（自动跳过截图）")
    parser.add_argument("--base-dir", help="截图/检测基础目录，默认使用 SinaAppBS")
    parser.add_argument("--archive-days", type=int, help="归档天数阈值，0表示不归档")
    parser.add_argument("--stock-codes", help="指定股票代码列表，逗号分隔")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    config_name, config_data = load_config(args.config_name)
    overrides = {}
    if args.screenshot_workers is not None:
        overrides["screenshot_workers"] = args.screenshot_workers
    if args.detect_workers is not None:
        overrides["detect_workers"] = args.detect_workers
    if args.skip_capture:
        overrides["skip_capture"] = True
    if args.capture_only:
        overrides["capture_only"] = True
    if args.analyze_only:
        overrides["analyze_only"] = True
    if args.base_dir is not None:
        overrides["base_dir"] = args.base_dir
    if args.archive_days is not None:
        overrides["archive_days"] = args.archive_days
    if args.stock_codes:
        overrides["stock_codes"] = [code.strip() for code in args.stock_codes.split(",") if code.strip()]

    overrides["mysql_config"] = require_pymysql_config(dict_cursor=False)
    overrides["mysql_config"]["autocommit"] = True
    raise SystemExit(
        run_pipeline(
            config_name=config_name,
            date_str=args.date,
            config_data=config_data,
            overrides=overrides,
        )
    )
