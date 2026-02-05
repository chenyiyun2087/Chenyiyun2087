"""Fetch Eastmoney Guba page HTML with Selenium."""

from __future__ import annotations

import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait


def fetch_html_with_selenium(url: str, timeout: int = 20) -> str:
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.page_load_strategy = "eager"

    driver = webdriver.Chrome(service=Service(), options=chrome_options)
    try:
        driver.get(url)
        try:
            WebDriverWait(driver, timeout).until(lambda d: "多空看盘" in d.page_source)
        except TimeoutException:
            time.sleep(2)
        return driver.page_source
    finally:
        driver.quit()
