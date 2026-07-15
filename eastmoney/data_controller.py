import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable, List, Optional

import pymysql
from project_network import configure_chrome_direct_options, enforce_direct_network
from scoreRank.core.db_config import require_pymysql_config

enforce_direct_network()

# --- Data Schemas & Constants ---

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

@dataclass(frozen=True)
class BatchResult:
    stock_code: str
    snapshot: DuokongSnapshot | None = None
    error: str | None = None
    duration_seconds: float = 0.0

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

# --- global logger & thread-local storage ---
logger = logging.getLogger(__name__)
_thread_local = threading.local()

def _get_driver(force_refresh: bool = False):
    """Get or create a thread-local Selenium driver with rotation support."""
    if force_refresh and hasattr(_thread_local, "driver"):
        try:
            _thread_local.driver.quit()
        except Exception:
            pass
        delattr(_thread_local, "driver")

    if not hasattr(_thread_local, "driver"):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # Add realistic User-Agent
        options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        configure_chrome_direct_options(options)
        
        _thread_local.driver = webdriver.Chrome(options=options)
        _thread_local.request_count = 0
    return _thread_local.driver

def _fetch_duokong_snapshot_selenium(
    code: str,
    debug: bool = False,
    force_driver_refresh: bool = False,
) -> tuple[DuokongSnapshot, float]:
    """使用Selenium获取动态加载的多空看盘数据 (Internal)."""
    start_time = time.time()
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException
    except ImportError as exc:
        raise RuntimeError("缺少 selenium 依赖，请先安装: pip install selenium") from exc

    # 1. Randomized Delay to evade detection
    time.sleep(random.uniform(0.5, 1.5))

    # 2. Driver Rotation: Refresh every 50 requests in this thread
    if not hasattr(_thread_local, "request_count"):
        _thread_local.request_count = 0
    
    _thread_local.request_count += 1
    force_refresh = (_thread_local.request_count > 50)
    
    url = f"https://guba.eastmoney.com/list,{code}.html"
    
    try:
        driver = _get_driver(force_refresh=force_refresh or force_driver_refresh)
        # Set Referrer for the request
        driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {"Referer": "https://guba.eastmoney.com/"}})
        
        driver.get(url)
        
        # Try to close popups if they appear
        try:
            # Look for common close buttons or "关闭" text
            popups = driver.find_elements(By.XPATH, "//div[contains(@class, 'popup')]//div[text()='关闭'] | //div[contains(@id, 'popup')]//div[text()='关闭'] | //a[contains(@class, 'close')]")
            for p in popups:
                if p.is_displayed():
                    p.click()
                    logger.info("Closed popup for %s", code)
        except Exception:
            pass

        wait = WebDriverWait(driver, 15) 
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpContainer")))

        red_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpRed")))
        green_elem = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "dkkpGreen")))

        bulls_percent = float(red_elem.text.strip().rstrip("%"))
        bears_percent = float(green_elem.text.strip().rstrip("%"))

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
            try:
                log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "debug_screenshots")
                os.makedirs(log_dir, exist_ok=True)
                screenshot_path = os.path.join(log_dir, f"timeout_{code}_{datetime.now().strftime('%H%M%S')}.png")
                _thread_local.driver.save_screenshot(screenshot_path)
                logger.warning("Timeout for %s, screenshot saved to %s", code, screenshot_path)
            except Exception as e:
                logger.debug("Failed to save debug screenshot: %s", e)
        
        raise RuntimeError(f"Timeout (elapsed: {time.time() - start_time:.2f}s)")
    except Exception as e:
        raise e

def _worker_task(code: str, debug: bool, max_retries: int = 1) -> BatchResult:
    """Worker function for threading."""
    start_time = time.time()
    last_error: Exception | None = None
    retry_budget = max(0, max_retries)
    for attempt in range(retry_budget + 1):
        try:
            snapshot, _ = _fetch_duokong_snapshot_selenium(
                code,
                debug=debug,
                force_driver_refresh=attempt > 0,
            )
            return BatchResult(stock_code=code, snapshot=snapshot, duration_seconds=time.time() - start_time)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Sentiment scan failed for %s on attempt %d/%d: %s",
                code,
                attempt + 1,
                retry_budget + 1,
                exc,
            )
            if attempt < retry_budget:
                time.sleep(min(2.0, 0.5 * (attempt + 1)))

    duration = time.time() - start_time
    return BatchResult(stock_code=code, error=str(last_error or "unknown error"), duration_seconds=duration)


# --- Controller Class ---

class DataController:
    """东方财富多空情绪数据控制器。集成了 Selenium 扫描与数据库操作。"""

    def __init__(self, mysql_config: Optional[dict] = None) -> None:
        self.mysql_config = mysql_config or require_pymysql_config(dict_cursor=False)

    def scan_sentiment(
        self,
        stock_codes: Optional[List[str]] = None,
        max_workers: int = 3,
        task_type: str = "all",
        trade_date: date | None = None,
        retry_attempts: int = 1,
        debug_screenshots: bool = False,
    ) -> dict:
        """执行多空情绪扫描，并保存结果到数据库。"""
        if not stock_codes:
            stock_codes = self.get_all_stock_codes_from_db(task_type)
        
        codes = [str(code).zfill(6) for code in stock_codes if code]
        if not codes:
            return {"total": 0, "success": 0, "saved": 0, "duration": 0, "results": []}

        logger.info(
            "开始扫描 %d 只股票, 并发: %d, 重试: %d",
            len(codes),
            max_workers,
            retry_attempts,
        )
        
        start_time = time.time()
        results: List[BatchResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_worker_task, code, debug_screenshots, retry_attempts): code
                for code in codes
            }
            for future in as_completed(future_map):
                results.append(future.result())
        
        results = sorted(results, key=lambda item: item.stock_code)
        scan_duration = time.time() - start_time
        success_count = sum(1 for res in results if res.snapshot)
        
        # Save to DB
        saved_count = self._save_results_to_mysql(results, trade_date=trade_date)
        
        return {
            "total": len(codes),
            "success": success_count,
            "saved": saved_count,
            "duration": scan_duration,
            "results": results
        }

    def get_all_stock_codes_from_db(self, task_type: str = "all") -> List[str]:
        """从 chenyiyun.a_share_stock_list 获取股票代码。"""
        sql = "SELECT stock_code FROM a_share_stock_list"
        if task_type == "custom":
            sql += " WHERE is_self_selected = 1"

        try:
            with pymysql.connect(**self.mysql_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    rows = cursor.fetchall()
            return [str(row[0]).zfill(6) for row in rows]
        except Exception as e:
            logger.error("从数据库获取股票列表失败: %s", e)
            return []

    def _save_results_to_mysql(self, results: List[BatchResult], trade_date: date | None = None) -> int:
        """Internal helper to save scan results."""
        if not self.mysql_config:
            return 0
        
        trade_date = trade_date or date.today()
        rows = []
        import json
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

        try:
            # Ensure DB/Table initialized
            self._init_db_if_needed()
            with pymysql.connect(**self.mysql_config) as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(MYSQL_UPSERT_SQL, rows)
                conn.commit()
            return len(rows)
        except Exception as e:
            logger.error("保存结果到 MySQL 失败: %s", e)
            return 0

    def _init_db_if_needed(self):
        """Ensure database and table exist."""
        base_config = {k: v for k, v in self.mysql_config.items() if k != "database"}
        database = self.mysql_config.get("database")
        
        with pymysql.connect(**base_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` DEFAULT CHARSET utf8mb4")
            conn.commit()

        with pymysql.connect(**self.mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(MYSQL_CREATE_TABLE_SQL)
            conn.commit()
