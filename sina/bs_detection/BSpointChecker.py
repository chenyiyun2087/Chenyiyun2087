import concurrent.futures
import glob
import logging
import os
import threading
import time
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("sina_bs_capture.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

_CHROMEDRIVER_LOCK = threading.Lock()
_CHROMEDRIVER_PATH = None


def _ensure_local_no_proxy():
    """Ensure localhost WebDriver traffic bypasses global proxy settings."""
    required = {"localhost", "127.0.0.1", "::1"}
    raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    existing = {x.strip() for x in raw.split(",") if x.strip()}
    merged = existing | required
    value = ",".join(sorted(merged))
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value


def _find_cached_chromedriver():
    """Best-effort lookup for a previously downloaded chromedriver binary."""
    candidates = []

    # Explicit override has highest priority.
    custom = os.environ.get("CHROMEDRIVER_PATH")
    if custom and os.path.isfile(custom) and os.access(custom, os.X_OK):
        return custom

    # webdriver_manager default cache paths on macOS.
    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, ".wdm", "drivers", "chromedriver", "mac64", "*", "chromedriver-mac-arm64", "chromedriver"),
        os.path.join(home, ".wdm", "drivers", "chromedriver", "mac64", "*", "chromedriver-mac-x64", "chromedriver"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                candidates.append(path)

    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def get_chromedriver_path():
    global _CHROMEDRIVER_PATH
    _ensure_local_no_proxy()

    if _CHROMEDRIVER_PATH:
        return _CHROMEDRIVER_PATH

    with _CHROMEDRIVER_LOCK:
        if _CHROMEDRIVER_PATH:
            return _CHROMEDRIVER_PATH

        cached = _find_cached_chromedriver()
        if cached:
            _CHROMEDRIVER_PATH = cached
            logger.info("使用本地缓存ChromeDriver: %s", _CHROMEDRIVER_PATH)
            return _CHROMEDRIVER_PATH

        start_time = time.perf_counter()
        try:
            _CHROMEDRIVER_PATH = ChromeDriverManager().install()
        except Exception as exc:
            raise RuntimeError(
                "ChromeDriver下载失败。请检查代理设置，并确保 NO_PROXY 包含 localhost,127.0.0.1,::1"
            ) from exc
        logger.info("ChromeDriver准备完成: %s (耗时 %.2f 秒)", _CHROMEDRIVER_PATH, time.perf_counter() - start_time)
        return _CHROMEDRIVER_PATH


def get_base_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "SinaAppBS"))


def setup_directories(config_name, date_str):
    """
    创建SinaAppBS文件夹、来源文件夹和当前日期文件夹

    返回:
    str: 当前日期文件夹的路径
    """
    # 创建基础目录
    base_dir = get_base_dir()
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建基础目录: {base_dir}")

    batch_dir = os.path.join(base_dir, config_name)
    if not os.path.exists(batch_dir):
        os.makedirs(batch_dir)
        logger.info(f"创建来源目录: {batch_dir}")

    # 创建日期目录
    date_dir = os.path.join(batch_dir, date_str)
    if not os.path.exists(date_dir):
        os.makedirs(date_dir)
        logger.info(f"创建日期目录: {date_dir}")

    return date_dir


def normalize_stock_codes(stock_codes):
    return [code.zfill(6) if len(code) < 6 else code[-6:] for code in stock_codes]


def read_stock_codes(excel_file):
    """
    从Excel文件中读取股票代码列表

    参数:
    excel_file (str): Excel文件路径

    返回:
    list: 股票代码列表
    """
    try:
        # 读取Excel文件
        df = pd.read_excel(excel_file)

        # 检查是否存在stock_code列
        if 'stock_code' not in df.columns:
            logger.error(f"Excel文件中未找到'stock_code'列")
            return []

        # 提取股票代码列表并转换为字符串
        stock_codes = df['stock_code'].astype(str).tolist()
        stock_codes = normalize_stock_codes(stock_codes)

        logger.info(f"从Excel文件中读取了{len(stock_codes)}个股票代码")
        return stock_codes

    except Exception as e:
        logger.error(f"读取Excel文件时出错: {e}")
        return []


def capture_bs_point_screenshot(stock_code, save_dir, date_str):
    """
    访问新浪财经查询指定股票代码的实时行情，
    切换到B/S点标签页并进行全屏截图

    参数:
    stock_code (str): 股票代码，如"300444"
    save_dir (str): 保存截图的目录

    返回:
    tuple: (股票代码, 是否成功, 截图路径或错误信息)
    """
    # 设置截图文件名
    screenshot_filename = f"{stock_code}_{date_str}.png"
    screenshot_path = os.path.join(save_dir, screenshot_filename)

    # 设置Chrome选项hh
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 无头模式
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.page_load_strategy = "eager"

    driver = None
    try:
        _ensure_local_no_proxy()
        capture_start = time.perf_counter()
        # 初始化WebDriver
        driver = webdriver.Chrome(service=Service(get_chromedriver_path()), options=chrome_options)
        driver.set_page_load_timeout(20)
        driver.set_script_timeout(20)

        # 访问新浪财经股票行情页面
        # 判断股票代码是沪市还是深市
        if stock_code.startswith(('6', '9')):
            prefix = 'sh'  # 沪市
        else:
            prefix = 'sz'  # 深市

        url = f"https://finance.sina.com.cn/realstock/company/{prefix}{stock_code}/nc.shtml"
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning("股票 %s: 页面加载超时，尝试停止加载并继续", stock_code)
            try:
                driver.execute_script("window.stop();")
            except Exception:
                logger.debug("股票 %s: 停止页面加载失败", stock_code)
        logger.info("访问股票 %s 行情页面", stock_code)

        # 等待页面加载完成
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except TimeoutException:
            logger.warning("股票 %s: 页面未完全加载，尝试继续", stock_code)

        if time.perf_counter() - capture_start > 60:
            return (stock_code, False, "截图耗时超过60秒，提前结束")

        # 关闭可能出现的弹窗
        try:
            close_buttons = driver.find_elements(By.XPATH, "//a[contains(@class, 'close') or contains(@title, '关闭')]")
            for button in close_buttons:
                if button.is_displayed():
                    button.click()
                    logger.info(f"股票 {stock_code}: 关闭弹窗")
                    time.sleep(1)
        except Exception as e:
            logger.warning(f"股票 {stock_code}: 关闭弹窗时出现异常: {e}")

        # 切换到B/S点标签页
        try:
            bs_tab = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//a[text()='B/S点']"))
            )
            bs_tab.click()
            logger.info(f"股票 {stock_code}: 切换到B/S点标签页")
            time.sleep(2)
        except Exception as e:
            logger.error(f"股票 {stock_code}: 切换到B/S点标签页时出现异常: {e}")
            return (stock_code, False, f"切换到B/S点标签页失败: {e}")

        if time.perf_counter() - capture_start > 60:
            return (stock_code, False, "截图耗时超过60秒，提前结束")

        # 点击全屏查看按钮
        try:
            # 尝试多种方式定位全屏按钮
            try:
                # 方法1: 通过XPath定位
                fullscreen_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                "//div[contains(@class, 'chart')]//a[contains(@class, 'fullscreen') or contains(@title, '全屏')]"))
                )
                fullscreen_button.click()
                logger.info(f"股票 {stock_code}: 点击全屏查看按钮(XPath)")
            except Exception:
                try:
                    # 方法2: 通过CSS选择器定位
                    fullscreen_button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, ".fullscreen, .full-screen, [title*='全屏']"))
                    )
                    fullscreen_button.click()
                    logger.info(f"股票 {stock_code}: 点击全屏查看按钮(CSS)")
                except Exception:
                    # 方法3: 使用JavaScript点击
                    driver.execute_script(
                        "document.querySelector('.fullscreen, .full-screen, [title*=\"全屏\"]').click();")
                    logger.info(f"股票 {stock_code}: 使用JavaScript点击全屏按钮")

            time.sleep(2)
        except Exception as e:
            logger.warning(f"股票 {stock_code}: 点击全屏查看按钮时出现异常: {e}")
            # 继续执行，即使全屏按钮点击失败也尝试截图

        if time.perf_counter() - capture_start > 60:
            return (stock_code, False, "截图耗时超过60秒，提前结束")

        # 截取全屏图片
        driver.save_screenshot(screenshot_path)
        logger.info(f"股票 {stock_code}: 截图已保存为: {screenshot_path}")

        return (stock_code, True, screenshot_path)

    except Exception as e:
        error_msg = f"处理股票 {stock_code} 时出现异常: {e}"
        logger.error(error_msg)
        return (stock_code, False, error_msg)

    finally:
        # 关闭浏览器
        if driver:
            driver.quit()


def process_stock_codes_parallel(stock_codes, save_dir, date_str, max_workers=20):
    """
    并行处理多个股票代码

    参数:
    stock_codes (list): 股票代码列表
    save_dir (str): 保存截图的目录
    max_workers (int): 最大线程数

    返回:
    dict: 处理结果统计
    """
    results = {
        'total': len(stock_codes),
        'success': 0,
        'failed': 0,
        'failed_codes': []
    }

    logger.info(f"开始并行处理{len(stock_codes)}个股票代码，最大线程数: {max_workers}")

    # 使用线程池并行处理
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_stock = {
            executor.submit(capture_bs_point_screenshot, code, save_dir, date_str): code
            for code in stock_codes
        }

        # 处理结果
        for future in concurrent.futures.as_completed(future_to_stock):
            stock_code = future_to_stock[future]
            try:
                code, success, result = future.result()
                if success:
                    results['success'] += 1
                    logger.info(f"股票 {code} 处理成功: {result}")
                else:
                    results['failed'] += 1
                    results['failed_codes'].append((code, result))
                    logger.error(f"股票 {code} 处理失败: {result}")
            except Exception as e:
                results['failed'] += 1
                results['failed_codes'].append((stock_code, str(e)))
                logger.error(f"获取股票 {stock_code} 的结果时出现异常: {e}")

    return results


def main(excel_file, config_name, date_str=None, max_workers=20, stock_codes=None):
    """
    主函数

    参数:
    excel_file (str): Excel文件路径
    max_workers (int): 最大线程数
    """
    # 创建目录
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    save_dir = setup_directories(config_name, date_str)
    logger.info(f"截图将保存在: {save_dir}")

    if stock_codes:
        stock_codes = normalize_stock_codes([str(code).strip() for code in stock_codes if str(code).strip()])
        logger.info(f"使用传入的股票代码列表: {len(stock_codes)} 个")
    else:
        stock_codes = read_stock_codes(excel_file)
        if not stock_codes:
            logger.error("未能读取到有效的股票代码，程序退出")
            return None

    # 并行处理股票代码
    start_time = time.time()
    results = process_stock_codes_parallel(stock_codes, save_dir, date_str, max_workers)
    end_time = time.time()

    # 输出统计结果
    logger.info(f"处理完成，耗时: {end_time - start_time:.2f}秒")
    logger.info(f"总计: {results['total']} 个股票代码")
    logger.info(f"成功: {results['success']} 个")
    logger.info(f"失败: {results['failed']} 个")

    if results['failed'] > 0:
        logger.info("失败的股票代码:")
        for code, error in results['failed_codes']:
            logger.info(f"  - {code}: {error}")

    return save_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="sina B/S 点截图采集")
    parser.add_argument("--excel-file", default="stock_codes.xlsx", help="股票代码Excel文件路径")
    parser.add_argument("--config-name", default="default", help="配置文件名称")
    parser.add_argument("--date", help="日期 (YYYYMMDD)，默认当天")
    parser.add_argument("--max-workers", type=int, default=20, help="截图线程数")
    parser.add_argument("--stock-codes", help="指定股票代码列表，逗号分隔")
    args = parser.parse_args()

    stock_codes = None
    if args.stock_codes:
        stock_codes = [code.strip() for code in args.stock_codes.split(",") if code.strip()]

    main(args.excel_file, args.config_name, args.date, args.max_workers, stock_codes=stock_codes)
