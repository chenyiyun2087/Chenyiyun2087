from flask import Flask, render_template, g, request, redirect, url_for, flash
import sys
import pymysql
import json
from datetime import datetime
import subprocess
import threading
import time

try:
    from web.strategy_playbook import (
        DEFAULT_PARAMS,
        WEIGHTED_PROFILES,
        build_pyramid,
        build_quadrants,
        build_weighted,
        clamp,
        evaluate_m2_presets,
        evaluate_m3_optimizer,
        evaluate_m4_allocation,
        evaluate_m5_rolling,
        evaluate_m6_nav,
        evaluate_m7_rebalance,
    )
except ImportError:
    from strategy_playbook import (  # type: ignore
        DEFAULT_PARAMS,
        WEIGHTED_PROFILES,
        build_pyramid,
        build_quadrants,
        build_weighted,
        clamp,
        evaluate_m2_presets,
        evaluate_m3_optimizer,
        evaluate_m4_allocation,
        evaluate_m5_rolling,
        evaluate_m6_nav,
        evaluate_m7_rebalance,
    )

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
    "sina_m8": {"name": "策略M8 回归落库", "script": "scoreRank/cli/run_m8_cycle.py", "last_run": "Never", "status": "Idle", "switched_day": False},
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


@app.route('/api/prompt_template')
def get_prompt_template():
    """API to get the prompt template content"""
    try:
        with open('Web/templates/prompt.txt', 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        return {"error": str(e)}, 500


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
    
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in [50, 100, 200]: per_page = 50

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
            elif sort_by == 'claude_score':
                order_stmt = f"ORDER BY claude_score {order}"
            else:
                # Default: Prioritize TRADE pool, then score desc
                order_stmt = "ORDER BY CASE WHEN pool_type='TRADE' THEN 1 WHEN pool_type='WATCH' THEN 2 ELSE 3 END, score DESC"

            sql = f"""
            SELECT 
                *, 
                COALESCE(opt_score, 0) as opt_score,
                COALESCE(claude_score, 0) as claude_score 
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
                           per_page=per_page,
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
    
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in [50, 100, 200]: per_page = 50

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
            elif sort_by == 'claude_score':
                order_stmt = f"ORDER BY claude_score {order}"
            else:
                order_stmt = "ORDER BY score DESC"

            sql = f"""
            SELECT 
                *, 
                COALESCE(opt_score, 0) as opt_score,
                COALESCE(claude_score, 0) as claude_score 
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
                           per_page=per_page,
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


@app.route('/sina/strategy')
def sina_strategy():
    return redirect(url_for('sina_strategy_pyramid'))


def _fetch_m1_event_summary(conn):
    """Fetch lightweight M1 research summary from b_event tables."""
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
        has_fact = cursor.fetchone() is not None
        cursor.execute("SHOW TABLES LIKE 'b_event_kpi'")
        has_kpi = cursor.fetchone() is not None
        if not (has_fact and has_kpi):
            return None

        cursor.execute("SELECT MAX(event_date) AS latest_date FROM b_event_fact")
        latest = cursor.fetchone() or {}
        latest_date = latest.get('latest_date')
        if not latest_date:
            return None

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_events,
                SUM(CASE WHEN is_eligible = 1 THEN 1 ELSE 0 END) AS eligible_events,
                SUM(CASE WHEN is_high_risk = 1 THEN 1 ELSE 0 END) AS high_risk_events
            FROM b_event_fact
            WHERE event_date = %s
            """,
            (latest_date,),
        )
        fact_stats = cursor.fetchone() or {}

        cursor.execute(
            """
            SELECT
                AVG(hit_3_10pct) AS hit_3_10pct,
                AVG(hit_5_10pct) AS hit_5_10pct,
                AVG(hit_10_10pct) AS hit_10_10pct,
                AVG(ret_3) AS ret_3,
                AVG(ret_5) AS ret_5,
                AVG(ret_10) AS ret_10
            FROM b_event_kpi k
            JOIN b_event_fact f
              ON f.event_date = k.event_date AND f.symbol = k.symbol
            WHERE k.event_date = %s
              AND f.is_eligible = 1
            """,
            (latest_date,),
        )
        kpi_stats = cursor.fetchone() or {}

    def _p(v):
        return None if v is None else round(float(v) * 100, 2)

    return {
        'latest_date': latest_date,
        'total_events': int(fact_stats.get('total_events') or 0),
        'eligible_events': int(fact_stats.get('eligible_events') or 0),
        'high_risk_events': int(fact_stats.get('high_risk_events') or 0),
        'hit_3_10pct': _p(kpi_stats.get('hit_3_10pct')),
        'hit_5_10pct': _p(kpi_stats.get('hit_5_10pct')),
        'hit_10_10pct': _p(kpi_stats.get('hit_10_10pct')),
        'ret_3': _p(kpi_stats.get('ret_3')),
        'ret_5': _p(kpi_stats.get('ret_5')),
        'ret_10': _p(kpi_stats.get('ret_10')),
    }


def _safe_fetch_strategy_context(conn):
    """Read strategy rows and M1 summary; degrade gracefully when DB is unavailable."""
    if conn is None:
        return None, [], None

    try:
        latest_date, rows = _fetch_latest_bs_scores(conn)
    except Exception as e:
        print(f"Failed to load strategy rows: {e}")
        latest_date, rows = None, []

    try:
        m1_summary = _fetch_m1_event_summary(conn)
    except Exception as e:
        print(f"Failed to load M1 summary: {e}")
        m1_summary = None

    return latest_date, rows, m1_summary



def _fetch_latest_m1_rows(conn):
    """Fetch latest eligible b_event merged rows for M2 evaluation."""
    if conn is None:
        return None, []

    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
        has_fact = cursor.fetchone() is not None
        cursor.execute("SHOW TABLES LIKE 'b_event_kpi'")
        has_kpi = cursor.fetchone() is not None
        if not (has_fact and has_kpi):
            return None, []

        cursor.execute("SELECT MAX(event_date) AS latest_date FROM b_event_fact")
        latest = cursor.fetchone() or {}
        latest_date = latest.get('latest_date')
        if not latest_date:
            return None, []

        cursor.execute(
            """
            SELECT
                f.event_date,
                f.symbol,
                f.name,
                f.score,
                COALESCE(f.opt_score, 0) AS opt_score,
                COALESCE(f.claude_score, 0) AS claude_score,
                COALESCE(f.is_eligible, 0) AS is_eligible,
                k.ret_3,
                k.ret_5,
                k.ret_10,
                k.hit_3_10pct,
                k.hit_5_10pct,
                k.hit_10_10pct
            FROM b_event_fact f
            LEFT JOIN b_event_kpi k
              ON f.event_date = k.event_date AND f.symbol = k.symbol
            WHERE f.event_date = %s
            """,
            (latest_date,),
        )
        rows = cursor.fetchall()

    return latest_date, rows


def _fetch_recent_m1_rows(conn, lookback_dates=20):
    """Fetch merged M1 rows across recent N event dates for rolling validation."""
    if conn is None:
        return []

    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
        has_fact = cursor.fetchone() is not None
        cursor.execute("SHOW TABLES LIKE 'b_event_kpi'")
        has_kpi = cursor.fetchone() is not None
        if not (has_fact and has_kpi):
            return []

        cursor.execute(
            """
            SELECT event_date
            FROM b_event_fact
            GROUP BY event_date
            ORDER BY event_date DESC
            LIMIT %s
            """,
            (int(lookback_dates),),
        )
        ds = [r['event_date'] for r in cursor.fetchall()]
        if not ds:
            return []

        placeholders = ','.join(['%s'] * len(ds))
        sql = f"""
            SELECT
                f.event_date,
                f.symbol,
                f.name,
                f.score,
                COALESCE(f.opt_score, 0) AS opt_score,
                COALESCE(f.claude_score, 0) AS claude_score,
                COALESCE(f.is_eligible, 0) AS is_eligible,
                k.ret_3,
                k.ret_5,
                k.ret_10,
                k.hit_3_10pct,
                k.hit_5_10pct,
                k.hit_10_10pct
            FROM b_event_fact f
            LEFT JOIN b_event_kpi k
              ON f.event_date = k.event_date AND f.symbol = k.symbol
            WHERE f.event_date IN ({placeholders})
        """
        cursor.execute(sql, tuple(ds))
        return cursor.fetchall()


def _fetch_live_positions_snapshot(conn):
    """Fetch current live positions as current portfolio snapshot."""
    if conn is None:
        return [], None

    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'live_positions'")
        has_pos = cursor.fetchone() is not None
        if not has_pos:
            return [], None

        cursor.execute(
            """
            SELECT symbol, name, shares, avg_cost, current_price
            FROM live_positions
            """
        )
        rows = cursor.fetchall()

        positions = []
        positions_mv = 0.0
        for r in rows:
            shares = float(r.get('shares') or 0)
            px = float(r.get('current_price') or r.get('avg_cost') or 0)
            mv = round(shares * px, 2)
            positions_mv += mv
            positions.append(
                {
                    'symbol': str(r.get('symbol') or '').zfill(6),
                    'name': r.get('name'),
                    'market_value': mv,
                }
            )

        total_equity = None
        cursor.execute("SHOW TABLES LIKE 'live_daily_snapshots'")
        has_snap = cursor.fetchone() is not None
        if has_snap:
            cursor.execute("SELECT total_equity FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1")
            row = cursor.fetchone()
            if row and row.get('total_equity') is not None:
                total_equity = float(row.get('total_equity'))

    if total_equity is None:
        total_equity = positions_mv if positions_mv > 0 else None

    return positions, total_equity
def _fetch_latest_bs_scores(conn):
    with conn.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_date = res['max_date']

        rows = []
        if latest_date:
            sql = """
            SELECT
                symbol,
                name,
                score,
                COALESCE(opt_score, 0) as opt_score,
                COALESCE(claude_score, 0) as claude_score,
                pool_type
            FROM score_rank_daily
            WHERE trade_date = %s AND is_bs_candidate = 1
            """
            cursor.execute(sql, (latest_date,))
            rows = cursor.fetchall()
    return latest_date, rows


@app.route('/sina/strategy/pyramid')
def sina_strategy_pyramid():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_pyramid: {e}")
        conn = None
    pyramid_min_score = request.args.get('pyramid_min_score', DEFAULT_PARAMS['pyramid_min_score'], type=float)
    pyramid_top_pct = request.args.get('pyramid_top_pct', DEFAULT_PARAMS['pyramid_top_pct'], type=float)
    pyramid_min_claude = request.args.get('pyramid_min_claude', DEFAULT_PARAMS['pyramid_min_claude'], type=float)

    pyramid_min_score = clamp(pyramid_min_score or DEFAULT_PARAMS['pyramid_min_score'], 0.0, 100.0)
    pyramid_top_pct = clamp(pyramid_top_pct or DEFAULT_PARAMS['pyramid_top_pct'], 0.0, 100.0)
    pyramid_min_claude = clamp(pyramid_min_claude or DEFAULT_PARAMS['pyramid_min_claude'], 0.0, 100.0)

    latest_date, rows, m1_summary = _safe_fetch_strategy_context(conn)

    pyramid = build_pyramid(rows, pyramid_min_score, pyramid_top_pct, pyramid_min_claude)

    return render_template(
        'sina_strategy_pyramid.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        params={
            'pyramid_min_score': pyramid_min_score,
            'pyramid_top_pct': pyramid_top_pct,
            'pyramid_min_claude': pyramid_min_claude,
        },
        total_candidates=len(rows),
        pyramid=pyramid,
        page_title="策略一：金字塔筛选法",
        m1_summary=m1_summary,
    )


@app.route('/sina/strategy/weighted')
def sina_strategy_weighted():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_weighted: {e}")
        conn = None

    weighted_profile = request.args.get('weighted_profile', DEFAULT_PARAMS['weighted_profile'], type=str)
    if weighted_profile not in WEIGHTED_PROFILES:
        weighted_profile = DEFAULT_PARAMS['weighted_profile']

    profile_a, profile_b, profile_c = WEIGHTED_PROFILES[weighted_profile]
    weight_a = request.args.get('weight_a', profile_a, type=float)
    weight_b = request.args.get('weight_b', profile_b, type=float)
    weight_c = request.args.get('weight_c', profile_c, type=float)
    weighted_top_n = request.args.get('weighted_top_n', DEFAULT_PARAMS['weighted_top_n'], type=int)

    weight_a = clamp(weight_a if weight_a is not None else profile_a, 0.0, 1.0)
    weight_b = clamp(weight_b if weight_b is not None else profile_b, 0.0, 1.0)
    weight_c = clamp(weight_c if weight_c is not None else profile_c, 0.0, 1.0)
    weighted_top_n = int(clamp(weighted_top_n or DEFAULT_PARAMS['weighted_top_n'], 1, 200))

    latest_date, rows, m1_summary = _safe_fetch_strategy_context(conn)
    weighted_rank = build_weighted(rows, weight_a, weight_b, weight_c)

    return render_template(
        'sina_strategy_weighted.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        params={
            'weighted_profile': weighted_profile,
            'weight_a': weight_a,
            'weight_b': weight_b,
            'weight_c': weight_c,
            'weighted_top_n': weighted_top_n,
        },
        weights_sum=round(weight_a + weight_b + weight_c, 4),
        total_candidates=len(rows),
        weighted_rank=weighted_rank[:weighted_top_n],
        page_title="策略二：综合加权评分法",
        m1_summary=m1_summary,
    )


@app.route('/sina/strategy/quadrant')
def sina_strategy_quadrant():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_quadrant: {e}")
        conn = None

    quadrant_min_score = request.args.get('quadrant_min_score', DEFAULT_PARAMS['quadrant_min_score'], type=float)
    quadrant_opt_cut = request.args.get('quadrant_opt_cut', DEFAULT_PARAMS['quadrant_opt_cut'], type=float)
    quadrant_claude_cut = request.args.get('quadrant_claude_cut', DEFAULT_PARAMS['quadrant_claude_cut'], type=float)

    quadrant_min_score = clamp(quadrant_min_score or DEFAULT_PARAMS['quadrant_min_score'], 0.0, 100.0)
    quadrant_opt_cut = clamp(quadrant_opt_cut or DEFAULT_PARAMS['quadrant_opt_cut'], 0.0, 10.0)
    quadrant_claude_cut = clamp(quadrant_claude_cut or DEFAULT_PARAMS['quadrant_claude_cut'], 0.0, 100.0)

    latest_date, rows, m1_summary = _safe_fetch_strategy_context(conn)
    quadrants, quadrant_base = build_quadrants(rows, quadrant_min_score, quadrant_opt_cut, quadrant_claude_cut)

    return render_template(
        'sina_strategy_quadrant.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        params={
            'quadrant_min_score': quadrant_min_score,
            'quadrant_opt_cut': quadrant_opt_cut,
            'quadrant_claude_cut': quadrant_claude_cut,
        },
        total_candidates=len(rows),
        quadrants=quadrants,
        quadrant_base_count=len(quadrant_base),
        quadrant_points=quadrant_base,
        page_title="策略三：四象限矩阵法",
        m1_summary=m1_summary,
    )


@app.route('/sina/strategy/m2')
def sina_strategy_m2():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m2: {e}")
        conn = None

    latest_date = None
    rows = []
    try:
        latest_date, _ = _fetch_latest_m1_rows(conn)
        rows = _fetch_recent_m1_rows(conn, lookback_dates=20)
    except Exception as e:
        print(f"Failed to load M2 rows: {e}")

    m2_eval = evaluate_m2_presets(rows)
    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M2 page: {e}")

    return render_template(
        'sina_strategy_m2.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m2_eval=m2_eval,
        page_title="策略M2：预设策略效果回归",
    )


@app.route('/sina/strategy/m3')
def sina_strategy_m3():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m3: {e}")
        conn = None

    latest_date = None
    rows = []
    try:
        latest_date, _ = _fetch_latest_m1_rows(conn)
        rows = _fetch_recent_m1_rows(conn, lookback_dates=20)
    except Exception as e:
        print(f"Failed to load M3 rows: {e}")

    m3_eval = evaluate_m3_optimizer(rows)
    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M3 page: {e}")

    return render_template(
        'sina_strategy_m3.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m3_eval=m3_eval,
        page_title="策略M3：参数优化与冠军方案",
    )


@app.route('/sina/strategy/m4')
def sina_strategy_m4():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m4: {e}")
        conn = None

    max_positions = request.args.get('max_positions', 5, type=int)
    max_positions = int(clamp(max_positions or 5, 1, 20))

    latest_date = None
    rows = []
    try:
        latest_date, rows = _fetch_latest_m1_rows(conn)
    except Exception as e:
        print(f"Failed to load M4 rows: {e}")

    m4_eval = evaluate_m4_allocation(rows, max_positions=max_positions)
    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M4 page: {e}")

    return render_template(
        'sina_strategy_m4.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m4_eval=m4_eval,
        max_positions=max_positions,
        page_title="策略M4：组合落地与仓位建议",
    )


@app.route('/sina/strategy/m5')
def sina_strategy_m5():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m5: {e}")
        conn = None

    window_size = request.args.get('window_size', 5, type=int)
    lookback_dates = request.args.get('lookback_dates', 20, type=int)
    max_positions = request.args.get('max_positions', 5, type=int)

    window_size = int(clamp(window_size or 5, 2, 30))
    lookback_dates = int(clamp(lookback_dates or 20, 5, 120))
    max_positions = int(clamp(max_positions or 5, 1, 20))

    latest_date = None
    rows = []
    try:
        latest_date, _ = _fetch_latest_m1_rows(conn)
        rows = _fetch_recent_m1_rows(conn, lookback_dates=lookback_dates)
    except Exception as e:
        print(f"Failed to load M5 rows: {e}")

    m5_eval = evaluate_m5_rolling(rows, window_size=window_size, max_positions=max_positions)
    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M5 page: {e}")

    return render_template(
        'sina_strategy_m5.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m5_eval=m5_eval,
        params={
            'window_size': window_size,
            'lookback_dates': lookback_dates,
            'max_positions': max_positions,
        },
        page_title="策略M5：滚动窗口稳定性验证",
    )


@app.route('/sina/strategy/m6')
def sina_strategy_m6():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m6: {e}")
        conn = None

    lookback_dates = request.args.get('lookback_dates', 30, type=int)
    max_positions = request.args.get('max_positions', 5, type=int)
    cost_bps = request.args.get('cost_bps', 20, type=float)
    slippage_bps = request.args.get('slippage_bps', 10, type=float)

    lookback_dates = int(clamp(lookback_dates or 30, 5, 240))
    max_positions = int(clamp(max_positions or 5, 1, 20))
    cost_bps = clamp(cost_bps if cost_bps is not None else 20, 0.0, 200.0)
    slippage_bps = clamp(slippage_bps if slippage_bps is not None else 10, 0.0, 200.0)

    latest_date = None
    rows = []
    try:
        latest_date, _ = _fetch_latest_m1_rows(conn)
        rows = _fetch_recent_m1_rows(conn, lookback_dates=lookback_dates)
    except Exception as e:
        print(f"Failed to load M6 rows: {e}")

    m6_eval = evaluate_m6_nav(
        rows,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
        max_positions=max_positions,
    )

    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M6 page: {e}")

    return render_template(
        'sina_strategy_m6.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m6_eval=m6_eval,
        params={
            'lookback_dates': lookback_dates,
            'max_positions': max_positions,
            'cost_bps': cost_bps,
            'slippage_bps': slippage_bps,
        },
        page_title="策略M6：净值回测（成本/滑点）",
    )


@app.route('/sina/strategy/m7')
def sina_strategy_m7():
    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_strategy_m7: {e}")
        conn = None

    max_positions = request.args.get('max_positions', 5, type=int)
    capital = request.args.get('capital', 100000, type=float)
    min_trade_weight = request.args.get('min_trade_weight', 1.0, type=float)

    max_positions = int(clamp(max_positions or 5, 1, 20))
    capital = clamp(capital if capital is not None else 100000, 10000.0, 100000000.0)
    min_trade_weight = clamp(min_trade_weight if min_trade_weight is not None else 1.0, 0.0, 20.0)

    latest_date = None
    rows = []
    try:
        latest_date, rows = _fetch_latest_m1_rows(conn)
    except Exception as e:
        print(f"Failed to load M7 rows: {e}")

    m4_eval = evaluate_m4_allocation(rows, max_positions=max_positions)

    current_positions, total_equity = [], None
    try:
        current_positions, total_equity = _fetch_live_positions_snapshot(conn)
    except Exception as e:
        print(f"Failed to load live positions for M7: {e}")

    capital_used = total_equity if (total_equity and total_equity > 0) else capital
    m7_eval = evaluate_m7_rebalance(
        target_allocations=m4_eval.get('allocations') or [],
        current_positions=current_positions,
        total_capital=capital_used,
        min_trade_weight=min_trade_weight,
        conn=conn
    )

    m1_summary = None
    try:
        m1_summary = _fetch_m1_event_summary(conn) if conn else None
    except Exception as e:
        print(f"Failed to load M1 summary in M7 page: {e}")

    return render_template(
        'sina_strategy_m7.html',
        date=latest_date,
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        m1_summary=m1_summary,
        m4_eval=m4_eval,
        m7_eval=m7_eval,
        params={
            'max_positions': max_positions,
            'capital': capital,
            'capital_used': capital_used,
            'min_trade_weight': min_trade_weight,
        },
        page_title="策略M7：模拟调仓与下单流水",
    )


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
    elif task_name == 'sina_m8':
        script_parts = [script, '--lookback-dates', '60']
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
