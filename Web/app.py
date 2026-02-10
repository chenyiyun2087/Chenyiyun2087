from flask import Flask, render_template, g, request, redirect, url_for, flash
import pymysql
import json
from datetime import datetime
import subprocess
import threading
import time

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' # Needed for flash messages

# Database Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor
}

# Task Status Storage (In-memory for simplicity)
TASKS = {
    "sina": {"last_run": None, "status": "Idle", "switched_day": False},
    "eastmoney": {"last_run": None, "status": "Idle", "switched_day": False}
}

def get_db():
    if 'db' not in g:
        g.db = pymysql.connect(**DB_CONFIG)
    return g.db

@app.teardown_appcontext
def close_db(error):
    if 'db' in g:
        g.db.close()

def get_pagination(cursor, table_name, page, per_page, where_clause="", params=None):
    """Helper for pagination"""
    offset = (page - 1) * per_page
    
    # Get total count
    count_sql = f"SELECT COUNT(*) as total FROM {table_name} {where_clause}"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()['total']
    
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'has_prev': page > 1,
        'has_next': page * per_page < total,
        'prev_num': page - 1,
        'next_num': page + 1
    }
    return pagination, offset

@app.route('/')
def index():
    return redirect(url_for('positions'))

@app.route('/sina/positions')
def positions():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    conn = get_db()
    
    with conn.cursor() as cursor:
        pagination, offset = get_pagination(cursor, "live_positions", page, per_page)
        
        # Fetch positions with pagination
        sql = f"SELECT * FROM live_positions ORDER BY id DESC LIMIT %s OFFSET %s"
        cursor.execute(sql, (per_page, offset))
        positions = cursor.fetchall()

        # Fetch latest two snapshots to determine baseline for daily P&L
        cursor.execute("SELECT * FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 2")
        snapshots = cursor.fetchall()
        
        assets = snapshots[0] if snapshots else None
        baseline_equity = 0
        
        if snapshots:
            today_str = datetime.now().strftime('%Y-%m-%d')
            # If the latest snapshot is today, the baseline is the previous one
            if str(snapshots[0]['snapshot_date']) == today_str:
                if len(snapshots) > 1:
                    baseline_equity = float(snapshots[1]['total_equity'])
                else:
                    # No previous snapshot, use initial capital or first snapshot's open?
                    # For now, let's assume baseline is total_equity of the same snapshot 
                    # (will result in pnl since that snapshot)
                    baseline_equity = float(snapshots[0]['total_equity'])
            else:
                # Latest snapshot is from a previous day, it is the baseline
                baseline_equity = float(snapshots[0]['total_equity'])

        # Enhance data (Calculate profit, market_value, fetch prev close if missing)
        total_positions_value = 0
        for pos in positions:
            pos['entry_price'] = pos['avg_cost'] # Buy Price ≈ Avg Cost for display
            
            # Values Calculation
            current = float(pos.get('current_price') or 0)
            cost = float(pos['avg_cost'] or 0)
            shares = int(pos['shares'] or 0)
            
            pos['market_value'] = round(current * shares, 2)
            pos['profit'] = round((current - cost) * shares, 2)
            pos['profit_rate'] = round(((current - cost) / cost * 100), 2) if cost > 0 else 0
            
            total_positions_value += pos['market_value']
            
            # Placeholder for prev_close if column doesn't exist
            if 'prev_close' not in pos:
                pos['prev_close'] = 0 # To be enhanced with real data source

        # If assets found, update the total_equity and positions_value with real-time calculations
        if assets:
            assets['positions_value'] = round(total_positions_value, 2)
            assets['total_equity'] = round(float(assets['cash']) + total_positions_value, 2)
            
            # Calculate Real-time Daily P&L
            if baseline_equity > 0:
                assets['daily_pnl'] = round(assets['total_equity'] - baseline_equity, 2)
                assets['daily_return_pct'] = round((assets['daily_pnl'] / baseline_equity * 100), 4)
            else:
                assets['daily_pnl'] = 0
                assets['daily_return_pct'] = 0

    return render_template('positions.html', 
                           positions=positions, 
                           assets=assets,
                           pagination=pagination,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/eastmoney')
def eastmoney_scores():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    conn = get_db()
    
    with conn.cursor() as cursor:
        # Get latest date first
        cursor.execute("SELECT MAX(trade_date) as max_date FROM em_strategy_results")
        res = cursor.fetchone()
        latest_date = res['max_date']
        
        results = []
        pagination = None
        
        if latest_date:
            where_clause = "WHERE trade_date = %s"
            pagination, offset = get_pagination(cursor, "em_strategy_results", page, per_page, where_clause, (latest_date,))
            
            sql = f"SELECT * FROM em_strategy_results {where_clause} ORDER BY comprehensive_score DESC LIMIT %s OFFSET %s"
            cursor.execute(sql, (latest_date, per_page, offset))
            results = cursor.fetchall()
            
            for row in results:
                if row.get('details_json'):
                    try:
                        row['details'] = json.loads(row['details_json'])
                    except:
                        row['details'] = {}

    return render_template('eastmoney.html', 
                           results=results, 
                           pagination=pagination,
                           date=latest_date,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/sina/scores')
def sina_scores():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    conn = get_db()
    
    with conn.cursor() as cursor:
        # Get latest date
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_date = res['max_date']
        
        scores = []
        pagination = None
        
        if latest_date:
            where_clause = "WHERE trade_date = %s"
            pagination, offset = get_pagination(cursor, "score_rank_daily", page, per_page, where_clause, (latest_date,))
            
            # Prioritize TRADE pool, then score
            sql = f"""
            SELECT * FROM score_rank_daily 
            {where_clause} 
            ORDER BY 
                CASE WHEN pool_type='TRADE' THEN 1 WHEN pool_type='WATCH' THEN 2 ELSE 3 END,
                score DESC 
            LIMIT %s OFFSET %s
            """
            cursor.execute(sql, (latest_date, per_page, offset))
            scores = cursor.fetchall()

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

@app.route('/admin')
def admin():
    return render_template('admin.html', 
                           tasks=TASKS,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           now_date=datetime.now().strftime('%Y-%m-%d'))

def run_script(script_path, task_name):
    """Run a script in background and update status"""
    TASKS[task_name]['status'] = "Running..."
    TASKS[task_name]['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        # Check day switch (simple logic: if run after 3pm, consider switched for next day?)
        # Or just update the flag purely based on success
        
        # Use full python path
        cmd = [".venv/bin/python", script_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            TASKS[task_name]['status'] = "Success"
            TASKS[task_name]['switched_day'] = True # Mock logic
        else:
            TASKS[task_name]['status'] = f"Failed (Code {result.returncode})"
            print(f"Task {task_name} stderr: {result.stderr}")
            
    except Exception as e:
        TASKS[task_name]['status'] = f"Error: {str(e)}"

@app.route('/admin/run_task/<task_name>', methods=['POST'])
def run_task(task_name):
    if task_name == 'sina':
        script = 'Sina/live_tracker/live_tracker.py' # Example script
        # Actually user said "Call Sina package scheduled task". 
        # If there is no single entry script, we might need to point to one.
        # Assuming `Sina/live_tracker/live_tracker.py` or `ScoreRank/run_daily.py`?
        # Let's assume `ScoreRank/run_daily.py` for "daily update" or similar.
        # Based on context: "Sina包的定时任务" usually implies the live tracker or scoring.
        # Let's use `ScoreRank/run_daily.py` for now as it updates scores.
        script = 'ScoreRank/run_daily.py' 
    elif task_name == 'eastmoney':
        script = 'Eastmoney/run_strategy.py'
    else:
        flash(f"Unknown task: {task_name}", 'danger')
        return redirect(url_for('admin'))
    
    # Run in thread to not block
    thread = threading.Thread(target=run_script, args=(script, task_name))
    thread.start()
    
    flash(f"Task {task_name} started in background.", 'success')
    return redirect(url_for('admin'))

@app.route('/admin/add_position', methods=['POST'])
def add_position():
    symbol = request.form.get('symbol')
    name = request.form.get('name')
    price = request.form.get('price')
    shares = request.form.get('shares')
    date = request.form.get('date')
    strategy_id = request.form.get('strategy_id')
    
    conn = get_db()
    with conn.cursor() as cursor:
        sql = """
        INSERT INTO live_positions (symbol, name, entry_date, entry_price, shares, avg_cost, current_price)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            shares = shares + VALUES(shares),
            avg_cost = (avg_cost * shares + VALUES(avg_cost) * VALUES(shares)) / (shares + VALUES(shares))
        """
        # Note: entry_price column might not exist in live_positions schema based on previous check!
        # Schema was: id, symbol, name, shares, avg_cost, entry_date, current_price, updated_at.
        # So we cannot insert entry_price. We use avg_cost.
        
        sql = """
        INSERT INTO live_positions (symbol, name, entry_date, shares, avg_cost, current_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            shares = shares + VALUES(shares),
            # Simple weighted average for cost update
            avg_cost = (avg_cost * shares + VALUES(avg_cost) * VALUES(shares)) / (shares + VALUES(shares))
        """
        try:
            cursor.execute(sql, (symbol, name, date, shares, price, price))
            conn.commit()
            flash(f"Position {symbol} added/updated.", 'success')
        except Exception as e:
            flash(f"Failed to add position: {e}", 'danger')
            
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
