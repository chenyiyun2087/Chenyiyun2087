from flask import Flask, render_template, g
import pymysql
import json
from datetime import datetime

app = Flask(__name__)

# Database Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor
}

def get_db():
    if 'db' not in g:
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db

@app.teardown_appcontext
def close_db(error):
    if 'db' in g:
        g.db.close()

@app.route('/')
def dashboard():
    conn = get_db()
    
    # 1. 获取最新持仓 (Sina)
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM live_positions")
        positions = cursor.fetchall()
        
    # 2. 获取最新 Sina 评分结果
    # 先找最新日期
    with conn.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_score_date = res['max_date']
        
        sina_scores = []
        if latest_score_date:
            cursor.execute(f"SELECT * FROM score_rank_daily WHERE trade_date = '{latest_score_date}' ORDER BY score DESC LIMIT 20")
            sina_scores = cursor.fetchall()
            
    # 3. 获取最新 Eastmoney 策略结果
    with conn.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) as max_date FROM em_strategy_results")
        res = cursor.fetchone()
        latest_em_date = res['max_date']
        
        em_results = []
        if latest_em_date:
            cursor.execute(f"SELECT * FROM em_strategy_results WHERE trade_date = '{latest_em_date}' ORDER BY comprehensive_score DESC")
            em_results = cursor.fetchall()
            # Parse JSON details if needed, but handled in template usually or here
            for row in em_results:
                if row.get('details_json'):
                    try:
                        row['details'] = json.loads(row['details_json'])
                    except:
                        row['details'] = {}

    return render_template('dashboard.html', 
                           positions=positions, 
                           sina_scores=sina_scores, 
                           st_date=latest_score_date,
                           em_results=em_results,
                           em_date=latest_em_date)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
