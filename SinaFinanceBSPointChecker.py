import os
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


def capture_bs_point_screenshot(stock_code):
    """
    访问新浪财经查询指定股票代码的实时行情，
    切换到B/S点标签页并进行全屏截图

    参数:
    stock_code (str): 股票代码，如"300444"

    返回:
    str: 保存的截图文件路径
    """
    # 设置当前日期作为文件名的一部分
    today = datetime.now().strftime("%Y%m%d")
    screenshot_filename = f"{stock_code}_{today}.png"

    # 设置Chrome选项
    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")  # 启动时最大化窗口
    chrome_options.add_argument("--disable-notifications")  # 禁用通知

    # 初始化WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    try:
        # 访问新浪财经股票行情页面
        url = f"https://finance.sina.com.cn/realstock/company/sz{stock_code}/nc.shtml"
        driver.get(url)
        print(f"访问股票 {stock_code} 行情页面")

        # 等待页面加载完成
        time.sleep(3)

        # 关闭可能出现的弹窗
        try:
            close_buttons = driver.find_elements(By.XPATH, "//a[contains(@class, 'close') or contains(@title, '关闭')]")
            for button in close_buttons:
                if button.is_displayed():
                    button.click()
                    print("关闭弹窗")
                    time.sleep(1)
        except Exception as e:
            print(f"关闭弹窗时出现异常: {e}")

        # 切换到B/S点标签页
        try:
            bs_tab = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//a[text()='B/S点']"))
            )
            bs_tab.click()
            print("切换到B/S点标签页")
            time.sleep(2)
        except Exception as e:
            print(f"切换到B/S点标签页时出现异常: {e}")

        # 点击全屏查看按钮
        try:
            fullscreen_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH,
                                            "//div[contains(@class, 'kke_cfg_fullscreen')]//a[contains(@class, 'fullscreen') or contains(@title, '全屏查看')]"))
            )
            fullscreen_button.click()
            print("点击全屏查看按钮")
            time.sleep(2)
        except Exception as e:
            print(f"点击全屏查看按钮时出现异常: {e}")
            # 尝试使用JavaScript点击全屏按钮
            try:
                driver.execute_script("document.querySelector('.kke_cfg_fullscreen').click();")
                print("使用JavaScript点击全屏按钮")
                time.sleep(2)
            except Exception as js_e:
                print(f"使用JavaScript点击全屏按钮时出现异常: {js_e}")

        # 截取全屏图片
        driver.save_screenshot(screenshot_filename)
        print(f"截图已保存为: {screenshot_filename}")

        # 获取当前工作目录的绝对路径
        abs_path = os.path.abspath(screenshot_filename)
        return abs_path

    finally:
        # 关闭浏览器
        driver.quit()


if __name__ == "__main__":
    # 设置要查询的股票代码
    stock_code = "300444"  # 双杰电气

    # 执行截图操作
    screenshot_path = capture_bs_point_screenshot(stock_code)
    print(f"任务完成，截图保存在: {screenshot_path}")
