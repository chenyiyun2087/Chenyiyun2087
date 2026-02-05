"""东方财富股吧多空看盘扫描器 - Selenium版本

解决JavaScript动态加载问题
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import datetime as dt
import json
import time
import re


@dataclass(frozen=True)
class DuokongSnapshot:
    """多空看盘数据快照"""
    code: str
    bulls_percent: float
    bears_percent: float
    bulls_votes: int | None = None
    bears_votes: int | None = None
    snapshot_time: dt.datetime | None = None
    source_url: str | None = None


def _build_url(code: str) -> str:
    """构建股吧URL"""
    return f"https://guba.eastmoney.com/list,{code}.html"


def fetch_duokong_snapshot_selenium(code: str, debug: bool = False) -> DuokongSnapshot:
    """使用Selenium获取动态加载的多空看盘数据
    
    需要安装: pip install selenium --break-system-packages
    需要下载: ChromeDriver 或 GeckoDriver
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        raise RuntimeError(
            "需要安装Selenium库:\n"
            "pip install selenium --break-system-packages\n\n"
            "并下载对应的浏览器驱动:\n"
            "Chrome: https://chromedriver.chromium.org/\n"
            "Firefox: https://github.com/mozilla/geckodriver/releases"
        )
    
    url = _build_url(code)
    if debug:
        print(f"正在访问: {url}")
    
    # 配置Chrome选项
    chrome_options = Options()
    chrome_options.add_argument('--headless')  # 无头模式
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(url)
        
        if debug:
            print("等待页面加载...")
        
        # 等待多空看盘容器出现
        wait = WebDriverWait(driver, 20)
        container = wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "dkkpContainer"))
        )
        
        if debug:
            print("找到多空看盘容器")
        
        # 等待红色和绿色百分比元素加载
        red_elem = wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, "dkkpRed"))
        )
        green_elem = driver.find_element(By.CLASS_NAME, "dkkpGreen")
        
        # 提取百分比
        bulls_text = red_elem.text.strip()
        bears_text = green_elem.text.strip()
        
        if debug:
            print(f"红色元素文本: {bulls_text}")
            print(f"绿色元素文本: {bears_text}")
        
        # 解析百分比
        bulls_percent = float(bulls_text.rstrip('%'))
        bears_percent = float(bears_text.rstrip('%'))
        
        if debug:
            print(f"✓ 成功解析: 看涨 {bulls_percent}%, 看跌 {bears_percent}%")
        
        return DuokongSnapshot(
            code=code,
            bulls_percent=bulls_percent,
            bears_percent=bears_percent,
            bulls_votes=None,
            bears_votes=None,
            snapshot_time=dt.datetime.now(),
            source_url=url,
        )
        
    except Exception as e:
        if debug:
            print(f"错误: {e}")
            if driver:
                print("页面源代码片段:")
                print(driver.page_source[:1000])
        raise ValueError(
            f"无法获取多空看盘数据 (股票代码: {code})。\n"
            f"原因: {str(e)}\n"
            "请检查:\n"
            "1. Selenium和ChromeDriver是否正确安装\n"
            "2. 网络连接是否正常\n"
            "3. 页面结构是否发生变化"
        )
    finally:
        if driver:
            driver.quit()


def main() -> None:
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="扫描东方财富股吧多空看盘数据(Selenium版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
依赖安装:
  pip install selenium --break-system-packages
  
浏览器驱动下载:
  Chrome: https://chromedriver.chromium.org/
  Firefox: https://github.com/mozilla/geckodriver/releases

示例:
  %(prog)s 688158
  %(prog)s 688158 --debug
  %(prog)s 688158 --format table
        """,
    )
    parser.add_argument("code", help="股票代码,例如 688158")
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    parser.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="输出格式 (默认: json)",
    )
    
    args = parser.parse_args()
    
    try:
        snapshot = fetch_duokong_snapshot_selenium(args.code, debug=args.debug)
        
        if args.format == "json":
            print(json.dumps(asdict(snapshot), ensure_ascii=False, default=str, indent=2))
        else:  # table
            print(f"\n{'='*60}")
            print(f"多空看盘 - 股票代码: {snapshot.code}")
            print(f"{'='*60}")
            print(f"看涨比例: {snapshot.bulls_percent:>6.2f}%")
            print(f"看跌比例: {snapshot.bears_percent:>6.2f}%")
            print(f"抓取时间: {snapshot.snapshot_time}")
            print(f"数据来源: {snapshot.source_url}")
            print(f"{'='*60}\n")
    
    except Exception as e:
        print(f"错误: {e}", file=__import__('sys').stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        __import__('sys').exit(1)


if __name__ == "__main__":
    main()
