
import requests
import pandas as pd
import json
from io import StringIO

def test_read_html(code):
    url = f"https://data.eastmoney.com/rzrq/stock/{code}.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    print(f"Testing pd.read_html for {url}...")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        tables = pd.read_html(StringIO(response.text))
        if tables:
            print(f"Success! Found {len(tables)} tables.")
            print(tables[0].head())
            return True
    except Exception as e:
        print(f"read_html failed: {e}")
    return False

def test_api(code):
    print(f"\nTesting API for {code}...")
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    # Convert code to potential standard format if needed, but API usually takes raw code
    # Typically 6 digits.
    
    # Try finding the market for filter? usually just SCODE works
    params = {
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "ALL",
        "source": "WEB",
        "sortColumns": "DATE",
        "sortTypes": "-1",
        "p": 1,
        "ps": 50,
        "filter": f'(SCODE="{code}")'
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("result") and data["result"].get("data"):
            print("API Success!")
            df = pd.DataFrame(data["result"]["data"])
            print(df.head())
            print("Columns:", df.columns.tolist())
            return True
        else:
            print("API returned no data:", data)
    except Exception as e:
        print(f"API failed: {e}")
    return False

if __name__ == "__main__":
    code = "301251"
    if not test_read_html(code):
        test_api(code)
    else:
        # even if hml works, API is often better for history. Let's test API too.
        test_api(code)
