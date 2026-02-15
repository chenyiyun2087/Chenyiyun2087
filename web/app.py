from flask import Flask, render_template, g, request, redirect, url_for, flash
import sys
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

# Task Status Storage (Loaded from DB on start)
TASKS = {
    "sina_bs": {"name": "sina B/S 扫描", "script": "sina/bs_detection/main.py", "last_run": "Never", "status": "Idle", "switched_day": False},
    "sina_score": {"name": "sina 每日评分", "script": "scoreRank/run_daily.py", "last_run": "Never", "status": "Idle", "switched_day": False},
    "sina_snapshot": {"name": "sina 实盘快照", "script": "sina/live_tracker/live_tracker.py", "last_run": "Never", "status": "Idle", "switched_day": False},
    "eastmoney": {"name": "eastmoney 策略扫描", "script": "eastmoney/run_strategy.py", "last_run": "Never", "status": "Idle", "switched_day": False}
}

def init_tasks():
    """Load task status from database"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM app_task_status")
            rows = cursor.fetchall()
            for row in rows:
                name = row['task_name']
                if name in TASKS:
                    TASKS[name]['last_run'] = row['last_run'].strftime('%Y-%m-%d %H:%M:%S') if row['last_run'] else "Never"
                    TASKS[name]['status'] = row['status'] or "Idle"
                    TASKS[name]['switched_day'] = bool(row['switched_day'])
        conn.close()
    except Exception as e:
        print(f"Failed to load task status: {e}")

# Initialize tasks from DB
init_tasks()

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

@app.template_filter('sina_finance_url')
def sina_finance_url(symbol):
    """Generate sina Finance URL for a stock symbol"""
    if not symbol:
        return "#"
    symbol = str(symbol)
    if symbol.startswith('6'):
        market = 'sh'
    elif symbol.startswith('8') or symbol.startswith('4'):
        market = 'bj' 
    else:
        market = 'sz'
    return f"http://finance.sina.com.cn/realstock/company/{market}{symbol}/nc.shtml"

@app.template_filter('eastmoney_guba_url')
def eastmoney_guba_url(symbol):
    """Generate eastmoney Guba URL for a stock symbol"""
    if not symbol:
        return "#"
    return f"http://guba.eastmoney.com/list,{symbol}.html"


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
    sort_by = request.args.get('sort', 'default')  # default, score, opt_score
    order = request.args.get('order', 'desc').upper()
    if order not in ['ASC', 'DESC']: order = 'DESC'
    
    min_score = request.args.get('min_s', type=float)
    min_opt_score = request.args.get('min_o', type=float)
    
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
            where_clauses = ["trade_date = %s"]
            params = [latest_date]
            
            if min_score is not None:
                where_clauses.append("score >= %s")
                params.append(min_score)
            if min_opt_score is not None:
                where_clauses.append("opt_score >= %s")
                params.append(min_opt_score)
                
            where_stmt = " WHERE " + " AND ".join(where_clauses)
            
            # Count for pagination
            pagination, offset = get_pagination(cursor, "score_rank_daily", page, per_page, where_stmt, tuple(params))
            
            # Build ORDER BY
            if sort_by == 'score':
                order_stmt = f"ORDER BY score {order}"
            elif sort_by == 'opt_score':
                order_stmt = f"ORDER BY opt_score {order}"
            else:
                # Default: Prioritize TRADE pool, then score desc
                order_stmt = "ORDER BY CASE WHEN pool_type='TRADE' THEN 1 WHEN pool_type='WATCH' THEN 2 ELSE 3 END, score DESC"

            sql = f"""
            SELECT 
                *, 
                COALESCE(opt_score, 0) as opt_score 
            FROM score_rank_daily 
            {where_stmt}
            AND (is_bs_candidate = 1)
            {order_stmt}
            LIMIT %s OFFSET %s
            """
            
            final_params = params + [per_page, offset]
            cursor.execute(sql, tuple(final_params))
            scores = cursor.fetchall()

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           sort_by=sort_by,
                           order=order,
                           min_s=min_score,
                           min_o=min_opt_score,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           page_title="sina B点股票评分")

@app.route('/sina/self_selected')
def sina_self_selected():
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'default')
    order = request.args.get('order', 'desc').upper()
    if order not in ['ASC', 'DESC']: order = 'DESC'
    
    min_score = request.args.get('min_s', type=float)
    min_opt_score = request.args.get('min_o', type=float)
    
    per_page = 20
    conn = get_db()
    
    with conn.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_date = res['max_date']
        
        scores = []
        pagination = None
        
        if latest_date:
            where_clauses = ["trade_date = %s", "is_self_selected = 1"]
            params = [latest_date]
            
            if min_score is not None:
                where_clauses.append("score >= %s")
                params.append(min_score)
            if min_opt_score is not None:
                where_clauses.append("opt_score >= %s")
                params.append(min_opt_score)
                
            where_stmt = " WHERE " + " AND ".join(where_clauses)
            
            pagination, offset = get_pagination(cursor, "score_rank_daily", page, per_page, where_stmt, tuple(params))
            
            if sort_by == 'score':
                order_stmt = f"ORDER BY score {order}"
            elif sort_by == 'opt_score':
                order_stmt = f"ORDER BY opt_score {order}"
            else:
                order_stmt = "ORDER BY score DESC"

            sql = f"""
            SELECT 
                *, 
                COALESCE(opt_score, 0) as opt_score 
            FROM score_rank_daily 
            {where_stmt}
            {order_stmt}
            LIMIT %s OFFSET %s
            """
            
            final_params = params + [per_page, offset]
            cursor.execute(sql, tuple(final_params))
            scores = cursor.fetchall()

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           sort_by=sort_by,
                           order=order,
                           min_s=min_score,
                           min_o=min_opt_score,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           page_title="自选股评分")

@app.route('/sina/monitor')
def sina_monitor():
    conn = get_db()
    
    # 1. 默认查询日期（参数或今天）
    query_date = request.args.get('date', datetime.now().strftime('%Y%m%d'))
    
    # 2. 获取当日 B/S 数据
    with conn.cursor() as cursor:
        # 统计概览
        sql_stats = """
            SELECT 
                COUNT(*) as total,
                SUM(has_buy_signal) as buy_count,
                SUM(has_sell_signal) as sell_count,
                MAX(created_at) as last_update
            FROM bs_detection_results 
            WHERE batch_date = %s
        """
        cursor.execute(sql_stats, (query_date,))
        daily_stats = cursor.fetchone()
        
        # 3. 获取明细列表 (只显示有信号的)
        sql_details = """
            SELECT * FROM bs_detection_results 
            WHERE batch_date = %s AND (has_buy_signal=1 OR has_sell_signal=1)
            ORDER BY stock_code
        """
        cursor.execute(sql_details, (query_date,))
        daily_signals = cursor.fetchall()

        # 4. 获取最近发现的买点/卖点 (按发现时间倒序，显示前50条)
        # 这可以帮助用户看到"实时"新发现的信号
        sql_recent = """
            SELECT * FROM bs_detection_results 
            WHERE has_buy_signal=1 OR has_sell_signal=1
            ORDER BY created_at DESC
            LIMIT 50
        """
        cursor.execute(sql_recent)
        recent_signals = cursor.fetchall()
        
        # 5. 为了"默认显示上一次完成的日期"，查找最近有数据的日期
        if daily_stats['total'] == 0:
            cursor.execute("SELECT MAX(batch_date) as last_date FROM bs_detection_results")
            res = cursor.fetchone()
            last_completed_date = res['last_date']
        else:
            last_completed_date = query_date

    return render_template('sina_monitor.html',
                           query_date=query_date,
                           daily_stats=daily_stats,
                           daily_signals=daily_signals,
                           recent_signals=recent_signals,
                           last_completed_date=last_completed_date,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/admin')
def admin():
    return render_template('admin.html', 
                           tasks=TASKS,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           now_date=datetime.now().strftime('%Y-%m-%d'))

def update_task_db(task_name):
    """Save task status to database"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            last_run = TASKS[task_name]['last_run']
            if last_run == "Never":
                last_run_db = None
            else:
                last_run_db = last_run
                
            sql = """
            INSERT INTO app_task_status (task_name, last_run, status, switched_day)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_run = VALUES(last_run),
                status = VALUES(status),
                switched_day = VALUES(switched_day)
            """
            cursor.execute(sql, (task_name, last_run_db, TASKS[task_name]['status'], int(TASKS[task_name]['switched_day'])))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to update task DB: {e}")

def run_script(script_parts, task_name):
    """Run a script in background and update status"""
    TASKS[task_name]['status'] = "Running..."
    TASKS[task_name]['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    update_task_db(task_name)
    
    try:
        # Construct full command
        cmd = [sys.executable] + script_parts
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            TASKS[task_name]['status'] = "Success"
            TASKS[task_name]['switched_day'] = True # Mock logic
        else:
            TASKS[task_name]['status'] = f"Failed (Code {result.returncode})"
            # Log error details
            print(f"Task {task_name} stderr: {result.stderr}")
            TASKS[task_name]['error_log'] = result.stderr[:500] # Keep recent error
            
    except Exception as e:
        TASKS[task_name]['status'] = f"Error: {str(e)}"
    
    update_task_db(task_name)

@app.route('/admin/run_task/<task_name>', methods=['POST'])
def run_task(task_name):
    if task_name not in TASKS:
        flash(f"Unknown task: {task_name}", 'danger')
        return redirect(url_for('admin'))
    
    task_config = TASKS[task_name]
    script = task_config['script']
    
    # Special handling for sina BS scan which needs config and date
    if task_name == 'sina_bs':
        today = datetime.now().strftime('%Y%m%d')
        # We need to split if we want to pass as list, but subprocess likes list for args
        # Actually run_script uses subprocess.run([sys.executable, script])
        # So we better handle args in run_script or here.
        # Let's adjust run_script to handle a list of command parts.
        script_parts = [script, "config_1", today]
    else:
        script_parts = [script]
    
    # Run in thread to not block
    thread = threading.Thread(target=run_script, args=(script_parts, task_name))
    thread.start()
    
    flash(f"Task {TASKS[task_name]['name']} started in background.", 'success')
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
            avg_cost = (avg_cost * shares + VALUES(avg_cost) * VALUES(shares)) / (shares + VALUES(shares)),
            current_price = VALUES(current_price),
            entry_date = VALUES(entry_date)
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
