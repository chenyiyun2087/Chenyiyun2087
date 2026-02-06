
import requests
import json

def fetch_api_data(stock_code):
    market = "1" if stock_code.startswith("6") else "0"
    secid = f"{market}.{stock_code}"
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": secid,
        "klt": 101,
        "lmt": 5, # Just get last 5 days
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    response = requests.get(url, params=params, timeout=15)
    print("Status Code:", response.status_code)
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        print("Error parsing JSON:", e)
        print(response.text)

if __name__ == "__main__":
    fetch_api_data("000001") # Ping An Bank
