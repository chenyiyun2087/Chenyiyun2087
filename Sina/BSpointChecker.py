import concurrent.futures
import logging
import os
import time
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
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


def setup_directories():
    """
    创建SinaAppBS文件夹和当前日期文件夹

    返回:
    str: 当前日期文件夹的路径
    """
    # 获取当前日期
    today = datetime.now().strftime("%Y%m%d")

    # 创建基础目录
    base_dir = "../SinaAppBS"
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        logger.info(f"创建基础目录: {base_dir}")

    # 创建日期目录
    date_dir = os.path.join(base_dir, today)
    if not os.path.exists(date_dir):
        os.makedirs(date_dir)
        logger.info(f"创建日期目录: {date_dir}")

    return date_dir


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

        # 确保股票代码格式正确（6位数字）
        stock_codes = [code.zfill(6) if len(code) < 6 else code[-6:] for code in stock_codes]

        logger.info(f"从Excel文件中读取了{len(stock_codes)}个股票代码")
        return stock_codes

    except Exception as e:
        logger.error(f"读取Excel文件时出错: {e}")
        return []


def capture_bs_point_screenshot(stock_code, save_dir):
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
    screenshot_filename = f"{stock_code}_{datetime.now().strftime('%Y%m%d')}.png"
    screenshot_path = os.path.join(save_dir, screenshot_filename)

    # 设置Chrome选项hh
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # 无头模式
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        # 初始化WebDriver
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

        # 访问新浪财经股票行情页面
        # 判断股票代码是沪市还是深市
        if stock_code.startswith(('6', '9')):
            prefix = 'sh'  # 沪市
        else:
            prefix = 'sz'  # 深市

        url = f"https://finance.sina.com.cn/realstock/company/{prefix}{stock_code}/nc.shtml"
        driver.get(url)
        logger.info(f"访问股票 {stock_code} 行情页面")

        # 等待页面加载完成
        time.sleep(3)

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


def process_stock_codes_parallel(stock_codes, save_dir, max_workers=20):
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
            executor.submit(capture_bs_point_screenshot, code, save_dir): code
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


def main(excel_file, max_workers=20):
    """
    主函数

    参数:
    excel_file (str): Excel文件路径
    max_workers (int): 最大线程数
    """
    # 创建目录
    save_dir = setup_directories()
    logger.info(f"截图将保存在: {save_dir}")

    # 读取股票代码
    stock_codes = read_stock_codes(excel_file)
    if not stock_codes:
        logger.error("未能读取到有效的股票代码，程序退出")
        return

    # 并行处理股票代码
    start_time = time.time()
    results = process_stock_codes_parallel(stock_codes, save_dir, max_workers)
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


if __name__ == "__main__":
    # 设置Excel文件路径
    excel_file = "stock_codes.xlsx"  # 请替换为实际的Excel文件路径

    # 设置最大线程数
    max_workers = 20

    # 执行主函数
    main(excel_file, max_workers)
