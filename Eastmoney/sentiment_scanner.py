"""东方财富多空看盘批量扫描（Selenium版本）。"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, List

@dataclass(frozen=True)
class DuokongSnapshot:
    """Structured snapshot of the multi/short sentiment widget."""
    code: str
    bulls_percent: float
    bears_percent: float
    bulls_votes: int | None = None
    bears_votes: int | None = None
    snapshot_time: datetime | None = None
    source_url: str | None = None

logger = logging.getLogger(__name__)

MYSQL_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS em_duokong_sentiment (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(16) NOT NULL,
    bulls_percent DECIMAL(7,4) NOT NULL,
    bears_percent DECIMAL(7,4) NOT NULL,
    bulls_votes INT NULL,
    bears_votes INT NULL,
    source_url VARCHAR(255) NULL,
    raw_json JSON NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_date_code (trade_date, stock_code)
);
"""

MYSQL_UPSERT_SQL = """
INSERT INTO em_duokong_sentiment (
    trade_date,
    stock_code,
    bulls_percent,
    bears_percent,
    bulls_votes,
    bears_votes,
    source_url,
    raw_json,
    created_at,
    updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    bulls_percent = VALUES(bulls_percent),
    bears_percent = VALUES(bears_percent),
    bulls_votes = VALUES(bulls_votes),
    bears_votes = VALUES(bears_votes),
    source_url = VALUES(source_url),
    raw_json = VALUES(raw_json),
    updated_at = CURRENT_TIMESTAMP;
"""

# Thread-local storage for Selenium drivers
_thread_local = threading.local()


def _get_driver():
    """Get or create a thread-local Selenium driver."""
    if not hasattr(_thread_local, "driver"):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # Optimization: keep browser open
        _thread_local.driver = webdriver.Chrome(options=options)
    return _thread_local.driver


def _close_driver():
    """Close the thread-local driver if it exists."""
    if hasattr(_thread_local, "driver"):
        try:
            _thread_local.driver.quit()
        except Exception as e:
            logger.warning("Error closing driver: %s", e)
        finally:
            del _thread_local.driver


@dataclass(frozen=True)
class BatchResult:
    stock_code: str
    snapshot: DuokongSnapshot | None = None
    error: str | None = None
    duration_seconds: float = 0.0


def fetch_duokong_snapshot_selenium(code: str, debug: bool = False) -> tuple[DuokongSnapshot, float]:
    """使用Selenium获取动态加载的多空看盘数据 (Reuse Driver)。"""
    start_time = time.time()
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException, WebDriverException
    except ImportError as exc:
        raise RuntimeError("缺少 selenium 依赖，请先安装: pip install selenium") from exc

    url = f"https://guba.eastmoney.com/list,{code}.html"
    
    try:
        driver = _get_driver()
        driver.get(url)
        
        # Wait for the element
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpContainer")))

        red_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpRed")))
        green_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpGreen")))

        bulls_percent = float(red_elem.text.strip().rstrip("%"))
        bears_percent = float(green_elem.text.strip().rstrip("%"))

        if debug:
            logger.info("%s 看涨=%s%% 看跌=%s%%", code, bulls_percent, bears_percent)

        snapshot = DuokongSnapshot(
            code=code,
            bulls_percent=bulls_percent,
            bears_percent=bears_percent,
            snapshot_time=datetime.now(),
            source_url=url,
        )
        duration = time.time() - start_time
        return snapshot, duration

    except TimeoutException:
        if debug:
            logger.warning("Timeout fetching %s", code)
        # Raise specific error so it can be caught or returned as string in worker
        raise RuntimeError("Timeout")
    except Exception as e:
        if debug:
            logger.error("Error fetching %s: %s", code, e, exc_info=True)
        raise e


def _worker_task(code: str, debug: bool) -> BatchResult:
    """Worker function that handles exceptions and timing."""
    try:
        snapshot, duration = fetch_duokong_snapshot_selenium(code, debug)
        return BatchResult(stock_code=code, snapshot=snapshot, duration_seconds=duration)
    except Exception as exc:
        return BatchResult(stock_code=code, error=str(exc), duration_seconds=0.0)


def scan_stocks_batch(
    stock_codes: Iterable[str],
    max_workers: int = 4,
    debug: bool = False,
) -> List[BatchResult]:
    """并发扫描多只股票。"""
    codes = [str(code).zfill(6) for code in stock_codes if code]
    if not codes:
        return []

    results: List[BatchResult] = []
    
    # We use a custom mechanism to ensure we can close drivers
    # But ThreadPoolExecutor doesn't expose threads easily.
    # We will just run the tasks. Drivers might hang if we don't close them explicitly.
    # To properly close them, we can submit a 'cleanup' task to all threads, 
    # but threads in pool are dynamic.
    # A simple robust way for a script is to let OS clean up, but for long running app it's bad.
    # Here we'll try to be nice: since we don't control the pool threads lifecycle fully,
    # we accept that drivers stay open until the process exits (for this CLI tool it is fine).
    # IF we want to force close, we can set max_workers = len(codes) and shut down? No.
    
    # For this CLI use case, keeping drivers open until script exit is acceptable optimization.
    # However, to avoid zombie processes if run programmatically:
    # We can't easily close thread-local drivers from the main thread.
    
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_worker_task, code, debug): code for code in codes}
        for future in as_completed(future_map):
            results.append(future.result())
            
    return sorted(results, key=lambda item: item.stock_code)


def init_mysql_db(mysql_config: dict) -> None:
    base_config = {k: v for k, v in mysql_config.items() if k != "database"}
    database = mysql_config.get("database")
    if not database:
        raise ValueError("mysql 配置缺少 database")

    import pymysql

    with pymysql.connect(**base_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` DEFAULT CHARSET utf8mb4")
        conn.commit()

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(MYSQL_CREATE_TABLE_SQL)
        conn.commit()


def save_results_to_mysql(results: List[BatchResult], mysql_config: dict, trade_date: date) -> int:
    import json
    rows = []
    for item in results:
        if not item.snapshot:
            continue
        snapshot = item.snapshot
        rows.append(
            (
                trade_date,
                item.stock_code,
                snapshot.bulls_percent,
                snapshot.bears_percent,
                snapshot.bulls_votes,
                snapshot.bears_votes,
                snapshot.source_url,
                json.dumps(asdict(snapshot), ensure_ascii=False, default=str),
            )
        )

    if not rows:
        return 0

    init_mysql_db(mysql_config)
    import pymysql

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(MYSQL_UPSERT_SQL, rows)
        conn.commit()
    return len(rows)
