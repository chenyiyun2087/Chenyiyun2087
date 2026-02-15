import json
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request


app = Flask(__name__)
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def load_mysql_config():
    config_name = os.environ.get("SINA_CONFIG", "config_1.json")
    config_path = os.path.join(CONFIG_DIR, config_name)
    mysql_config = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as file_handle:
            config_data = json.load(file_handle)
        mysql_config = config_data.get("mysql", {})

    mysql_config = {
        "host": os.environ.get("MYSQL_HOST", mysql_config.get("host", "localhost")),
        "port": int(os.environ.get("MYSQL_PORT", mysql_config.get("port", 3306))),
        "user": os.environ.get("MYSQL_USER", mysql_config.get("user", "root")),
        "password": os.environ.get("MYSQL_PASSWORD", mysql_config.get("password", "")),
        "database": os.environ.get("MYSQL_DB", mysql_config.get("database", "chenyiyun")),
        "charset": "utf8mb4",
    }
    return mysql_config


def fetch_signals(days):
    days = max(1, min(days, 60))
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days - 1)
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    if os.environ.get("SINA_SAMPLE_DATA") == "1":
        sample_dates = [
            (start_date + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(min(days, 5))
        ]
        sample_stocks = ["000001", "000002", "000333", "600519"]
        sample_points = []
        for date in sample_dates:
            for stock in sample_stocks:
                sample_points.append(
                    {
                        "date": date,
                        "stock": stock,
                        "buy": stock.endswith("1"),
                        "sell": stock.endswith("2"),
                    }
                )
        return {
            "dates": sample_dates,
            "stocks": sample_stocks,
            "points": sample_points,
            "start_date": sample_dates[0],
            "end_date": sample_dates[-1],
        }

    mysql_config = load_mysql_config()
    import pymysql
    query = (
        "SELECT batch_date, stock_code, has_buy_signal, has_sell_signal "
        "FROM bs_detection_results "
        "WHERE batch_date BETWEEN %s AND %s "
        "ORDER BY batch_date, stock_code"
    )

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (start_str, end_str))
            rows = cursor.fetchall()

    dates = []
    stocks = []
    points = []
    for batch_date, stock_code, has_buy, has_sell in rows:
        date_str = str(batch_date)
        if date_str not in dates:
            dates.append(date_str)
        if stock_code not in stocks:
            stocks.append(stock_code)
        points.append(
            {
                "date": date_str,
                "stock": stock_code,
                "buy": bool(has_buy),
                "sell": bool(has_sell),
            }
        )

    return {
        "dates": dates,
        "stocks": stocks,
        "points": points,
        "start_date": start_str,
        "end_date": end_str,
    }


@app.get("/")
def index():
    return render_template("signals.hml")


@app.get("/api/signals")
def api_signals():
    days = int(request.args.get("days", 20))
    try:
        data = fetch_signals(days)
        return jsonify(data)
    except Exception as exc:
        return (
            jsonify(
                {
                    "dates": [],
                    "stocks": [],
                    "points": [],
                    "start_date": "",
                    "end_date": "",
                    "error": str(exc),
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
