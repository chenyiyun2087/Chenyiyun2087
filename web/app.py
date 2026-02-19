from flask import Flask, render_template, g, request, redirect, url_for, flash
import sys
import os
import pymysql
import json
from pathlib import Path
from datetime import datetime
import subprocess
import threading
import time
from werkzeug.utils import secure_filename

try:
    from sina.live_tracker.live_tracker import LiveTracker
    LIVE_TRACKER_IMPORT_ERROR = None
except ModuleNotFoundError as e:
    LiveTracker = None  # type: ignore
    LIVE_TRACKER_IMPORT_ERROR = str(e)

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

UPLOAD_BACKTEST_DIR = Path('web/uploads/backtest_results')
BACKTEST_RESULT_DIRS = [
    UPLOAD_BACKTEST_DIR,
    Path('backtest/result'),
    Path('backtest/results'),
    Path('sina/backtest/result'),
]


def _get_backtest_json_files():
    files = []
    for result_dir in BACKTEST_RESULT_DIRS:
        if not result_dir.exists() or not result_dir.is_dir():
            continue
        for path in sorted(result_dir.glob('*.json'), reverse=True):
            files.append(path)
    return files


def _extract_equity_points(payload):
    if isinstance(payload, list):
        source = payload
    elif isinstance(payload, dict):
        timeseries = payload.get('timeseries') if isinstance(payload.get('timeseries'), dict) else {}
        source = (
            payload.get('equity_curve')
            or payload.get('equity')
            or payload.get('nav_curve')
            or payload.get('curve')
            or payload.get('daily_equity')
            or timeseries.get('nav')
            or []
        )
    else:
        source = []

    points = []
    for item in source:
        if isinstance(item, dict):
            date = item.get('date') or item.get('datetime') or item.get('time') or item.get('timestamp')
            value = item.get('equity')
            if value is None:
                value = item.get('nav')
            if value is None:
                value = item.get('value')
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            date, value = item[0], item[1]
        else:
            continue

        if date is None or value is None:
            continue

        try:
            points.append({'date': str(date), 'value': float(value)})
        except (TypeError, ValueError):
            continue

    return points


def _extract_trade_rows(payload):
    if isinstance(payload, dict):
        source = payload.get('trades') or payload.get('transactions') or payload.get('orders') or []
    elif isinstance(payload, list):
        source = payload
    else:
        source = []

    rows = []
    for item in source:
        if not isinstance(item, dict):
            continue

        row = {
            'datetime': item.get('datetime') or item.get('date') or item.get('time') or item.get('timestamp') or item.get('ts') or '-',
            'buy_date': item.get('buy_date') or item.get('entry_date') or '-',
            'symbol': item.get('symbol') or item.get('code') or item.get('stock_code') or '-',
            'side': str(item.get('side') or item.get('action') or item.get('type') or '-').upper(),
            'price': item.get('price') if item.get('price') is not None else item.get('trade_price') or '-',
            'quantity': item.get('quantity') if item.get('quantity') is not None else item.get('qty') if item.get('qty') is not None else item.get('shares') or item.get('volume') or '-',
            'amount': item.get('amount') if item.get('amount') is not None else item.get('trade_value') or '-',
            'reason': item.get('reason') or item.get('note') or '-',
        }
        try:
            row['price_num'] = float(row['price'])
        except (TypeError, ValueError):
            row['price_num'] = 0.0
        try:
            row['quantity_num'] = int(float(row['quantity']))
        except (TypeError, ValueError):
            row['quantity_num'] = 0
        if row['amount'] in (None, '-', ''):
            row['amount'] = round(row['price_num'] * row['quantity_num'], 2) if row['price_num'] and row['quantity_num'] else '-'
        rows.append(row)

    rows.sort(key=lambda x: str(x.get('datetime') or ''))
    return rows


def _build_symbol_performance(trade_rows):
    stats = {}
    for row in trade_rows:
        symbol = row.get('symbol') or '-'
        side = str(row.get('side') or '').upper()
        qty = row.get('quantity_num', 0)
        price = row.get('price_num', 0.0)
        dt = row.get('datetime')

        if symbol not in stats:
            stats[symbol] = {
                'symbol': symbol,
                'buy_qty': 0,
                'sell_qty': 0,
                'buy_amount': 0.0,
                'sell_amount': 0.0,
                'realized_pnl': 0.0,
                'open_qty': 0,
                'avg_cost': 0.0,
                'first_buy_date': '-',
                '_lots': [],
            }

        st = stats[symbol]

        if side == 'BUY' and qty > 0:
            st['buy_qty'] += qty
            st['buy_amount'] += qty * price
            st['open_qty'] += qty
            st['_lots'].append({'qty': qty, 'price': price, 'buy_date': dt})
            if st['first_buy_date'] == '-' and dt:
                st['first_buy_date'] = dt

        elif side == 'SELL' and qty > 0:
            st['sell_qty'] += qty
            st['sell_amount'] += qty * price
            remain = qty
            matched_buy_date = '-'
            while remain > 0 and st['_lots']:
                lot = st['_lots'][0]
                take = min(remain, lot['qty'])
                st['realized_pnl'] += take * (price - lot['price'])
                remain -= take
                lot['qty'] -= take
                if matched_buy_date == '-' and lot.get('buy_date'):
                    matched_buy_date = lot['buy_date']
                if lot['qty'] <= 0:
                    st['_lots'].pop(0)
            st['open_qty'] = max(st['open_qty'] - qty, 0)
            row['buy_date'] = row.get('buy_date') if row.get('buy_date') not in (None, '', '-') else matched_buy_date

    result = []
    for symbol, st in stats.items():
        open_cost = sum(l['qty'] * l['price'] for l in st['_lots'])
        open_qty = sum(l['qty'] for l in st['_lots'])
        st['open_qty'] = open_qty
        st['avg_cost'] = (open_cost / open_qty) if open_qty > 0 else 0.0
        base = st['buy_amount'] if st['buy_amount'] > 0 else 0.0
        st['realized_return_pct'] = (st['realized_pnl'] / base * 100) if base > 0 else 0.0
        st.pop('_lots', None)
        result.append(st)

    result.sort(key=lambda x: x['realized_pnl'], reverse=True)
    return result


def _extract_strategy_summary(payload, symbol_stats):
    metrics = payload.get('metrics') if isinstance(payload, dict) and isinstance(payload.get('metrics'), dict) else {}

    def _pick(*keys):
        for k in keys:
            if k in metrics and metrics.get(k) is not None:
                return metrics.get(k)
        return None

    win_count = sum(1 for x in symbol_stats if x.get('realized_pnl', 0) > 0 and x.get('sell_qty', 0) > 0)
    loss_count = sum(1 for x in symbol_stats if x.get('realized_pnl', 0) < 0 and x.get('sell_qty', 0) > 0)
    total_closed = win_count + loss_count
    win_rate = (win_count / total_closed) if total_closed > 0 else None

    total_profit = _pick('total_profit', 'total_pnl', 'pnl', 'net_profit')
    if total_profit is None:
        total_profit = sum(float(x.get('realized_pnl', 0) or 0) for x in symbol_stats)

    return {
        'total_profit': total_profit,
        'total_return': _pick('total_return', 'return', 'cum_return'),
        'annualized_return': _pick('annualized_return', 'annual_return', 'cagr'),
        'sharpe': _pick('sharpe', 'sharpe_ratio'),
        'beta': _pick('beta'),
        'alpha': _pick('alpha'),
        'win_rate': _pick('win_rate') if _pick('win_rate') is not None else win_rate,
        'win_count': _pick('win_count', 'wins') if _pick('win_count', 'wins') is not None else win_count,
        'loss_count': _pick('loss_count', 'losses') if _pick('loss_count', 'losses') is not None else loss_count,
        'ic': _pick('ic', 'information_coefficient'),
        'volatility': _pick('volatility', 'annualized_volatility', 'vol'),
    }


def _fmt_ratio(v):
    if v is None:
        return '-'
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return '-'


def _fmt_num(v, ndigits=4):
    if v is None:
        return '-'
    try:
        return f"{float(v):.{ndigits}f}"
    except (TypeError, ValueError):
        return '-'

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
    # Use absolute path relative to the app's root directory
    template_path = os.path.join(app.root_path, 'templates', 'prompt.txt')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        return {"error": f"Could not find template at {template_path}: {str(e)}"}, 500


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



@app.route('/chenyiyun/selected')
def chenyiyun_selected_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    signals = []
    positions = []
    pagination = {
        'page': page,
        'per_page': per_page,
        'total': 0,
        'pages': 0,
        'has_prev': False,
        'has_next': False,
        'prev_num': page - 1,
        'next_num': page + 1,
    }

    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ads_chenyiyun_selected_signals (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    signal_time DATETIME NOT NULL,
                    trade_date DATE NOT NULL,
                    ts_code VARCHAR(16) NOT NULL,
                    stock_name VARCHAR(64) NOT NULL,
                    side VARCHAR(8) NOT NULL,
                    open_price DOUBLE NOT NULL,
                    allocated_shares INT NOT NULL,
                    current_shares INT NOT NULL,
                    target_shares INT NOT NULL,
                    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_signal (trade_date, ts_code, side)
                )
                """
            )
            conn.commit()

            pagination, offset = get_pagination(cursor, "ads_chenyiyun_selected_signals", page, per_page)
            cursor.execute(
                """
                SELECT signal_time, trade_date, ts_code, stock_name, side, open_price, allocated_shares, current_shares, target_shares
                FROM ads_chenyiyun_selected_signals
                ORDER BY signal_time DESC, ts_code ASC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            signals = cursor.fetchall()

            cursor.execute(
                """
                SELECT symbol AS ts_code, name AS stock_name, entry_date, avg_cost, current_price, shares
                FROM live_positions
                ORDER BY id DESC
                """
            )
            positions = cursor.fetchall()
    except Exception as e:
        flash(f"陈依云精选策略页面数据库不可用: {e}", "danger")

    return render_template(
        'chenyiyun_selected.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        signals=signals,
        positions=positions,
        pagination=pagination,
    )
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
    
    # Parameters
    active_tab = request.args.get('tab', 'summary')
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    # 1. 默认查询日期（参数或今天）
    date_param = request.args.get('date')
    if date_param:
        query_date = date_param.replace('-', '') # Handle YYYY-MM-DD from date picker
    else:
        query_date = datetime.now().strftime('%Y%m%d')
    
    # ISO date for <input type="date">
    try:
        query_date_iso = datetime.strptime(query_date, '%Y%m%d').strftime('%Y-%m-%d')
    except:
        query_date_iso = ''
    
    daily_stats = {}
    signals = []
    pagination = None
    last_completed_date = None

    with conn.cursor() as cursor:
        # Always fetch stats for the summary cards
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

        # Find latest date if current query has no data
        cursor.execute("SELECT MAX(batch_date) as last_date FROM bs_detection_results")
        res = cursor.fetchone()
        last_completed_date = res['last_date'] if res else None

        if active_tab == 'summary':
            where_stmt = "WHERE batch_date = %s AND (has_buy_signal=1 OR has_sell_signal=1)"
            params = (query_date,)
            pagination, offset = get_pagination(cursor, "bs_detection_results", page, per_page, where_stmt, params)
            
            sql_details = f"""
                SELECT t1.*, t2.stock_name 
                FROM bs_detection_results t1
                LEFT JOIN a_share_stock_list t2 ON t1.stock_code = t2.stock_code COLLATE utf8mb4_general_ci
                {where_stmt}
                ORDER BY t1.stock_code
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_details, (query_date, per_page, offset))
            signals = cursor.fetchall()

        elif active_tab == 'holding_b':
            # Logic: Latest signal is a B signal
            # We use a subquery as the table name for get_pagination
            subquery = """
                (SELECT t1.*
                 FROM bs_detection_results t1
                 INNER JOIN (
                     SELECT stock_code, MAX(created_at) as max_created
                     FROM bs_detection_results
                     WHERE has_buy_signal = 1 OR has_sell_signal = 1
                     GROUP BY stock_code
                 ) t2 ON t1.stock_code = t2.stock_code AND t1.created_at = t2.max_created
                 WHERE t1.has_buy_signal = 1)
            """
            pagination, offset = get_pagination(cursor, f"{subquery} as sub", page, per_page)
            
            sql_holding = f"""
                SELECT sub.*, t_name.stock_name
                FROM {subquery} as sub
                LEFT JOIN a_share_stock_list t_name ON sub.stock_code = t_name.stock_code COLLATE utf8mb4_general_ci
                ORDER BY sub.created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_holding, (per_page, offset))
            signals = cursor.fetchall()

        elif active_tab == 'recent_buy':
            where_stmt = "WHERE has_buy_signal=1"
            pagination, offset = get_pagination(cursor, "bs_detection_results", page, per_page, where_stmt)
            
            sql_buy = f"""
                SELECT t1.*, t2.stock_name 
                FROM bs_detection_results t1
                LEFT JOIN a_share_stock_list t2 ON t1.stock_code = t2.stock_code COLLATE utf8mb4_general_ci
                {where_stmt}
                ORDER BY t1.created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_buy, (per_page, offset))
            signals = cursor.fetchall()

        elif active_tab == 'recent_sell':
            where_stmt = "WHERE has_sell_signal=1"
            pagination, offset = get_pagination(cursor, "bs_detection_results", page, per_page, where_stmt)
            
            sql_sell = f"""
                SELECT t1.*, t2.stock_name 
                FROM bs_detection_results t1
                LEFT JOIN a_share_stock_list t2 ON t1.stock_code = t2.stock_code COLLATE utf8mb4_general_ci
                {where_stmt}
                ORDER BY t1.created_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_sell, (per_page, offset))
            signals = cursor.fetchall()

    return render_template('sina_monitor.html',
                           active_tab=active_tab,
                           query_date=query_date,
                           query_date_iso=query_date_iso,
                           daily_stats=daily_stats,
                           signals=signals,
                           pagination=pagination,
                           last_completed_date=last_completed_date,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/api/live/execute_trade', methods=['POST'])
def execute_trade():
    """Execute a trade from the web interface using LiveTracker"""
    if LiveTracker is None:
        return {"error": f"LiveTracker 不可用: {LIVE_TRACKER_IMPORT_ERROR}"}, 503

    try:
        data = request.json
        if not data:
            return {"error": "No data provided"}, 400
        
        symbol = str(data.get('symbol') or "").zfill(6)
        action = str(data.get('action') or "").lower()
        price = float(data.get('price') or 0)
        shares = int(data.get('shares') or 0)
        reason = data.get('reason') or "Web 执行"
        score = data.get('score')
        
        if not symbol or not action or price <= 0 or shares <= 0:
            return {"error": "Invalid parameters"}, 400
        
        tracker = LiveTracker()
        
        if action == "buy":
            tracker.record_buy(
                symbol=symbol,
                price=price,
                shares=shares,
                reason=reason,
                score=score
            )
        elif action == "sell":
            tracker.record_sell(
                symbol=symbol,
                price=price,
                shares=shares,
                reason=reason,
                score=score
            )
        else:
            return {"error": f"Unsupported action: {action}"}, 400
            
        return {"success": True, "message": f"{action.upper()} {symbol} 执行成功"}
        
    except Exception as e:
        print(f"Failed to execute trade: {e}")
        return {"error": str(e)}, 500


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
    executed_symbols = set()
    try:
        if conn:
            m1_summary = _fetch_m1_event_summary(conn)
            # Fetch symbols traded today to mark as executed
            today_str = datetime.now().strftime('%Y-%m-%d')
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT symbol FROM live_trades WHERE trade_date = %s", (today_str,))
                executed_symbols = {r['symbol'] for r in cursor.fetchall()}
    except Exception as e:
        print(f"Failed to load M1 summary or executed symbols in M7 page: {e}")

    # Mark orders as executed based on DB history
    for o in m7_eval.get('orders', []):
        if o.get('symbol') in executed_symbols:
            o['is_executed'] = True
        else:
            o['is_executed'] = False

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




@app.route('/backtest/results', methods=['GET', 'POST'])
def backtest_results():
    if request.method == 'POST':
        upload_file = request.files.get('backtest_file')
        if not upload_file or not upload_file.filename:
            flash('请选择需要上传的 JSON 文件。', 'danger')
            return redirect(url_for('backtest_results'))

        filename = secure_filename(upload_file.filename)
        if not filename.lower().endswith('.json'):
            flash('仅支持上传 .json 文件。', 'danger')
            return redirect(url_for('backtest_results'))

        UPLOAD_BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
        save_path = UPLOAD_BACKTEST_DIR / filename
        upload_file.save(save_path)
        flash(f'上传成功：{save_path.as_posix()}', 'success')
        return redirect(url_for('backtest_results', file=save_path.as_posix()))

    json_files = _get_backtest_json_files()
    selected = request.args.get('file')

    selected_file = None
    if selected:
        selected_path = Path(selected)
        for item in json_files:
            if item == selected_path or item.as_posix() == selected:
                selected_file = item
                break

    if selected_file is None and json_files:
        selected_file = json_files[0]

    file_options = [f.as_posix() for f in json_files]
    chart_labels = []
    chart_values = []
    trade_rows = []
    symbol_stats = []
    strategy_summary = {}
    error_msg = None

    if selected_file:
        try:
            payload = json.loads(selected_file.read_text(encoding='utf-8'))
            equity_points = _extract_equity_points(payload)
            trade_rows = _extract_trade_rows(payload)
            symbol_stats = _build_symbol_performance(trade_rows)
            strategy_summary = _extract_strategy_summary(payload, symbol_stats)
            chart_labels = [p['date'] for p in equity_points]
            raw_values = [p['value'] for p in equity_points]
            if raw_values and raw_values[0] not in (0, None):
                base_value = float(raw_values[0])
                chart_values = [round(float(v) / base_value, 6) for v in raw_values]
            else:
                chart_values = raw_values
        except Exception as e:
            error_msg = f'读取回测结果失败: {e}'

    return render_template(
        'backtest_results.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        file_options=file_options,
        selected_file=selected_file.as_posix() if selected_file else '',
        chart_labels=chart_labels,
        chart_values=chart_values,
        trade_rows=trade_rows,
        symbol_stats=symbol_stats,
        strategy_summary=strategy_summary,
        error_msg=error_msg,
    )


@app.route('/stock_pool')
def stock_pool():
    selected_pool_id = request.args.get('pool_id', type=int)
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = 20

    conn = get_db()
    with conn.cursor() as cursor:
        _ensure_stock_pool_schema(cursor)
        _ensure_seed_pools(cursor)
        _sync_recent_buy_pool(cursor)
        _seed_self_selected_if_empty(cursor)
        conn.commit()

        cursor.execute(
            """
            SELECT p.id, p.pool_name, p.pool_key, p.is_editable, p.source_type,
                   COUNT(i.id) AS stock_count
            FROM stock_pools p
            LEFT JOIN stock_pool_items i ON i.pool_id = p.id
            GROUP BY p.id, p.pool_name, p.pool_key, p.is_editable, p.source_type
            ORDER BY p.is_system DESC, p.id ASC
            """
        )
        pools = cursor.fetchall()

        if not pools:
            flash('股票池未初始化成功。', 'danger')
            return render_template('stock_pool.html', now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pools=[], selected_pool=None, stocks=[])

        if selected_pool_id is None or selected_pool_id not in {x['id'] for x in pools}:
            selected_pool_id = pools[0]['id']

        cursor.execute("SELECT * FROM stock_pools WHERE id = %s", (selected_pool_id,))
        selected_pool = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) AS c FROM stock_pool_items WHERE pool_id = %s", (selected_pool_id,))
        total_stocks = int((cursor.fetchone() or {}).get('c') or 0)
        total_pages = max((total_stocks + page_size - 1) // page_size, 1)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size

        cursor.execute(
            """
            SELECT id, symbol, stock_name, note, created_at, updated_at
            FROM stock_pool_items
            WHERE pool_id = %s
            ORDER BY updated_at DESC, symbol ASC
            LIMIT %s OFFSET %s
            """,
            (selected_pool_id, page_size, offset),
        )
        stocks = cursor.fetchall()

    return render_template(
        'stock_pool.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        pools=pools,
        selected_pool=selected_pool,
        stocks=stocks,
        page=page,
        page_size=page_size,
        total_stocks=total_stocks,
        total_pages=total_pages,
    )


def _ensure_stock_pool_schema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_pools (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            pool_key VARCHAR(32) NOT NULL,
            pool_name VARCHAR(64) NOT NULL,
            source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
            is_system TINYINT NOT NULL DEFAULT 0,
            is_editable TINYINT NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_pool_key (pool_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_pool_items (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            pool_id BIGINT NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            stock_name VARCHAR(64) NOT NULL,
            note VARCHAR(255) DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_pool_symbol (pool_id, symbol),
            KEY idx_pool_id (pool_id),
            CONSTRAINT fk_pool_items_pool FOREIGN KEY (pool_id) REFERENCES stock_pools(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _ensure_seed_pools(cursor):
    cursor.execute(
        """
        INSERT INTO stock_pools (pool_key, pool_name, source_type, is_system, is_editable)
        VALUES
            ('SELF_SELECTED', '自选股池', 'MANUAL', 1, 1),
            ('RECENT_BUY', '最近有买点股票池', 'SIGNAL_SYNC', 1, 0)
        ON DUPLICATE KEY UPDATE
            pool_name = VALUES(pool_name),
            source_type = VALUES(source_type),
            is_system = VALUES(is_system),
            is_editable = VALUES(is_editable),
            updated_at = CURRENT_TIMESTAMP
        """
    )


def _seed_self_selected_if_empty(cursor):
    cursor.execute("SELECT id FROM stock_pools WHERE pool_key='SELF_SELECTED' LIMIT 1")
    row = cursor.fetchone()
    if not row:
        return
    pool_id = row['id']

    cursor.execute("SELECT COUNT(*) AS c FROM stock_pool_items WHERE pool_id = %s", (pool_id,))
    if (cursor.fetchone() or {}).get('c', 0) > 0:
        return

    cursor.execute("SHOW TABLES LIKE 'score_rank_daily'")
    if cursor.fetchone() is None:
        return

    cursor.execute("SELECT MAX(trade_date) AS d FROM score_rank_daily")
    d = (cursor.fetchone() or {}).get('d')
    if not d:
        return

    cursor.execute(
        """
        INSERT INTO stock_pool_items (pool_id, symbol, stock_name)
        SELECT %s, symbol, COALESCE(name, symbol)
        FROM score_rank_daily
        WHERE trade_date = %s AND is_self_selected = 1
        ON DUPLICATE KEY UPDATE
            stock_name = VALUES(stock_name),
            updated_at = CURRENT_TIMESTAMP
        """,
        (pool_id, d),
    )


def _sync_recent_buy_pool(cursor):
    cursor.execute("SELECT id FROM stock_pools WHERE pool_key='RECENT_BUY' LIMIT 1")
    row = cursor.fetchone()
    if not row:
        return
    pool_id = row['id']

    cursor.execute("SHOW TABLES LIKE 'bs_detection_results'")
    if cursor.fetchone() is None:
        return

    cursor.execute("SELECT MAX(batch_date) AS d FROM bs_detection_results")
    latest_date = (cursor.fetchone() or {}).get('d')
    if not latest_date:
        return

    cursor.execute(
        """
        SELECT b.stock_code AS symbol, COALESCE(a.stock_name, b.stock_code) AS stock_name
        FROM bs_detection_results b
        LEFT JOIN a_share_stock_list a ON b.stock_code = a.stock_code COLLATE utf8mb4_general_ci
        WHERE b.batch_date = %s AND b.has_buy_signal = 1
        """,
        (latest_date,),
    )
    rows = cursor.fetchall()
    symbols = {str(r.get('symbol') or '').zfill(6) for r in rows if r.get('symbol')}

    if symbols:
        placeholders = ','.join(['%s'] * len(symbols))
        cursor.execute(
            f"DELETE FROM stock_pool_items WHERE pool_id = %s AND symbol NOT IN ({placeholders})",
            tuple([pool_id] + list(symbols)),
        )
    else:
        cursor.execute("DELETE FROM stock_pool_items WHERE pool_id = %s", (pool_id,))

    for r in rows:
        symbol = str(r.get('symbol') or '').zfill(6)
        if not symbol.isdigit():
            continue
        cursor.execute(
            """
            INSERT INTO stock_pool_items (pool_id, symbol, stock_name, note)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stock_name = VALUES(stock_name),
                note = VALUES(note),
                updated_at = CURRENT_TIMESTAMP
            """,
            (pool_id, symbol, r.get('stock_name') or symbol, f'同步日期: {latest_date}'),
        )


@app.route('/stock_pool/pool/add', methods=['POST'])
def add_stock_pool():
    flash('当前版本仅允许管理【自选股池】，不支持新增其他股票池。', 'danger')
    return redirect(url_for('stock_pool'))


@app.route('/stock_pool/pool/<int:pool_id>/rename', methods=['POST'])
def rename_stock_pool(pool_id):
    pool_name = (request.form.get('pool_name') or '').strip()
    if not pool_name:
        flash('股票池名称不能为空。', 'danger')
        return redirect(url_for('stock_pool', pool_id=pool_id))

    conn = get_db()
    with conn.cursor() as cursor:
        try:
            cursor.execute("SELECT is_editable FROM stock_pools WHERE id = %s", (pool_id,))
            pool = cursor.fetchone()
            if not pool:
                flash('未找到要更新的股票池。', 'danger')
                return redirect(url_for('stock_pool', pool_id=pool_id))
            if int(pool.get('is_editable') or 0) != 1:
                flash('该股票池为只读属性，不允许手动管理。', 'danger')
                return redirect(url_for('stock_pool', pool_id=pool_id))

            cursor.execute("UPDATE stock_pools SET pool_name = %s WHERE id = %s", (pool_name, pool_id))
            conn.commit()
            flash('股票池名称已更新。', 'success')
        except Exception as e:
            flash(f'更新股票池名称失败: {e}', 'danger')

    return redirect(url_for('stock_pool', pool_id=pool_id))


@app.route('/stock_pool/item/add', methods=['POST'])
def add_stock_pool_item():
    pool_id = request.form.get('pool_id', type=int)
    symbol = str(request.form.get('symbol') or '').strip()
    stock_name = (request.form.get('stock_name') or '').strip()
    note = (request.form.get('note') or '').strip()
    symbols_batch = (request.form.get('symbols_batch') or '').strip()

    if not pool_id:
        flash('缺少股票池参数。', 'danger')
        return redirect(url_for('stock_pool'))

    raw_codes = []
    if symbol:
        raw_codes.append(symbol)

    if symbols_batch:
        normalized = symbols_batch.replace('\n', ',').replace('\t', ',').replace('，', ',').replace('；', ',').replace(';', ',').replace(' ', ',')
        raw_codes.extend([item.strip() for item in normalized.split(',') if item.strip()])

    if not raw_codes:
        flash('请至少输入一个股票代码。', 'danger')
        return redirect(url_for('stock_pool', pool_id=pool_id))

    valid_symbols = []
    invalid_symbols = []
    seen = set()
    for code in raw_codes:
        normalized_code = code.zfill(6)
        if normalized_code.isdigit() and len(normalized_code) == 6:
            if normalized_code not in seen:
                seen.add(normalized_code)
                valid_symbols.append(normalized_code)
        else:
            invalid_symbols.append(code)

    if not valid_symbols:
        flash('股票代码必须为 6 位数字。', 'danger')
        return redirect(url_for('stock_pool', pool_id=pool_id))

    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT pool_name, pool_key, is_editable FROM stock_pools WHERE id = %s", (pool_id,))
        pool = cursor.fetchone()
        if not pool:
            flash('股票池不存在。', 'danger')
            return redirect(url_for('stock_pool'))

        if int(pool.get('is_editable') or 0) != 1:
            flash('该股票池为只读属性，不允许手动管理。', 'danger')
            return redirect(url_for('stock_pool', pool_id=pool_id))

        try:
            for code in valid_symbols:
                current_name = stock_name if (stock_name and len(valid_symbols) == 1) else code
                cursor.execute(
                    """
                    INSERT INTO stock_pool_items (pool_id, symbol, stock_name, note)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        stock_name = VALUES(stock_name),
                        note = VALUES(note),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (pool_id, code, current_name, note or None),
                )
            conn.commit()
            flash(f"已保存到【{pool['pool_name']}】：{len(valid_symbols)} 只股票", 'success')
            if invalid_symbols:
                flash(f"以下代码格式无效，已跳过：{', '.join(invalid_symbols)}", 'danger')
        except Exception as e:
            flash(f'保存股票失败: {e}', 'danger')

    return redirect(url_for('stock_pool', pool_id=pool_id))


@app.route('/stock_pool/item/delete/<int:item_id>', methods=['POST'])
def delete_stock_pool_item(item_id):
    conn = get_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.id, i.pool_id, p.is_editable, p.pool_key
            FROM stock_pool_items i
            JOIN stock_pools p ON p.id = i.pool_id
            WHERE i.id = %s
            """,
            (item_id,),
        )
        row = cursor.fetchone()
        if not row:
            flash('未找到待删除记录。', 'danger')
            return redirect(url_for('stock_pool'))

        if int(row.get('is_editable') or 0) != 1:
            flash('该股票池为只读属性，不允许手动管理。', 'danger')
            return redirect(url_for('stock_pool', pool_id=row['pool_id']))

        try:
            cursor.execute("DELETE FROM stock_pool_items WHERE id = %s", (item_id,))
            conn.commit()
            flash('股票池记录已删除。', 'success')
        except Exception as e:
            flash(f'删除失败: {e}', 'danger')

    return redirect(url_for('stock_pool', pool_id=row['pool_id']))


@app.route('/admin')
def admin():
    now_date_iso = datetime.now().strftime('%Y-%m-%d')
    return render_template('admin.html', 
                           tasks=TASKS,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           now_date=now_date_iso)

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
    date_str = request.form.get('date', '').replace('-', '') # Handle YYYY-MM-DD
    strategy_id = request.form.get('strategy_id') or 'Manual'
    
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
            cursor.execute(sql, (symbol, name, date_str, shares, price, price))
            conn.commit()
            flash(f"Position {symbol} added/updated.", 'success')
        except Exception as e:
            flash(f"Failed to add position: {e}", 'danger')
            
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
