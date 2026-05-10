from flask import Flask, render_template, g, request, redirect, url_for, flash, jsonify
import sys
import os
import pymysql
import json
from pathlib import Path
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
import subprocess
import threading
import time
import tempfile
import urllib.request
import urllib.error
from werkzeug.utils import secure_filename
from project_network import build_direct_network_env, enforce_direct_network

enforce_direct_network()

from scoreRank.core.bs_enhanced_score import calculate_bs_consensus_signal, calculate_bs_enhanced_score, calculate_bs_research_signal, calculate_bs_score_v2, calculate_bs_trade_gate

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
        M7_FORCED_REASON_CODES,
        M7_RULE_VERSION_V1,
        M7_RULE_VERSION_V21,
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
        M7_FORCED_REASON_CODES,
        M7_RULE_VERSION_V1,
        M7_RULE_VERSION_V21,
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

DEFAULT_CHENYIYUN_SELECTED_SETTINGS = {
    "stock_count": 10,
    "position_ratio": 1.0,
    "holding_days": 20,
}

# Task Status Storage (Loaded from DB on start)
TASKS = {
    "sina_picture": {
        "name": "sina 图片截图",
        "description": "Sina财经B/S信号批量截图",
        "script": "sina/bs_detection/main.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "15:20",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_analyse": {
        "name": "sina 买卖点分析",
        "description": "基于已截图图片执行B/S买卖点分析并落库",
        "script": "sina/bs_detection/main.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "16:10",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_score": {
        "name": "全A股评分",
        "description": "全市场A股多因子量化评分落库",
        "script": "scoreRank/run_daily.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:00",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_bs_consensus": {
        "name": "B点综合评分建议",
        "description": "批量复算并写回B点增强分、研究分、综合分与综合建议",
        "script": "scoreRank/cli/build_bs_consensus.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:20",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_m8": {
        "name": "M8策略回归落库",
        "description": "M8评分策略历史回归与信号生成",
        "script": "scoreRank/cli/run_m8_cycle.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:10",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_snapshot": {
        "name": "sina 实盘快照",
        "description": "账户实时持仓与净值曲线快照同步",
        "script": "sina/live_tracker/run_live_tracker.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:30",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sina_m7_sell": {
        "name": "M7 卖出评估",
        "description": "基于 M4 目标仓位与实盘持仓执行 M7 卖出规则引擎（m7_sell_v2.1）",
        "script": "scripts/ops/run_m7_sell_eval.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:35",
        "next_run": "-",
        "trading_day_only": True,
    },
    "eastmoney": {
        "name": "eastmoney 策略扫描",
        "description": "东方财富社区舆情扫描与个股热度分析",
        "script": "eastmoney/run_strategy.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "16:30",
        "next_run": "-",
        "trading_day_only": True,
    },
    "sync_trade_cal": {
        "name": "交易日历同步",
        "description": "同步Tushare官方交易日历到本地库",
        "script": "scripts/ops/sync_trade_cal.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "08:00",
        "next_run": "-",
        "trading_day_only": False,
    },
    "chenyiyun_selected": {
        "name": "陈依云信号检查（09:05）",
        "description": "检查是否触发买卖信号，生成调仓建议",
        "script": "scripts/ops/run_chenyiyun_signal_check.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "09:05",
        "next_run": "-",
        "trading_day_only": True,
    },
    "chenyiyun_weekly_rebalance": {
        "name": "陈依云周调仓（周一09:30）",
        "description": "每周一执行周度仓位调整",
        "script": "scripts/ops/run_chenyiyun_weekly_rebalance.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "09:30",
        "next_run": "-",
        "trading_day_only": True,
    },
    "chenyiyun_limitup_check": {
        "name": "陈依云涨停检查（14:00）",
        "description": "检查持仓涨停打开并生成卖出建议",
        "script": "scripts/ops/run_chenyiyun_limitup_check.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "14:00",
        "next_run": "-",
        "trading_day_only": True,
    },
    "chenyiyun_position_update": {
        "name": "陈依云仓位更新（21:10）",
        "description": "同步持仓价格并更新每日仓位快照",
        "script": "scripts/ops/run_chenyiyun_position_update.py",
        "last_run": "Never",
        "status": "Idle",
        "switched_day": False,
        "schedule_enabled": False,
        "schedule_time": "21:10",
        "next_run": "-",
        "trading_day_only": True,
    },
}
TASKS_LOCK = threading.Lock()
TASK_HEARTBEAT_INTERVAL_SECONDS = 20
TASK_STALE_TIMEOUT_SECONDS = 3 * 3600  # Sina B/S full run can take ~1h
SCHEDULED_TASK_WHITELIST = {"sina_picture", "sina_analyse", "sina_score", "sina_bs_consensus", "sina_m8", "sina_snapshot", "sina_m7_sell"}

NOTIFICATION_CHANNEL_DEFS = [
    ("feishu", "飞书"),
    ("wechat", "企业微信"),
    ("dingtalk", "钉钉"),
    ("custom", "自定义Webhook"),
]
DEFAULT_FEISHU_TEST_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/a8374c19-3620-4891-8c7a-df6885229607"

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



def _ensure_task_management_schema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_task_status (
            task_name VARCHAR(64) PRIMARY KEY,
            last_run DATETIME NULL,
            status VARCHAR(64) NOT NULL DEFAULT 'Idle',
            switched_day TINYINT(1) NOT NULL DEFAULT 0,
            schedule_enabled TINYINT(1) NOT NULL DEFAULT 0,
            schedule_time VARCHAR(5) NOT NULL DEFAULT '00:00',
            next_run DATETIME NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    
    # Check for missing columns and add them if necessary
    cursor.execute("DESC app_task_status")
    existing_columns = [row['Field'] for row in cursor.fetchall()]
    
    if 'schedule_enabled' not in existing_columns:
        cursor.execute("ALTER TABLE app_task_status ADD COLUMN schedule_enabled TINYINT(1) NOT NULL DEFAULT 0 AFTER switched_day")
    if 'schedule_time' not in existing_columns:
        cursor.execute("ALTER TABLE app_task_status ADD COLUMN schedule_time VARCHAR(5) NOT NULL DEFAULT '00:00' AFTER schedule_enabled")
    if 'next_run' not in existing_columns:
        cursor.execute("ALTER TABLE app_task_status ADD COLUMN next_run DATETIME NULL AFTER schedule_time")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_task_history (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            task_name VARCHAR(64) NOT NULL,
            task_display_name VARCHAR(128) NOT NULL,
            trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual',
            started_at DATETIME NOT NULL,
            finished_at DATETIME NULL,
            status VARCHAR(32) NOT NULL,
            exit_code INT NULL,
            duration_seconds INT NULL,
            message TEXT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_task_started (task_name, started_at),
            KEY idx_started (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_task_runner (
            id TINYINT PRIMARY KEY,
            status VARCHAR(16) NOT NULL DEFAULT 'IDLE',
            running_task_name VARCHAR(64) NULL,
            trigger_type VARCHAR(16) NULL,
            queue_id BIGINT NULL,
            started_at DATETIME NULL,
            heartbeat_at DATETIME NULL,
            finished_at DATETIME NULL,
            message VARCHAR(255) NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_task_queue (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            task_name VARCHAR(64) NOT NULL,
            trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual',
            scheduled_for DATETIME NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            requested_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME NULL,
            finished_at DATETIME NULL,
            exit_code INT NULL,
            message TEXT NULL,
            KEY idx_queue_status_requested (status, requested_at),
            KEY idx_queue_task_status (task_name, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_task_lock (
            task_name VARCHAR(64) PRIMARY KEY,
            status VARCHAR(16) NOT NULL DEFAULT 'IDLE',
            queue_id BIGINT NULL,
            started_at DATETIME NULL,
            heartbeat_at DATETIME NULL,
            finished_at DATETIME NULL,
            message VARCHAR(255) NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    for task_name in TASKS.keys():
        cursor.execute(
            """
            INSERT INTO app_task_lock (task_name, status, message)
            VALUES (%s, 'IDLE', 'ready')
            ON DUPLICATE KEY UPDATE task_name = task_name
            """,
            (task_name,),
        )
    _ensure_notification_channel_schema(cursor)


def _default_notification_channels():
    rows = []
    for key, name in NOTIFICATION_CHANNEL_DEFS:
        if key == "feishu":
            rows.append(
                {
                    "channel_key": key,
                    "channel_name": name,
                    "webhook_url": DEFAULT_FEISHU_TEST_WEBHOOK,
                    "enabled": 1,
                }
            )
        else:
            rows.append(
                {
                    "channel_key": key,
                    "channel_name": name,
                    "webhook_url": "",
                    "enabled": 0,
                }
            )
    return rows


def _ensure_notification_channel_schema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_notification_channel (
            channel_key VARCHAR(32) PRIMARY KEY,
            channel_name VARCHAR(64) NOT NULL,
            webhook_url TEXT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    defaults = _default_notification_channels()
    for row in defaults:
        cursor.execute(
            """
            INSERT INTO app_notification_channel (channel_key, channel_name, webhook_url, enabled)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE channel_name = VALUES(channel_name)
            """,
            (
                row["channel_key"],
                row["channel_name"],
                row["webhook_url"],
                int(row["enabled"]),
            ),
        )


def _load_notification_channels_from_cursor(cursor):
    _ensure_notification_channel_schema(cursor)
    cursor.execute(
        """
        SELECT channel_key, channel_name, webhook_url, enabled
        FROM app_notification_channel
        ORDER BY channel_key
        """
    )
    rows = {str(r.get("channel_key")): r for r in cursor.fetchall()}
    out = []
    for default in _default_notification_channels():
        key = default["channel_key"]
        src = rows.get(key) or {}
        out.append(
            {
                "channel_key": key,
                "channel_name": src.get("channel_name") or default["channel_name"],
                "webhook_url": str(src.get("webhook_url") or default["webhook_url"] or ""),
                "enabled": int(src.get("enabled") if src.get("enabled") is not None else default["enabled"]),
            }
        )
    return out


def _normalize_webhook_url(raw):
    value = str(raw or "").strip()
    if not value:
        return ""
    if not (value.startswith("http://") or value.startswith("https://")):
        return ""
    return value


def _normalize_schedule_time(raw_time):
    parts = str(raw_time or '').strip().split(':')
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return None
    if h < 0 or h > 23 or m < 0 or m > 59:
        return None
    return f"{h:02d}:{m:02d}"


def _normalize_datestr(raw_value):
    val = str(raw_value or "").strip()
    if not val:
        return None
    val = val.replace("-", "")
    if len(val) != 8 or not val.isdigit():
        return None
    return val


def _db_value_to_datestr(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, (datetime, date)):
        return raw_value.strftime("%Y%m%d")

    normalized = _normalize_datestr(raw_value)
    if normalized:
        return normalized

    value = str(raw_value).strip()
    if not value:
        return None

    try:
        return datetime.strptime(value[:10].replace("/", "-"), "%Y-%m-%d").strftime("%Y%m%d")
    except Exception:
        pass

    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        return _normalize_datestr(digits[:8])
    return None


def _db_value_to_datetime(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value
    if isinstance(raw_value, date):
        return datetime.combine(raw_value, datetime.min.time())

    value = str(raw_value).strip()
    if not value:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            continue
    return None


def _parse_task_last_run(raw_value):
    value = str(raw_value or "").strip()
    if not value or value == "Never":
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _compute_next_run(schedule_time, enabled):
    if not enabled:
        return None
    normalized = _normalize_schedule_time(schedule_time)
    if not normalized:
        return None
    hour, minute = [int(x) for x in normalized.split(':')]
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _refresh_task_next_runs():
    with TASKS_LOCK:
        for task in TASKS.values():
            next_dt = _compute_next_run(task.get('schedule_time'), bool(task.get('schedule_enabled')))
            task['next_run'] = next_dt.strftime('%Y-%m-%d %H:%M:%S') if next_dt else '-'


def _insert_task_history(task_name, trigger_type, status, started_at, finished_at=None, exit_code=None, duration_seconds=None, message=None):
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute(
                """
                INSERT INTO app_task_history
                    (task_name, task_display_name, trigger_type, started_at, finished_at, status, exit_code, duration_seconds, message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (task_name, TASKS[task_name]['name'], trigger_type, started_at, finished_at, status, exit_code, duration_seconds, message),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to insert task history: {e}")


def _get_task_history(limit=100, task_name=None):
    rows = []
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            if task_name:
                cursor.execute(
                    """
                    SELECT id, task_name, task_display_name, trigger_type, started_at, finished_at, status, exit_code, duration_seconds, message
                    FROM app_task_history
                    WHERE task_name = %s
                    ORDER BY started_at DESC
                    LIMIT %s
                    """,
                    (task_name, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, task_name, task_display_name, trigger_type, started_at, finished_at, status, exit_code, duration_seconds, message
                    FROM app_task_history
                    ORDER BY started_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
    except Exception as e:
        print(f"Failed to load task history: {e}")
    return rows


def _get_task_lock_rows(limit=50):
    rows = []
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute(
                """
                SELECT task_name, status, started_at, heartbeat_at, finished_at, message
                FROM app_task_lock
                ORDER BY (status='RUNNING') DESC, task_name ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    except Exception as e:
        print(f"Failed to load task lock rows: {e}")
    return rows


def _is_running_status_text(status_text):
    return str(status_text or "").strip().lower().startswith("running")


def _map_history_to_task_status(history_status):
    hs = str(history_status or "").strip().lower()
    if hs == "success":
        return "Success"
    if hs == "failed":
        return "Failed"
    if hs == "error":
        return "Error"
    return "Idle"


def _reconcile_stale_task_states():
    """
    Reconcile stale UI/DB task status:
    - If app_task_status says running but lock is not RUNNING, mark task as completed/failed/idle by latest history.
    - Close leftover legacy queue RUNNING rows for the same task.
    """
    fixed_rows = []
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            for task_name in TASKS.keys():
                cursor.execute(
                    """
                    SELECT status, last_run
                    FROM app_task_status
                    WHERE task_name = %s
                    LIMIT 1
                    """,
                    (task_name,),
                )
                status_row = cursor.fetchone()
                if not status_row:
                    continue

                current_status = status_row.get("status") or "Idle"
                if not _is_running_status_text(current_status):
                    continue

                cursor.execute(
                    """
                    SELECT status
                    FROM app_task_lock
                    WHERE task_name = %s
                    LIMIT 1
                    """,
                    (task_name,),
                )
                lock_row = cursor.fetchone() or {}
                lock_status = str(lock_row.get("status") or "IDLE").upper()
                if lock_status == "RUNNING":
                    continue

                cursor.execute(
                    """
                    SELECT status
                    FROM app_task_history
                    WHERE task_name = %s
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (task_name,),
                )
                last_history = cursor.fetchone() or {}
                reconciled_status = _map_history_to_task_status(last_history.get("status"))
                if reconciled_status == "Idle":
                    if lock_status == "COMPLETE":
                        reconciled_status = "Success"
                    elif lock_status in ("FAILED", "ERROR"):
                        reconciled_status = "Failed"

                if reconciled_status != current_status:
                    cursor.execute(
                        """
                        UPDATE app_task_status
                        SET status = %s, updated_at = NOW()
                        WHERE task_name = %s
                        """,
                        (reconciled_status, task_name),
                    )
                    with TASKS_LOCK:
                        if task_name in TASKS:
                            TASKS[task_name]["status"] = reconciled_status
                    fixed_rows.append((task_name, current_status, reconciled_status))

                cursor.execute(
                    """
                    UPDATE app_task_queue
                    SET status = 'FAILED',
                        finished_at = NOW(),
                        exit_code = COALESCE(exit_code, -2),
                        message = COALESCE(message, 'stale queue row auto-closed')
                    WHERE task_name = %s
                      AND status = 'RUNNING'
                    """,
                    (task_name,),
                )

            conn.commit()
    except Exception as e:
        print(f"Failed to reconcile stale task states: {e}")
    return fixed_rows


def _try_acquire_task_lock(task_name, trigger_type="manual"):
    if task_name not in TASKS:
        return False, "unknown task"

    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute(
                """
                SELECT status, started_at, heartbeat_at
                FROM app_task_lock
                WHERE task_name = %s
                FOR UPDATE
                """,
                (task_name,),
            )
            lock_row = cursor.fetchone()
            if lock_row is None:
                cursor.execute(
                    """
                    INSERT INTO app_task_lock (task_name, status, message)
                    VALUES (%s, 'IDLE', 'auto-create')
                    """,
                    (task_name,),
                )
                lock_row = {"status": "IDLE", "started_at": None, "heartbeat_at": None}

            lock_status = str(lock_row.get("status") or "").upper()
            now = datetime.now()
            if lock_status == "RUNNING":
                heartbeat = lock_row.get("heartbeat_at") or lock_row.get("started_at")
                if heartbeat and (now - heartbeat).total_seconds() <= TASK_STALE_TIMEOUT_SECONDS:
                    conn.commit()
                    return False, "RUNNING"

                cursor.execute(
                    """
                    UPDATE app_task_lock
                    SET status = 'FAILED',
                        finished_at = NOW(),
                        message = 'stale heartbeat timeout reset'
                    WHERE task_name = %s
                    """,
                    (task_name,),
                )

            cursor.execute(
                """
                UPDATE app_task_lock
                SET status = 'RUNNING',
                    queue_id = NULL,
                    started_at = NOW(),
                    heartbeat_at = NOW(),
                    finished_at = NULL,
                    message = %s
                WHERE task_name = %s
                """,
                (f"running:{trigger_type}", task_name),
            )
            conn.commit()
            return True, None
    except Exception as e:
        print(f"Failed to acquire task lock for {task_name}: {e}")
        return False, str(e)
    finally:
        if conn:
            conn.close()


def _touch_task_lock_heartbeat(task_name):
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute(
                """
                UPDATE app_task_lock
                SET heartbeat_at = NOW(), message = 'running'
                WHERE task_name = %s
                  AND status = 'RUNNING'
                """,
                (task_name,),
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to update task lock heartbeat: {e}")
    finally:
        if conn:
            conn.close()


def _mark_task_lock_finished(task_name, lock_status, message):
    conn = None
    try:
        msg = (str(message) if message is not None else lock_status)[:240]
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute(
                """
                UPDATE app_task_lock
                SET status = %s,
                    queue_id = NULL,
                    finished_at = NOW(),
                    heartbeat_at = NOW(),
                    message = %s
                WHERE task_name = %s
                """,
                (lock_status, msg, task_name),
            )
            conn.commit()
    except Exception as e:
        print(f"Failed to finalize task lock state: {e}")
    finally:
        if conn:
            conn.close()


def _format_task_message(stdout, stderr, verification_lines=None):
    blocks = []
    verification_lines = verification_lines or []

    if verification_lines:
        blocks.append("[verify]\n" + "\n".join(verification_lines))

    stdout_tail = str(stdout or "").strip()
    if stdout_tail:
        blocks.append("[stdout_tail]\n" + stdout_tail[-2000:])

    stderr_tail = str(stderr or "").strip()
    if stderr_tail:
        blocks.append("[stderr_tail]\n" + stderr_tail[-1200:])

    if not blocks:
        return None
    return "\n\n".join(blocks)[-6000:]


def _read_text_tail(file_path, max_chars):
    if not file_path:
        return ""
    try:
        with open(file_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size <= 0:
                return ""
            # Read a bounded tail window to avoid loading huge logs into memory.
            window = min(size, max_chars * 6)
            fh.seek(-window, os.SEEK_END)
            data = fh.read()
        return data.decode("utf-8", errors="replace")[-max_chars:]
    except Exception as e:
        return f"[tail-read-error] {e}"


def _has_scheduled_run_in_slot(task_name, scheduled_for):
    """Return True if a scheduled run already exists in history for this slot."""
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            slot_end = scheduled_for + timedelta(days=1)
            cursor.execute(
                """
                SELECT 1
                FROM app_task_history
                WHERE task_name = %s
                  AND trigger_type = 'schedule'
                  AND started_at >= %s
                  AND started_at < %s
                LIMIT 1
                """,
                (task_name, scheduled_for, slot_end),
            )
            return cursor.fetchone() is not None
    except Exception as e:
        print(f"Failed to check scheduled history slot for {task_name}: {e}")
        return False
    finally:
        if conn:
            conn.close()


def _verify_sina_score_result(started_at, finished_at, run_options=None):
    lines = []
    try:
        target_datestr = _normalize_datestr((run_options or {}).get("datestr"))
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT MAX(trade_date) AS d FROM score_rank_daily")
                latest_score_row = cursor.fetchone() or {}
                latest_score_date = latest_score_row.get("d")
                latest_score_datestr = _db_value_to_datestr(latest_score_date)

                cursor.execute(
                    """
                    SELECT trade_date,
                           COUNT(*) AS rows_cnt,
                           MAX(created_at) AS max_created,
                           SUM(score IS NULL) AS null_score,
                           SUM(opt_score IS NULL) AS null_opt,
                           SUM(claude_score IS NULL) AS null_claude
                    FROM score_rank_daily
                    WHERE trade_date = (SELECT MAX(trade_date) FROM score_rank_daily)
                    GROUP BY trade_date
                    """
                )
                stat = cursor.fetchone() or {}

                cursor.execute("SELECT MAX(batch_date) AS d FROM bs_detection_results")
                latest_bs_row = cursor.fetchone() or {}
                latest_bs_date = latest_bs_row.get("d")
                latest_bs_datestr = _db_value_to_datestr(latest_bs_date)

                cursor.execute("SELECT MAX(trade_date) AS d FROM tushare_stock.dwd_stock_daily_standard")
                latest_market_row = cursor.fetchone() or {}
                latest_market_date = latest_market_row.get("d")
                latest_market_datestr = _db_value_to_datestr(latest_market_date)
        finally:
            conn.close()

        rows_cnt = int(stat.get("rows_cnt") or 0)
        max_created_raw = stat.get("max_created")
        max_created = _db_value_to_datetime(max_created_raw) or max_created_raw
        written_in_window = (
            isinstance(max_created, datetime)
            and (started_at - timedelta(seconds=30)) <= max_created <= (finished_at + timedelta(seconds=180))
        )

        expected_datestr = target_datestr or latest_bs_datestr
        date_match = bool(latest_score_datestr and expected_datestr and latest_score_datestr == expected_datestr)

        null_claude = int(stat.get('null_claude') or 0)
        claude_coverage_ok = rows_cnt <= 0 or null_claude < rows_cnt
        ok = bool(rows_cnt > 0 and written_in_window and date_match and claude_coverage_ok)
        lines.append(
            "result="
            + ("PASS" if ok else "FAIL")
            + f"; expected_score_date={expected_datestr or '-'}; latest_score_date={latest_score_datestr or '-'}"
        )
        lines.append(
            f"score_rank_daily rows={rows_cnt}, max_created={max_created or '-'}, "
            f"null_score={int(stat.get('null_score') or 0)}, "
            f"null_opt={int(stat.get('null_opt') or 0)}, "
            f"null_claude={int(stat.get('null_claude') or 0)}"
        )
        lines.append(f"claude_coverage_ok={claude_coverage_ok}")
        lines.append(
            f"upstream bs_latest={latest_bs_datestr or '-'}, market_latest={latest_market_datestr or '-'}"
        )
        lines.append(f"written_in_window={written_in_window}, date_match={date_match}")
        return ok, lines
    except Exception as e:
        return False, [f"result=FAIL; verifier_error={e}"]


def _verify_sina_bs_consensus_result(started_at, finished_at, run_options=None):
    _ = started_at, finished_at
    lines = []
    try:
        target_datestr = _normalize_datestr((run_options or {}).get("datestr"))
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                _ensure_score_rank_daily_score_columns(cursor)
                if target_datestr:
                    target_date = datetime.strptime(target_datestr, "%Y%m%d").date()
                else:
                    cursor.execute("SELECT MAX(trade_date) AS d FROM score_rank_daily")
                    target_date = (cursor.fetchone() or {}).get("d")

                if target_date is None:
                    return False, ["result=FAIL; reason=no_score_rank_daily_date"]

                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS rows_cnt,
                        SUM(CASE WHEN is_bs_candidate = 1 THEN 1 ELSE 0 END) AS bs_rows,
                        SUM(CASE WHEN bs_score IS NULL THEN 1 ELSE 0 END) AS null_bs_score,
                        SUM(CASE WHEN bs_score_v2 IS NULL THEN 1 ELSE 0 END) AS null_v2,
                        SUM(CASE WHEN bs_research_score IS NULL THEN 1 ELSE 0 END) AS null_research,
                        SUM(CASE WHEN bs_consensus_score IS NULL THEN 1 ELSE 0 END) AS null_consensus
                    FROM score_rank_daily
                    WHERE trade_date = %s
                    """,
                    (target_date,),
                )
                stat = cursor.fetchone() or {}
        finally:
            conn.close()

        rows_cnt = int(stat.get("rows_cnt") or 0)
        null_bs_score = int(stat.get("null_bs_score") or 0)
        null_v2 = int(stat.get("null_v2") or 0)
        null_research = int(stat.get("null_research") or 0)
        null_consensus = int(stat.get("null_consensus") or 0)
        ok = rows_cnt > 0 and null_bs_score == 0 and null_v2 == 0 and null_research == 0 and null_consensus == 0
        lines.append(
            "result="
            + ("PASS" if ok else "FAIL")
            + f"; target_date={_db_value_to_datestr(target_date) or target_date}; rows={rows_cnt}"
        )
        lines.append(
            f"bs_rows={int(stat.get('bs_rows') or 0)}, null_bs_score={null_bs_score}, "
            f"null_v2={null_v2}, null_research={null_research}, null_consensus={null_consensus}"
        )
        return ok, lines
    except Exception as e:
        return False, [f"result=FAIL; verifier_error={e}"]


def _verify_sina_m8_result(started_at, finished_at):
    lines = []
    try:
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, as_of_date, lookback_dates, sample_rows, eligible_rows, searched_total, status, created_at
                    FROM strategy_m8_runs
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                latest_run = cursor.fetchone() or {}

                run_id = int(latest_run.get("id") or 0)
                item_cnt = 0
                if run_id > 0:
                    cursor.execute("SELECT COUNT(*) AS c FROM strategy_m8_items WHERE run_id = %s", (run_id,))
                    item_cnt = int((cursor.fetchone() or {}).get("c") or 0)

                cursor.execute("SELECT MAX(trade_date) AS d FROM score_rank_daily")
                score_max_row = cursor.fetchone() or {}
                score_max_date = score_max_row.get("d")
        finally:
            conn.close()

        created_at_raw = latest_run.get("created_at")
        created_at = _db_value_to_datetime(created_at_raw) or created_at_raw
        as_of_datestr = _db_value_to_datestr(latest_run.get("as_of_date"))
        score_max_datestr = _db_value_to_datestr(score_max_date)
        status = str(latest_run.get("status") or "").upper()

        created_in_window = (
            isinstance(created_at, datetime)
            and (started_at - timedelta(seconds=30)) <= created_at <= (finished_at + timedelta(seconds=120))
        )
        as_of_match = bool(as_of_datestr and score_max_datestr and as_of_datestr == score_max_datestr)
        ok = bool(run_id > 0 and status == "SUCCESS" and item_cnt > 0 and created_in_window and as_of_match)

        lines.append(
            "result="
            + ("PASS" if ok else "FAIL")
            + f"; run_id={run_id}; status={status or '-'}; item_cnt={item_cnt}"
        )
        lines.append(
            f"as_of_date={as_of_datestr or '-'}, score_max_date={score_max_datestr or '-'}, as_of_match={as_of_match}"
        )
        lines.append(
            f"sample_rows={latest_run.get('sample_rows') or 0}, eligible_rows={latest_run.get('eligible_rows') or 0}, "
            f"searched_total={latest_run.get('searched_total') or 0}, created_at={created_at or '-'}"
        )
        lines.append(f"created_in_window={created_in_window}")
        return ok, lines
    except Exception as e:
        return False, [f"result=FAIL; verifier_error={e}"]


def _verify_sina_m7_sell_result(started_at, finished_at, run_options=None):
    lines = []
    try:
        target_datestr = _normalize_datestr((run_options or {}).get("datestr"))
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cursor:
                _ensure_m7_sell_signal_table(cursor)
                if target_datestr:
                    target_date = datetime.strptime(target_datestr, "%Y%m%d").date()
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS c, MAX(updated_at) AS max_updated
                        FROM m7_sell_signals
                        WHERE signal_date = %s AND source = 'm7_nightly'
                        """,
                        (target_date,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS c, MAX(updated_at) AS max_updated, MAX(signal_date) AS signal_date
                        FROM m7_sell_signals
                        WHERE source = 'm7_nightly'
                        """
                    )
                row = cursor.fetchone() or {}
        finally:
            conn.close()

        cnt = int(row.get("c") or 0)
        max_updated_raw = row.get("max_updated")
        max_updated = _db_value_to_datetime(max_updated_raw) or max_updated_raw
        in_window = (
            isinstance(max_updated, datetime)
            and (started_at - timedelta(seconds=30)) <= max_updated <= (finished_at + timedelta(seconds=300))
        )
        if cnt <= 0:
            # 无卖出单也可判定任务成功，避免空集导致夜间任务误报失败
            lines.append("result=PASS; rows=0; reason=no_sell_signal_generated")
            return True, lines

        ok = bool(in_window)
        lines.append(
            "result="
            + ("PASS" if ok else "FAIL")
            + f"; rows={cnt}; max_updated={max_updated or '-'}; in_window={in_window}"
        )
        return ok, lines
    except Exception as e:
        return False, [f"result=FAIL; verifier_error={e}"]


def _run_task_result_verification(task_name, started_at, finished_at, run_options=None):
    if task_name == "sina_score":
        return _verify_sina_score_result(started_at, finished_at, run_options=run_options)
    if task_name == "sina_bs_consensus":
        return _verify_sina_bs_consensus_result(started_at, finished_at, run_options=run_options)
    if task_name == "sina_m8":
        return _verify_sina_m8_result(started_at, finished_at)
    if task_name == "sina_m7_sell":
        return _verify_sina_m7_sell_result(started_at, finished_at, run_options=run_options)
    return None, [f"result=SKIP; no verifier for task={task_name}"]


def _table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _fmt_money(value):
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct_value(value):
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


def _build_sina_analyse_notification(cursor, run_options=None):
    if not _table_exists(cursor, "bs_detection_results"):
        return "分析结果表 `bs_detection_results` 不存在，无法生成统计摘要。"

    datestr = _normalize_datestr((run_options or {}).get("datestr"))
    target_date = None
    if datestr:
        target_date = datetime.strptime(datestr, "%Y%m%d").date()
    else:
        cursor.execute("SELECT MAX(batch_date) AS d FROM bs_detection_results")
        target_date = (cursor.fetchone() or {}).get("d")

    if target_date is None:
        return "未找到可用的 B/S 分析结果。"

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_cnt,
            SUM(CASE WHEN has_buy_signal = 1 THEN 1 ELSE 0 END) AS buy_cnt,
            SUM(CASE WHEN has_sell_signal = 1 THEN 1 ELSE 0 END) AS sell_cnt,
            SUM(CASE WHEN has_buy_signal = 1 AND has_sell_signal = 1 THEN 1 ELSE 0 END) AS dual_cnt,
            MAX(created_at) AS updated_at
        FROM bs_detection_results
        WHERE batch_date = %s
        """,
        (target_date,),
    )
    stats = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT stock_code
        FROM bs_detection_results
        WHERE batch_date = %s AND has_buy_signal = 1
        ORDER BY stock_code ASC
        LIMIT 8
        """,
        (target_date,),
    )
    buy_codes = [str(r.get("stock_code") or "").zfill(6) for r in cursor.fetchall() if r.get("stock_code")]

    cursor.execute(
        """
        SELECT stock_code
        FROM bs_detection_results
        WHERE batch_date = %s AND has_sell_signal = 1
        ORDER BY stock_code ASC
        LIMIT 8
        """,
        (target_date,),
    )
    sell_codes = [str(r.get("stock_code") or "").zfill(6) for r in cursor.fetchall() if r.get("stock_code")]

    target_datestr = _db_value_to_datestr(target_date) or str(target_date)
    return "\n".join(
        [
            f"分析日期：{target_datestr}",
            f"覆盖股票：{int(stats.get('total_cnt') or 0)}",
            f"买点信号：{int(stats.get('buy_cnt') or 0)}",
            f"卖点信号：{int(stats.get('sell_cnt') or 0)}",
            f"双向信号：{int(stats.get('dual_cnt') or 0)}",
            f"数据更新时间：{stats.get('updated_at') or '-'}",
            f"买点示例：{', '.join(buy_codes) if buy_codes else '无'}",
            f"卖点示例：{', '.join(sell_codes) if sell_codes else '无'}",
        ]
    )


def _build_sina_m8_notification(cursor, run_options=None):
    if not _table_exists(cursor, "strategy_m8_runs"):
        return "M8 结果表 `strategy_m8_runs` 不存在，无法生成摘要。"

    datestr = _normalize_datestr((run_options or {}).get("datestr"))
    if datestr:
        target_date = datetime.strptime(datestr, "%Y%m%d").date()
        cursor.execute(
            """
            SELECT id, as_of_date, lookback_dates, sample_rows, eligible_rows, searched_total, status, created_at
            FROM strategy_m8_runs
            WHERE as_of_date = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_date,),
        )
    else:
        cursor.execute(
            """
            SELECT id, as_of_date, lookback_dates, sample_rows, eligible_rows, searched_total, status, created_at
            FROM strategy_m8_runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
    run = cursor.fetchone() or {}

    run_id = int(run.get("id") or 0)
    if run_id <= 0:
        return "未找到可用的 M8 运行记录。"

    item_cnt = 0
    if _table_exists(cursor, "strategy_m8_items"):
        cursor.execute("SELECT COUNT(*) AS c FROM strategy_m8_items WHERE run_id = %s", (run_id,))
        item_cnt = int((cursor.fetchone() or {}).get("c") or 0)

    winners = []
    if _table_exists(cursor, "strategy_m8_items"):
        cursor.execute(
            """
            SELECT strategy, avg_ret_10, hit_10, rank_no
            FROM strategy_m8_items
            WHERE run_id = %s AND item_type = 'M3'
            ORDER BY rank_no ASC
            LIMIT 3
            """,
            (run_id,),
        )
        winners = cursor.fetchall() or []

    as_of_date = run.get("as_of_date")
    rebalance_line = "今日调仓结果（卖出侧）：暂无 m7_sell_signals 数据"
    if as_of_date and _table_exists(cursor, "m7_sell_signals"):
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                SUM(
                    CASE
                        WHEN COALESCE(reason_code, '') IN ('BS_REVERSAL', 'HARD_STOP', 'LIMIT_DOWN_EXIT', 'TRAILING_STOP', 'TIME_STOP', 'SCORE_EXIT')
                             OR sell_signal = 'FORCED_EXIT'
                        THEN 1 ELSE 0
                    END
                ) AS forced_rows,
                SUM(CASE WHEN COALESCE(reason_code, '') = 'REBALANCE_SELL' OR sell_signal = 'REBALANCE' THEN 1 ELSE 0 END) AS rebalance_rows,
                SUM(CASE WHEN COALESCE(pending_flag, 0) = 1 OR UPPER(COALESCE(exec_status, '')) = 'PENDING' THEN 1 ELSE 0 END) AS pending_rows,
                SUM(COALESCE(notional, 0)) AS total_notional
            FROM m7_sell_signals
            WHERE signal_date = %s
            """,
            (as_of_date,),
        )
        rb = cursor.fetchone() or {}
        rebalance_line = (
            "今日调仓结果（卖出侧）："
            f"卖出单 {int(rb.get('total_rows') or 0)}，"
            f"强制卖出 {int(rb.get('forced_rows') or 0)}，"
            f"再平衡卖出 {int(rb.get('rebalance_rows') or 0)}，"
            f"挂起 {int(rb.get('pending_rows') or 0)}，"
            f"预计卖出额 {_fmt_money(rb.get('total_notional') or 0)}"
        )

    winner_lines = []
    for row in winners:
        winner_lines.append(
            f"- {row.get('strategy') or '-'}: ret10={_fmt_pct_value(row.get('avg_ret_10'))}, hit10={_fmt_pct_value(row.get('hit_10'))}"
        )

    return "\n".join(
        [
            f"M8运行ID：{run_id}",
            f"评估日期：{_db_value_to_datestr(run.get('as_of_date')) or run.get('as_of_date') or '-'}",
            f"状态：{run.get('status') or '-'}",
            f"lookback_dates：{int(run.get('lookback_dates') or 0)}",
            f"样本行数：{int(run.get('sample_rows') or 0)}",
            f"可交易样本：{int(run.get('eligible_rows') or 0)}",
            f"搜索组合数：{int(run.get('searched_total') or 0)}",
            f"结果条目数：{item_cnt}",
            f"运行时间：{run.get('created_at') or '-'}",
            ("M3冠军参数：" if winner_lines else "M3冠军参数：无"),
            *winner_lines,
            rebalance_line,
        ]
    )


def _build_sina_snapshot_notification(cursor, run_options=None):
    if not _table_exists(cursor, "live_daily_snapshots"):
        return "快照表 `live_daily_snapshots` 不存在，无法生成实盘总结。"

    datestr = _normalize_datestr((run_options or {}).get("datestr"))
    snapshot = None
    if datestr:
        target_date = datetime.strptime(datestr, "%Y%m%d").date()
        cursor.execute(
            """
            SELECT snapshot_date, cash, positions_value, total_equity, daily_pnl, daily_return_pct, csi300_return_pct, excess_return_pct
            FROM live_daily_snapshots
            WHERE snapshot_date = %s
            LIMIT 1
            """,
            (target_date,),
        )
        snapshot = cursor.fetchone()
    if not snapshot:
        cursor.execute(
            """
            SELECT snapshot_date, cash, positions_value, total_equity, daily_pnl, daily_return_pct, csi300_return_pct, excess_return_pct
            FROM live_daily_snapshots
            ORDER BY snapshot_date DESC
            LIMIT 1
            """
        )
        snapshot = cursor.fetchone()

    if not snapshot:
        return "未找到可用的实盘快照记录。"

    pos_cnt = 0
    if _table_exists(cursor, "live_positions"):
        cursor.execute("SELECT COUNT(*) AS c FROM live_positions")
        pos_cnt = int((cursor.fetchone() or {}).get("c") or 0)

    trade_date = snapshot.get("snapshot_date")
    buy_cnt = sell_cnt = 0
    buy_amt = sell_amt = 0.0
    if trade_date and _table_exists(cursor, "live_trades"):
        cursor.execute(
            """
            SELECT direction, COUNT(*) AS cnt, SUM(amount) AS amt
            FROM live_trades
            WHERE trade_date = %s
            GROUP BY direction
            """,
            (trade_date,),
        )
        for row in cursor.fetchall() or []:
            direction = str(row.get("direction") or "").lower()
            if direction == "buy":
                buy_cnt = int(row.get("cnt") or 0)
                buy_amt = float(row.get("amt") or 0.0)
            elif direction == "sell":
                sell_cnt = int(row.get("cnt") or 0)
                sell_amt = float(row.get("amt") or 0.0)

    return "\n".join(
        [
            f"快照日期：{_db_value_to_datestr(snapshot.get('snapshot_date')) or snapshot.get('snapshot_date') or '-'}",
            f"总权益：{_fmt_money(snapshot.get('total_equity') or 0)}",
            f"现金：{_fmt_money(snapshot.get('cash') or 0)}",
            f"持仓市值：{_fmt_money(snapshot.get('positions_value') or 0)}",
            f"当日盈亏：{_fmt_money(snapshot.get('daily_pnl') or 0)}",
            f"当日收益率：{_fmt_pct_value(snapshot.get('daily_return_pct'))}",
            f"沪深300收益率：{_fmt_pct_value(snapshot.get('csi300_return_pct'))}",
            f"超额收益率：{_fmt_pct_value(snapshot.get('excess_return_pct'))}",
            f"当前持仓数：{pos_cnt}",
            f"当日成交：买入 {buy_cnt} 笔 / {_fmt_money(buy_amt)}，卖出 {sell_cnt} 笔 / {_fmt_money(sell_amt)}",
        ]
    )


def _build_task_completion_notification(task_name, trigger_type, started_at, finished_at, run_options=None):
    if task_name not in {"sina_analyse", "sina_m8", "sina_snapshot"}:
        return None

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            if task_name == "sina_analyse":
                detail = _build_sina_analyse_notification(cursor, run_options=run_options)
            elif task_name == "sina_m8":
                detail = _build_sina_m8_notification(cursor, run_options=run_options)
            else:
                detail = _build_sina_snapshot_notification(cursor, run_options=run_options)
    finally:
        conn.close()

    task_display_name = TASKS.get(task_name, {}).get("name") or task_name
    lines = [
        f"【任务完成】{task_display_name}",
        f"任务ID：{task_name}",
        f"触发方式：{trigger_type or '-'}",
        f"开始时间：{started_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(started_at, datetime) else started_at}",
        f"完成时间：{finished_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(finished_at, datetime) else finished_at}",
        "",
        str(detail or "无摘要信息。"),
    ]
    return "\n".join(lines)


def _build_channel_payload(channel_key, content):
    text = str(content or "")
    if channel_key == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if channel_key == "wechat":
        return {"msgtype": "markdown", "markdown": {"content": text}}
    if channel_key == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": "任务完成通知", "text": text}}
    return {"text": text, "content": text}


def _is_webhook_response_ok(status_code, body):
    if status_code < 200 or status_code >= 300:
        return False, f"http_status={status_code}"
    try:
        parsed = json.loads(body) if body else {}
    except Exception:
        return True, "http_ok"
    if not isinstance(parsed, dict):
        return True, "http_ok"
    errcode = parsed.get("errcode")
    code = parsed.get("code")
    status_code_field = parsed.get("StatusCode")
    if errcode not in (None, 0, "0"):
        return False, f"errcode={errcode}"
    if code not in (None, 0, "0"):
        return False, f"code={code}"
    if status_code_field not in (None, 0, "0"):
        return False, f"StatusCode={status_code_field}"
    return True, "ok"


def _post_channel_webhook(channel_key, webhook_url, content):
    payload = _build_channel_payload(channel_key, content)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
            ok, reason = _is_webhook_response_ok(status, body)
            return ok, reason
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        return False, f"http_error={e.code}; body={body[:200]}"
    except Exception as e:
        return False, f"exception={e}"


def _dispatch_task_notification(content):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            channels = _load_notification_channels_from_cursor(cursor)
    finally:
        conn.close()

    enabled_channels = [
        row for row in channels
        if int(row.get("enabled") or 0) == 1 and _normalize_webhook_url(row.get("webhook_url"))
    ]
    if not enabled_channels:
        print("Task notification skipped: no enabled webhook channels configured.")
        return

    success_cnt = 0
    fail_logs = []
    for row in enabled_channels:
        channel_key = row.get("channel_key")
        webhook_url = _normalize_webhook_url(row.get("webhook_url"))
        if not webhook_url:
            continue
        ok, reason = _post_channel_webhook(channel_key, webhook_url, content)
        if ok:
            success_cnt += 1
        else:
            fail_logs.append(f"{channel_key}:{reason}")
    print(
        f"Task notification dispatched: success={success_cnt}/{len(enabled_channels)}"
        + (f"; failures={' | '.join(fail_logs)}" if fail_logs else "")
    )


def _send_task_completion_notification(task_name, history_status, trigger_type, started_at, finished_at, run_options=None):
    if history_status != "Success":
        return
    if task_name not in {"sina_analyse", "sina_m8", "sina_snapshot"}:
        return
    try:
        content = _build_task_completion_notification(
            task_name=task_name,
            trigger_type=trigger_type,
            started_at=started_at,
            finished_at=finished_at,
            run_options=run_options,
        )
        if not content:
            return
        _dispatch_task_notification(content)
    except Exception as e:
        print(f"Task notification error ({task_name}): {e}")


def _execute_locked_task(task_name, trigger_type, run_options=None):
    started_at = datetime.now()
    project_root = Path(app.root_path).parent

    with TASKS_LOCK:
        TASKS[task_name]["status"] = "Running..."
        TASKS[task_name]["last_run"] = started_at.strftime("%Y-%m-%d %H:%M:%S")
        TASKS[task_name]["error_log"] = ""
    update_task_db(task_name)

    history_status = "Success"
    lock_status = "COMPLETE"
    exit_code = 0
    message = None
    stdout = ""
    stderr = ""
    stdout_path = None
    stderr_path = None

    try:
        script_parts = _build_task_script_parts(task_name, run_options=run_options)
        script_abs_path = project_root / script_parts[0]
        cmd = [sys.executable, str(script_abs_path)] + script_parts[1:]
        env = _build_task_subprocess_env(task_name, project_root)

        with tempfile.NamedTemporaryFile(mode="wb+", delete=False, prefix=f"{task_name}_", suffix=".stdout.log") as out_fh, \
                tempfile.NamedTemporaryFile(mode="wb+", delete=False, prefix=f"{task_name}_", suffix=".stderr.log") as err_fh:
            stdout_path = out_fh.name
            stderr_path = err_fh.name
            process = subprocess.Popen(
                cmd,
                stdout=out_fh,
                stderr=err_fh,
                cwd=str(project_root),
                env=env,
            )

            while True:
                try:
                    process.wait(timeout=TASK_HEARTBEAT_INTERVAL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    _touch_task_lock_heartbeat(task_name)

        exit_code = int(process.returncode or 0)
        stdout = _read_text_tail(stdout_path, max_chars=4000)
        stderr = _read_text_tail(stderr_path, max_chars=2400)
        if exit_code == 0:
            verify_finished_at = datetime.now()
            verify_ok, verify_lines = _run_task_result_verification(
                task_name=task_name,
                started_at=started_at,
                finished_at=verify_finished_at,
                run_options=run_options,
            )
            message = _format_task_message(stdout, stderr, verify_lines)

            if verify_ok is False:
                history_status = "Failed"
                lock_status = "FAILED"
                with TASKS_LOCK:
                    TASKS[task_name]["status"] = "Failed (Verification)"
                    TASKS[task_name]["error_log"] = (message or "verification failed")[-500:]
            else:
                with TASKS_LOCK:
                    TASKS[task_name]["status"] = "Success"
                    TASKS[task_name]["switched_day"] = True
        else:
            history_status = "Failed"
            lock_status = "FAILED"
            with TASKS_LOCK:
                TASKS[task_name]["status"] = f"Failed (Code {exit_code})"
                TASKS[task_name]["error_log"] = (stderr or "")[-500:]
            message = _format_task_message(stdout, stderr, [f"result=FAIL; exit_code={exit_code}"])
    except Exception as e:
        history_status = "Error"
        lock_status = "ERROR"
        exit_code = -1
        message = _format_task_message(stdout, stderr, [f"result=FAIL; exception={e}"])
        with TASKS_LOCK:
            TASKS[task_name]["status"] = f"Error: {str(e)}"
            TASKS[task_name]["error_log"] = str(e)[:500]
    finally:
        for path in (stdout_path, stderr_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        update_task_db(task_name)
        finished_at = datetime.now()
        duration_seconds = int((finished_at - started_at).total_seconds())
        _insert_task_history(
            task_name=task_name,
            trigger_type=trigger_type,
            status=history_status,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=exit_code,
            duration_seconds=duration_seconds,
            message=message,
        )
        _send_task_completion_notification(
            task_name=task_name,
            history_status=history_status,
            trigger_type=trigger_type,
            started_at=started_at,
            finished_at=finished_at,
            run_options=run_options,
        )
        _mark_task_lock_finished(task_name, lock_status, message or lock_status)


def _build_task_subprocess_env(task_name, project_root):
    _ = task_name
    return build_direct_network_env(os.environ, pythonpath_prefix=str(project_root))


def _trigger_task_execution(task_name, trigger_type="manual", run_options=None):
    acquired, reason = _try_acquire_task_lock(task_name, trigger_type=trigger_type)
    if not acquired:
        return False, reason

    try:
        thread = threading.Thread(target=_execute_locked_task, args=(task_name, trigger_type, run_options))
        thread.daemon = True
        thread.start()
        return True, None
    except Exception as e:
        _mark_task_lock_finished(task_name, "ERROR", str(e))
        return False, str(e)


def _is_trading_day(target_date=None):
    target = target_date or datetime.now().date()
    ymd = target.strftime('%Y%m%d')

    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT is_open FROM chenyiyun.dim_trade_cal WHERE cal_date = %s AND exchange = 'SSE' LIMIT 1",
                (ymd,)
            )
            row = cursor.fetchone()
        conn.close()
        if row is not None:
            return int(row.get('is_open')) == 1
    except Exception as e:
        print(f"Trade calendar check failed (chenyiyun.dim_trade_cal): {e}")
    return False


def _mark_scheduled_non_trading_success(task_name, scheduled_for):
    started_at = scheduled_for or datetime.now().replace(second=0, microsecond=0)
    message = "result=SKIP; reason=NON_TRADING_DAY; source=chenyiyun.dim_trade_cal"
    with TASKS_LOCK:
        TASKS[task_name]["status"] = "Success (Non-trading day skip)"
        TASKS[task_name]["switched_day"] = True
        TASKS[task_name]["last_run"] = started_at.strftime("%Y-%m-%d %H:%M:%S")
        TASKS[task_name]["error_log"] = ""
    update_task_db(task_name)
    _insert_task_history(
        task_name=task_name,
        trigger_type="schedule",
        status="Success",
        started_at=started_at,
        finished_at=started_at,
        exit_code=0,
        duration_seconds=0,
        message=message,
    )


def _build_task_script_parts(task_name, run_options=None):
    task_config = TASKS[task_name]
    script = task_config['script']
    run_options = run_options or {}
    datestr = _normalize_datestr(run_options.get("datestr"))
    if task_name == 'sina_picture':
        target_date = datestr or datetime.now().strftime('%Y%m%d')
        return [script, 'config_1', target_date, '--capture-only']
    if task_name == 'sina_analyse':
        target_date = datestr or datetime.now().strftime('%Y%m%d')
        return [script, 'config_1', target_date, '--analyze-only']
    if task_name == 'sina_score':
        if datestr:
            return [script, '--date', datestr, '--force']
        return [script]
    if task_name == 'sina_bs_consensus':
        if datestr:
            return [script, '--date', datestr]
        return [script]
    if task_name == 'sina_m8':
        return [script, '--lookback-dates', '60']
    if task_name == 'sina_snapshot':
        if datestr and len(datestr) == 8:
            date_iso = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
            return [script, 'snapshot', '--date', date_iso]
        return [script, 'snapshot']
    if task_name == 'sina_m7_sell':
        if datestr and len(datestr) == 8:
            date_iso = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
            return [script, '--date', date_iso]
        return [script]
    if task_name == 'eastmoney':
        return [script, '--export', 'result']
    if task_name == 'chenyiyun_selected':
        if datestr and len(datestr) == 8:
            date_iso = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
            return [script, '--date', date_iso]
        return [script]
    if task_name in {'chenyiyun_weekly_rebalance', 'chenyiyun_limitup_check', 'chenyiyun_position_update'}:
        if datestr and len(datestr) == 8:
            date_iso = f"{datestr[:4]}-{datestr[4:6]}-{datestr[6:]}"
            return [script, '--date', date_iso]
        return [script]
    return [script]

def init_tasks():
    """Load task status from database"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            cursor.execute("SELECT * FROM app_task_status")
            rows = cursor.fetchall()
            with TASKS_LOCK:
                for row in rows:
                    name = row['task_name']
                    if name in TASKS:
                        TASKS[name]['last_run'] = row['last_run'].strftime('%Y-%m-%d %H:%M:%S') if row.get('last_run') else "Never"
                        TASKS[name]['status'] = row.get('status') or "Idle"
                        TASKS[name]['switched_day'] = bool(row.get('switched_day'))
                        if 'schedule_enabled' in row:
                            TASKS[name]['schedule_enabled'] = bool(row.get('schedule_enabled'))
                        if row.get('schedule_time'):
                            normalized = _normalize_schedule_time(row.get('schedule_time'))
                            if normalized:
                                TASKS[name]['schedule_time'] = normalized
                        if row.get('next_run'):
                            TASKS[name]['next_run'] = row['next_run'].strftime('%Y-%m-%d %H:%M:%S')
            conn.commit()
        conn.close()
        _refresh_task_next_runs()
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


def _ensure_chenyiyun_selected_settings_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chenyiyun_selected_settings (
            id TINYINT PRIMARY KEY,
            stock_count INT NOT NULL DEFAULT 10,
            position_ratio DOUBLE NOT NULL DEFAULT 1.0,
            holding_days INT NOT NULL DEFAULT 20,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )


def _normalize_chenyiyun_selected_settings(raw):
    stock_count = int(raw.get("stock_count") or DEFAULT_CHENYIYUN_SELECTED_SETTINGS["stock_count"])
    stock_count = max(1, min(50, stock_count))

    position_ratio = float(raw.get("position_ratio") or DEFAULT_CHENYIYUN_SELECTED_SETTINGS["position_ratio"])
    position_ratio = max(0.05, min(1.0, position_ratio))

    holding_days = int(raw.get("holding_days") or DEFAULT_CHENYIYUN_SELECTED_SETTINGS["holding_days"])
    holding_days = max(1, min(120, holding_days))

    return {
        "stock_count": stock_count,
        "position_ratio": position_ratio,
        "holding_days": holding_days,
    }


def _get_chenyiyun_selected_settings(conn):
    with conn.cursor() as cursor:
        _ensure_chenyiyun_selected_settings_table(cursor)
        cursor.execute(
            """
            SELECT stock_count, position_ratio, holding_days
            FROM chenyiyun_selected_settings
            WHERE id = 1
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            return _normalize_chenyiyun_selected_settings(row)

        defaults = _normalize_chenyiyun_selected_settings(DEFAULT_CHENYIYUN_SELECTED_SETTINGS)
        cursor.execute(
            """
            INSERT INTO chenyiyun_selected_settings (id, stock_count, position_ratio, holding_days)
            VALUES (1, %s, %s, %s)
            """,
            (defaults["stock_count"], defaults["position_ratio"], defaults["holding_days"]),
        )
        conn.commit()
        return defaults


def _save_chenyiyun_selected_settings(conn, stock_count, position_ratio, holding_days):
    settings = _normalize_chenyiyun_selected_settings(
        {
            "stock_count": stock_count,
            "position_ratio": position_ratio,
            "holding_days": holding_days,
        }
    )
    with conn.cursor() as cursor:
        _ensure_chenyiyun_selected_settings_table(cursor)
        cursor.execute(
            """
            INSERT INTO chenyiyun_selected_settings (id, stock_count, position_ratio, holding_days)
            VALUES (1, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stock_count = VALUES(stock_count),
                position_ratio = VALUES(position_ratio),
                holding_days = VALUES(holding_days)
            """,
            (settings["stock_count"], settings["position_ratio"], settings["holding_days"]),
        )
    conn.commit()
    return settings


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


def get_memory_pagination(total, page, per_page):
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 50))
    return {
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'has_prev': page > 1,
        'has_next': page * per_page < total,
        'prev_num': page - 1,
        'next_num': page + 1
    }


def _safe_sort_float(row, key, default=0.0):
    try:
        value = row.get(key)
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        return default


def _enrich_bs_score_rows(rows):
    for row in rows:
        row.update(calculate_bs_enhanced_score(row))
        row.update(calculate_bs_score_v2(row))
        row.update(calculate_bs_research_signal(row))
        row.update(calculate_bs_trade_gate(row))
        row.update(calculate_bs_consensus_signal(row))
    return rows


def _pool_sort_rank(row):
    return {'TRADE': 1, 'WATCH': 2}.get(row.get('pool_type'), 3)


def _ensure_score_rank_daily_score_columns(cursor):
    cursor.execute("SHOW COLUMNS FROM score_rank_daily")
    existing = {row["Field"] for row in cursor.fetchall()}
    additions = {
        "score_momentum": "ALTER TABLE score_rank_daily ADD COLUMN score_momentum DECIMAL(10,2) NULL COMMENT 'Claude动量子分' AFTER claude_score",
        "score_value": "ALTER TABLE score_rank_daily ADD COLUMN score_value DECIMAL(10,2) NULL COMMENT 'Claude估值子分' AFTER score_momentum",
        "score_quality": "ALTER TABLE score_rank_daily ADD COLUMN score_quality DECIMAL(10,2) NULL COMMENT 'Claude质量子分' AFTER score_value",
        "score_technical": "ALTER TABLE score_rank_daily ADD COLUMN score_technical DECIMAL(10,2) NULL COMMENT 'Claude技术子分' AFTER score_quality",
        "score_capital": "ALTER TABLE score_rank_daily ADD COLUMN score_capital DECIMAL(10,2) NULL COMMENT 'Claude资金子分' AFTER score_technical",
        "score_chip": "ALTER TABLE score_rank_daily ADD COLUMN score_chip DECIMAL(10,2) NULL COMMENT 'Claude筹码子分' AFTER score_capital",
        "opt_momentum": "ALTER TABLE score_rank_daily ADD COLUMN opt_momentum DECIMAL(10,4) NULL COMMENT 'Factor Optimizer动量分类分' AFTER opt_score",
        "opt_value": "ALTER TABLE score_rank_daily ADD COLUMN opt_value DECIMAL(10,4) NULL COMMENT 'Factor Optimizer估值分类分' AFTER opt_momentum",
        "opt_quality": "ALTER TABLE score_rank_daily ADD COLUMN opt_quality DECIMAL(10,4) NULL COMMENT 'Factor Optimizer质量分类分' AFTER opt_value",
        "opt_technical": "ALTER TABLE score_rank_daily ADD COLUMN opt_technical DECIMAL(10,4) NULL COMMENT 'Factor Optimizer技术分类分' AFTER opt_quality",
        "opt_capital": "ALTER TABLE score_rank_daily ADD COLUMN opt_capital DECIMAL(10,4) NULL COMMENT 'Factor Optimizer资金分类分' AFTER opt_technical",
        "opt_chip": "ALTER TABLE score_rank_daily ADD COLUMN opt_chip DECIMAL(10,4) NULL COMMENT 'Factor Optimizer筹码分类分' AFTER opt_capital",
        "opt_size": "ALTER TABLE score_rank_daily ADD COLUMN opt_size DECIMAL(10,4) NULL COMMENT 'Factor Optimizer规模分类分' AFTER opt_chip",
        "bs_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_score DECIMAL(10,2) NULL COMMENT 'B点增强分' AFTER claude_score",
        "bs_entry_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_entry_score DECIMAL(10,2) NULL COMMENT '买点后节奏分' AFTER bs_score",
        "bs_score_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_label VARCHAR(16) NULL COMMENT 'B点增强分标签' AFTER bs_entry_score",
        "bs_score_v2": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2 DECIMAL(10,2) NULL COMMENT 'B点增强分V2' AFTER bs_score_label",
        "bs_score_v2_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2_label VARCHAR(16) NULL COMMENT 'B点增强分V2分层' AFTER bs_score_v2",
        "bs_research_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_score DECIMAL(10,2) NULL COMMENT 'B点研究建议分' AFTER bs_score_v2_label",
        "bs_research_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_label VARCHAR(16) NULL COMMENT 'B点研究建议标签' AFTER bs_research_score",
        "bs_research_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_reason VARCHAR(128) NULL COMMENT 'B点研究建议原因' AFTER bs_research_label",
        "bs_gate_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_score DECIMAL(10,2) NULL COMMENT 'B点交易门禁分' AFTER bs_research_reason",
        "bs_gate_pass": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_pass TINYINT(1) NULL COMMENT 'B点交易门禁是否通过' AFTER bs_gate_score",
        "bs_gate_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_label VARCHAR(16) NULL COMMENT 'B点交易门禁标签' AFTER bs_gate_pass",
        "bs_gate_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_reason VARCHAR(128) NULL COMMENT 'B点交易门禁原因' AFTER bs_gate_label",
        "bs_model_prob": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_prob DECIMAL(10,6) NULL COMMENT 'B点模型20日命中概率' AFTER bs_research_reason",
        "bs_model_expected_mdd": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_expected_mdd DECIMAL(10,6) NULL COMMENT 'B点模型预期最大回撤' AFTER bs_model_prob",
        "bs_model_risk_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_risk_score DECIMAL(10,4) NULL COMMENT 'B点模型回撤风险分' AFTER bs_model_expected_mdd",
        "bs_model_rank_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_rank_score DECIMAL(10,4) NULL COMMENT 'B点模型综合排序分' AFTER bs_model_risk_score",
        "bs_model_version": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_version VARCHAR(32) NULL COMMENT 'B点模型版本' AFTER bs_model_rank_score",
        "bs_consensus_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_score DECIMAL(10,2) NULL COMMENT 'B点综合建议分' AFTER bs_model_version",
        "bs_consensus_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_label VARCHAR(16) NULL COMMENT 'B点综合建议标签' AFTER bs_consensus_score",
        "bs_consensus_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_reason VARCHAR(128) NULL COMMENT 'B点综合建议原因' AFTER bs_consensus_label",
        "market_hs300_pct_chg": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_pct_chg DECIMAL(10,4) NULL COMMENT '沪深300当日涨跌幅' AFTER bs_research_reason",
        "market_hs300_ret_5": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_ret_5 DECIMAL(10,6) NULL COMMENT '沪深300近5日收益' AFTER market_hs300_pct_chg",
        "market_hs300_ret_20": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_ret_20 DECIMAL(10,6) NULL COMMENT '沪深300近20日收益' AFTER market_hs300_ret_5",
        "market_scored_count": "ALTER TABLE score_rank_daily ADD COLUMN market_scored_count INT NULL COMMENT '当日评分股票数' AFTER market_hs300_ret_20",
        "market_bs_count": "ALTER TABLE score_rank_daily ADD COLUMN market_bs_count INT NULL COMMENT '当日B点候选数' AFTER market_scored_count",
        "market_bs_ratio": "ALTER TABLE score_rank_daily ADD COLUMN market_bs_ratio DECIMAL(10,6) NULL COMMENT '当日B点候选占比' AFTER market_bs_count",
        "market_limit_up_rate": "ALTER TABLE score_rank_daily ADD COLUMN market_limit_up_rate DECIMAL(10,6) NULL COMMENT '当日评分池涨停率' AFTER market_bs_ratio",
        "market_avg_score": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_score DECIMAL(10,4) NULL COMMENT '当日市场平均技术分' AFTER market_limit_up_rate",
        "market_avg_v2": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_v2 DECIMAL(10,4) NULL COMMENT '当日市场平均V2分' AFTER market_avg_score",
        "market_avg_research_score": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_research_score DECIMAL(10,4) NULL COMMENT '当日市场平均研究分' AFTER market_avg_v2",
        "market_avg_price_change": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_price_change DECIMAL(10,4) NULL COMMENT '当日买点后平均涨幅' AFTER market_avg_research_score",
        "market_regime": "ALTER TABLE score_rank_daily ADD COLUMN market_regime VARCHAR(16) NULL COMMENT '市场状态' AFTER market_avg_price_change",
    }
    for col, ddl in additions.items():
        if col not in existing:
            cursor.execute(ddl)

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
        cursor.execute("SELECT snapshot_date, total_equity FROM live_daily_snapshots ORDER BY snapshot_date ASC LIMIT 1")
        first_snapshot = cursor.fetchone()
        
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

            # Calculate cumulative return and annualized return from first snapshot.
            start_equity = float(first_snapshot.get('total_equity') or 0) if first_snapshot else 0
            start_date = first_snapshot.get('snapshot_date') if first_snapshot else None
            if start_equity > 0:
                growth = float(assets['total_equity']) / start_equity
                assets['cumulative_return_pct'] = round((growth - 1.0) * 100, 4)
                if start_date:
                    elapsed_days = (datetime.now().date() - start_date).days
                    if elapsed_days > 0 and growth > 0:
                        annualized = (growth ** (365.0 / elapsed_days) - 1.0) * 100
                        assets['annualized_return_pct'] = round(annualized, 4)
                    else:
                        assets['annualized_return_pct'] = round(assets['cumulative_return_pct'], 4)
                else:
                    assets['annualized_return_pct'] = 0
            else:
                assets['cumulative_return_pct'] = 0
                assets['annualized_return_pct'] = 0

    return render_template('positions.html', 
                           positions=positions, 
                           assets=assets,
                           pagination=pagination,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/sina/positions/adjust', methods=['POST'])
def adjust_positions():
    selected_symbols = [
        symbol.strip()
        for symbol in request.form.getlist('selected_symbols')
        if (symbol or '').strip()
    ]
    selected_symbols = list(dict.fromkeys(selected_symbols))
    return_page = request.form.get('return_page', type=int) or 1

    if not selected_symbols:
        flash('请先勾选需要调整的持仓。', 'warning')
        return redirect(url_for('positions', page=return_page))

    conn = get_db()
    updated_count = 0
    errors = []

    try:
        with conn.cursor() as cursor:
            for symbol in selected_symbols:
                shares_raw = (request.form.get(f'shares_{symbol}') or '').strip()
                avg_cost_raw = (request.form.get(f'avg_cost_{symbol}') or '').strip()

                if not shares_raw or not avg_cost_raw:
                    errors.append(f'{symbol} 数量或成本为空')
                    continue

                try:
                    shares = int(shares_raw)
                except ValueError:
                    errors.append(f'{symbol} 数量格式非法')
                    continue
                if shares <= 0:
                    errors.append(f'{symbol} 数量必须大于0')
                    continue

                try:
                    avg_cost = Decimal(avg_cost_raw)
                except InvalidOperation:
                    errors.append(f'{symbol} 成本格式非法')
                    continue
                if avg_cost <= 0:
                    errors.append(f'{symbol} 成本必须大于0')
                    continue

                cursor.execute(
                    "SELECT id FROM live_positions WHERE symbol = %s LIMIT 1",
                    (symbol,),
                )
                if not cursor.fetchone():
                    errors.append(f'{symbol} 持仓不存在')
                    continue

                cursor.execute(
                    """
                    UPDATE live_positions
                    SET shares = %s, avg_cost = %s
                    WHERE symbol = %s
                    """,
                    (shares, float(avg_cost), symbol),
                )
                updated_count += cursor.rowcount

        if updated_count > 0:
            conn.commit()
        else:
            conn.rollback()
    except Exception as e:
        conn.rollback()
        flash(f'持仓调整失败: {e}', 'danger')
        return redirect(url_for('positions', page=return_page))

    if updated_count > 0:
        flash(f'已更新 {updated_count} 条持仓。', 'success')
    if errors:
        preview = '；'.join(errors[:3])
        suffix = '；...' if len(errors) > 3 else ''
        flash(f'部分持仓未更新：{preview}{suffix}', 'warning')
    elif updated_count == 0:
        flash('未更新任何持仓。', 'warning')

    return redirect(url_for('positions', page=return_page))


@app.route('/sina/cash/adjust', methods=['POST'])
def adjust_cash():
    amount_raw = (request.form.get('cash_adjust_amount') or '').strip()
    return_page = request.form.get('return_page', type=int) or 1

    if not amount_raw:
        flash('请输入现金调整金额。', 'warning')
        return redirect(url_for('positions', page=return_page))

    try:
        amount = Decimal(amount_raw).quantize(Decimal('0.01'))
    except InvalidOperation:
        flash('现金调整金额格式非法。', 'warning')
        return redirect(url_for('positions', page=return_page))

    if amount == 0:
        flash('现金调整金额不能为0。', 'warning')
        return redirect(url_for('positions', page=return_page))

    conn = get_db()
    today = datetime.now().date()

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1")
            latest_snapshot = cursor.fetchone()
            if not latest_snapshot:
                flash('暂无账户快照，请先执行一次实盘快照同步。', 'danger')
                return redirect(url_for('positions', page=return_page))

            latest_date = latest_snapshot.get('snapshot_date')
            if isinstance(latest_date, datetime):
                latest_date = latest_date.date()

            if latest_date != today:
                cursor.execute(
                    """
                    INSERT INTO live_daily_snapshots
                    (snapshot_date, cash, positions_value, total_equity, daily_pnl, daily_return_pct, csi300_return_pct, excess_return_pct)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        cash = VALUES(cash),
                        positions_value = VALUES(positions_value),
                        total_equity = VALUES(total_equity),
                        daily_pnl = VALUES(daily_pnl),
                        daily_return_pct = VALUES(daily_return_pct),
                        csi300_return_pct = VALUES(csi300_return_pct),
                        excess_return_pct = VALUES(excess_return_pct)
                    """,
                    (
                        today,
                        latest_snapshot.get('cash'),
                        latest_snapshot.get('positions_value'),
                        latest_snapshot.get('total_equity'),
                        latest_snapshot.get('daily_pnl'),
                        latest_snapshot.get('daily_return_pct'),
                        latest_snapshot.get('csi300_return_pct'),
                        latest_snapshot.get('excess_return_pct'),
                    ),
                )

            cursor.execute(
                """
                SELECT snapshot_date, cash, positions_value
                FROM live_daily_snapshots
                WHERE snapshot_date = %s
                LIMIT 1
                """,
                (today,),
            )
            today_snapshot = cursor.fetchone()
            if not today_snapshot:
                conn.rollback()
                flash('未找到今日快照，现金调整失败。', 'danger')
                return redirect(url_for('positions', page=return_page))

            current_cash = Decimal(str(today_snapshot.get('cash') or 0)).quantize(Decimal('0.01'))
            positions_value = Decimal(str(today_snapshot.get('positions_value') or 0)).quantize(Decimal('0.01'))
            new_cash = (current_cash + amount).quantize(Decimal('0.01'))
            if new_cash < 0:
                conn.rollback()
                flash('现金调整后小于0，已拒绝更新。', 'warning')
                return redirect(url_for('positions', page=return_page))

            new_total_equity = (new_cash + positions_value).quantize(Decimal('0.01'))

            cursor.execute(
                """
                UPDATE live_daily_snapshots
                SET cash = %s, total_equity = %s
                WHERE snapshot_date = %s
                """,
                (str(new_cash), str(new_total_equity), today),
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f'现金调整失败: {e}', 'danger')
        return redirect(url_for('positions', page=return_page))

    sign = '+' if amount > 0 else ''
    flash(
        f'现金调整成功：{today.strftime("%Y-%m-%d")} {sign}{amount:.2f}，当前现金 {new_cash:.2f}。',
        'success',
    )
    return redirect(url_for('positions', page=return_page))



@app.route('/chenyiyun/selected')
def chenyiyun_selected_dashboard():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    scope = str(request.args.get('scope') or 'latest').lower()
    if scope not in {'latest', 'all'}:
        scope = 'latest'
    side_filter = str(request.args.get('side') or 'BUY').upper()
    if side_filter not in {'BUY', 'SELL', 'ALL'}:
        side_filter = 'BUY'
    signals = []
    positions = []
    strategy_settings = _normalize_chenyiyun_selected_settings(DEFAULT_CHENYIYUN_SELECTED_SETTINGS)
    latest_snapshot_equity = None
    latest_signal_trade_date = None
    signal_stats = {
        "total_rows": 0,
        "unique_codes": 0,
        "buy_rows": 0,
        "sell_rows": 0,
    }
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
        strategy_settings = _get_chenyiyun_selected_settings(conn)
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

            cursor.execute("SELECT MAX(trade_date) AS d FROM ads_chenyiyun_selected_signals")
            latest_signal_trade_date = (cursor.fetchone() or {}).get("d")

            where_clauses = []
            params = []
            if scope == 'latest' and latest_signal_trade_date is not None:
                where_clauses.append("trade_date = %s")
                params.append(latest_signal_trade_date)
            if side_filter in {'BUY', 'SELL'}:
                where_clauses.append("side = %s")
                params.append(side_filter)
            where_stmt = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            pagination, offset = get_pagination(
                cursor,
                "ads_chenyiyun_selected_signals",
                page,
                per_page,
                where_stmt,
                tuple(params),
            )
            cursor.execute(
                f"""
                SELECT signal_time, trade_date, ts_code, stock_name, side, open_price, allocated_shares, current_shares, target_shares
                FROM ads_chenyiyun_selected_signals
                {where_stmt}
                ORDER BY signal_time DESC, ts_code ASC
                LIMIT %s OFFSET %s
                """,
                tuple(params + [per_page, offset]),
            )
            signals = cursor.fetchall()

            if latest_signal_trade_date is not None:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_rows,
                        COUNT(DISTINCT ts_code) AS unique_codes,
                        SUM(side='BUY') AS buy_rows,
                        SUM(side='SELL') AS sell_rows
                    FROM ads_chenyiyun_selected_signals
                    WHERE trade_date = %s
                    """,
                    (latest_signal_trade_date,),
                )
                stat_row = cursor.fetchone() or {}
                signal_stats = {
                    "total_rows": int(stat_row.get("total_rows") or 0),
                    "unique_codes": int(stat_row.get("unique_codes") or 0),
                    "buy_rows": int(stat_row.get("buy_rows") or 0),
                    "sell_rows": int(stat_row.get("sell_rows") or 0),
                }

            cursor.execute(
                """
                SELECT symbol AS ts_code, name AS stock_name, entry_date, avg_cost, current_price, shares
                FROM live_positions
                ORDER BY id DESC
                """
            )
            positions = cursor.fetchall()

            cursor.execute("SELECT total_equity FROM live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1")
            snapshot_row = cursor.fetchone() or {}
            latest_snapshot_equity = snapshot_row.get("total_equity")
    except Exception as e:
        flash(f"陈依云精选策略页面数据库不可用: {e}", "danger")

    return render_template(
        'chenyiyun_selected.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        signals=signals,
        positions=positions,
        pagination=pagination,
        strategy_settings=strategy_settings,
        latest_snapshot_equity=latest_snapshot_equity,
        latest_signal_trade_date=latest_signal_trade_date,
        signal_stats=signal_stats,
        scope=scope,
        side_filter=side_filter,
    )


@app.route('/chenyiyun/selected/settings', methods=['POST'])
def update_chenyiyun_selected_settings():
    stock_count_raw = request.form.get('stock_count')
    position_ratio_pct_raw = request.form.get('position_ratio_pct')
    holding_days_raw = request.form.get('holding_days')
    try:
        stock_count = int(stock_count_raw)
        position_ratio_pct = float(position_ratio_pct_raw)
        holding_days = int(holding_days_raw)
    except (TypeError, ValueError):
        flash("参数格式非法，请检查仓位比例、股票个数和持有天数。", "danger")
        return redirect(url_for('chenyiyun_selected_dashboard'))

    position_ratio = position_ratio_pct / 100.0
    try:
        conn = get_db()
        settings = _save_chenyiyun_selected_settings(conn, stock_count, position_ratio, holding_days)
        flash(
            (
                f"陈依云精选参数已更新：股票个数={settings['stock_count']}，"
                f"仓位比例={settings['position_ratio'] * 100:.1f}% ，"
                f"持有天数={settings['holding_days']}。"
            ),
            "success",
        )
    except Exception as e:
        flash(f"保存陈依云精选参数失败: {e}", "danger")
    return redirect(url_for('chenyiyun_selected_dashboard'))
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
    sort_by = request.args.get('sort', 'default')  # default, bs_score, score, opt_score
    order = request.args.get('order', 'desc').upper()
    if order not in ['ASC', 'DESC']: order = 'DESC'
    
    min_score = request.args.get('min_s', type=float)
    min_opt_score = request.args.get('min_o', type=float)
    
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in [50, 100, 200]: per_page = 50

    conn = get_db()
    
    with conn.cursor() as cursor:
        _ensure_score_rank_daily_score_columns(cursor)
        # Get latest date
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_date = res['max_date']
        
        scores = []
        pagination = None
        
        if latest_date:
            where_clauses = ["trade_date = %s", "is_bs_candidate = 1"]
            params = [latest_date]
            
            if min_score is not None:
                where_clauses.append("score >= %s")
                params.append(min_score)
            if min_opt_score is not None:
                where_clauses.append("opt_score >= %s")
                params.append(min_opt_score)
                
            where_stmt = " WHERE " + " AND ".join(where_clauses)
            
            sql = f"""
            SELECT 
                *, 
                COALESCE(opt_score, 0) as opt_score,
                COALESCE(claude_score, 0) as claude_score 
            FROM score_rank_daily 
            {where_stmt}
            """
            
            cursor.execute(sql, tuple(params))
            all_scores = _enrich_bs_score_rows(cursor.fetchall())

            reverse = order == 'DESC'
            if sort_by in {'bs_consensus_score', 'bs_model_rank_score', 'bs_model_prob', 'bs_model_risk_score', 'bs_research_score', 'bs_score_v2', 'bs_score', 'score', 'opt_score', 'claude_score'}:
                all_scores.sort(key=lambda row: _safe_sort_float(row, sort_by), reverse=reverse)
            else:
                all_scores.sort(
                    key=lambda row: (
                        _pool_sort_rank(row),
                        -_safe_sort_float(row, 'bs_consensus_score'),
                        -_safe_sort_float(row, 'bs_research_score'),
                        -_safe_sort_float(row, 'bs_score_v2'),
                        -_safe_sort_float(row, 'bs_score'),
                        -_safe_sort_float(row, 'score'),
                    )
                )

            pagination = get_memory_pagination(len(all_scores), page, per_page)
            offset = (pagination['page'] - 1) * per_page
            scores = all_scores[offset: offset + per_page]

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           sort_by=sort_by,
                           order=order,
                           min_s=min_score,
                           min_o=min_opt_score,
                           symbol='',
                           per_page=per_page,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           page_title="最近有B点股票评分")

@app.route('/sina/scores/all')
def sina_all_scores():
    pool_id = request.args.get('pool_id', 'all')
    page = request.args.get('page', 1, type=int)
    sort_by = request.args.get('sort', 'default')  # default, score, opt_score
    order = request.args.get('order', 'desc').upper()
    if order not in ['ASC', 'DESC']: order = 'DESC'
    
    min_score = request.args.get('min_s', type=float)
    min_opt_score = request.args.get('min_o', type=float)
    symbol_input = (request.args.get('symbol') or '').strip()
    symbol_filters = []
    if symbol_input:
        tokens = (
            symbol_input
            .replace('，', ',')
            .replace('；', ',')
            .replace(';', ',')
            .replace(' ', ',')
            .split(',')
        )
        for token in tokens:
            t = token.strip()
            if not t:
                continue
            if t.isdigit():
                symbol_filters.append(t.zfill(6))
                continue
            if '.' in t:
                code_part = t.split('.', 1)[0]
                if code_part.isdigit():
                    symbol_filters.append(code_part.zfill(6))
                    continue
            symbol_filters.append(t)
        symbol_filters = list(dict.fromkeys(symbol_filters))
    
    per_page = request.args.get('per_page', 50, type=int)
    if per_page not in [50, 100, 200]: per_page = 50

    conn = get_db()
    
    with conn.cursor() as cursor:
        _ensure_score_rank_daily_score_columns(cursor)
        _ensure_stock_pool_schema(cursor)
        _ensure_seed_pools(cursor)
        cursor.execute(
            """
            SELECT id, pool_name, pool_key
            FROM stock_pools
            ORDER BY is_system DESC, id ASC
            """
        )
        stock_pools = cursor.fetchall()

        if pool_id != 'all' and str(pool_id).isdigit():
            valid_pool_ids = [str(p['id']) for p in stock_pools]
            if str(pool_id) not in valid_pool_ids:
                pool_id = 'all'

        # Get latest date
        cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
        res = cursor.fetchone()
        latest_date = res['max_date']
        
        scores = []
        pagination = None
        
        if latest_date:
            where_clauses = ["srd.trade_date = %s"]
            params = [latest_date]
            
            if min_score is not None:
                where_clauses.append("srd.score >= %s")
                params.append(min_score)
            if min_opt_score is not None:
                where_clauses.append("srd.opt_score >= %s")
                params.append(min_opt_score)
            if symbol_filters:
                if len(symbol_filters) == 1:
                    where_clauses.append("srd.symbol = %s")
                    params.append(symbol_filters[0])
                else:
                    placeholders = ",".join(["%s"] * len(symbol_filters))
                    where_clauses.append(f"srd.symbol IN ({placeholders})")
                    params.extend(symbol_filters)
                
            where_stmt = " WHERE " + " AND ".join(where_clauses)
            
            # Build ORDER BY
            if sort_by == 'score':
                order_stmt = f"ORDER BY srd.score {order}"
            elif sort_by == 'opt_score':
                order_stmt = f"ORDER BY srd.opt_score {order}"
            elif sort_by == 'claude_score':
                order_stmt = f"ORDER BY srd.claude_score {order}"
            elif sort_by == 'bs_score':
                order_stmt = f"ORDER BY srd.bs_score {order}"
            elif sort_by == 'bs_score_v2':
                order_stmt = f"ORDER BY srd.bs_score_v2 {order}"
            elif sort_by == 'bs_research_score':
                order_stmt = f"ORDER BY srd.bs_research_score {order}"
            elif sort_by == 'bs_consensus_score':
                order_stmt = f"ORDER BY srd.bs_consensus_score {order}"
            elif sort_by == 'bs_model_rank_score':
                order_stmt = f"ORDER BY srd.bs_model_rank_score {order}"
            elif sort_by == 'bs_model_prob':
                order_stmt = f"ORDER BY srd.bs_model_prob {order}"
            elif sort_by == 'bs_model_risk_score':
                order_stmt = f"ORDER BY srd.bs_model_risk_score {order}"
            else:
                order_stmt = "ORDER BY srd.bs_research_score DESC, srd.bs_score_v2 DESC, srd.score DESC"

            join_stmt = ""
            if pool_id != 'all':
                join_stmt = "INNER JOIN stock_pool_items spi ON srd.symbol = spi.symbol AND spi.pool_id = %s"
                count_sql = f"SELECT COUNT(*) as total FROM score_rank_daily srd {join_stmt} {where_stmt}"
                count_params = [int(pool_id)] + params
                cursor.execute(count_sql, tuple(count_params))
                total = cursor.fetchone()['total']
                offset = (page - 1) * per_page
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
                params = [int(pool_id)] + params
            else:
                pagination, offset = get_pagination(cursor, "score_rank_daily srd", page, per_page, where_stmt, tuple(params))

            sql = f"""
            SELECT 
                srd.*, 
                COALESCE(srd.opt_score, 0) as opt_score,
                COALESCE(srd.claude_score, 0) as claude_score 
            FROM score_rank_daily srd
            {join_stmt}
            {where_stmt}
            {order_stmt}
            LIMIT %s OFFSET %s
            """
            
            final_params = params + [per_page, offset]
            cursor.execute(sql, tuple(final_params))
            scores = _enrich_bs_score_rows(cursor.fetchall())

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           sort_by=sort_by,
                           order=order,
                           min_s=min_score,
                           min_o=min_opt_score,
                           symbol=symbol_input,
                           per_page=per_page,
                           pool_id=pool_id,
                           stock_pools=stock_pools,
                           is_all_scores_page=True,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           page_title="系统全量评分")

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
        _ensure_score_rank_daily_score_columns(cursor)
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
            elif sort_by == 'bs_score':
                order_stmt = f"ORDER BY bs_score {order}"
            elif sort_by == 'bs_score_v2':
                order_stmt = f"ORDER BY bs_score_v2 {order}"
            elif sort_by == 'bs_research_score':
                order_stmt = f"ORDER BY bs_research_score {order}"
            elif sort_by == 'bs_consensus_score':
                order_stmt = f"ORDER BY bs_consensus_score {order}"
            elif sort_by == 'bs_model_rank_score':
                order_stmt = f"ORDER BY bs_model_rank_score {order}"
            elif sort_by == 'bs_model_prob':
                order_stmt = f"ORDER BY bs_model_prob {order}"
            elif sort_by == 'bs_model_risk_score':
                order_stmt = f"ORDER BY bs_model_risk_score {order}"
            else:
                order_stmt = "ORDER BY bs_research_score DESC, bs_score_v2 DESC, score DESC"

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
            scores = _enrich_bs_score_rows(cursor.fetchall())

    return render_template('scores.html', 
                           scores=scores, 
                           pagination=pagination,
                           date=latest_date,
                           sort_by=sort_by,
                           order=order,
                           min_s=min_score,
                           min_o=min_opt_score,
                           symbol='',
                           per_page=per_page,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           page_title="自选股评分")

@app.route('/sina/tech_score')
def sina_tech_score():
    symbol_input = (request.args.get('symbol') or '').strip()
    pool_id = request.args.get('pool_id', type=int)

    latest_date = None
    rows = []
    pools = []
    selected_symbols = []

    weighted_profile = DEFAULT_PARAMS['weighted_profile']
    profile_a, profile_b, profile_c = WEIGHTED_PROFILES[weighted_profile]

    try:
        conn = get_db()
    except Exception as e:
        print(f"Failed to connect DB in sina_tech_score: {e}")
        conn = None

    if conn:
        with conn.cursor() as cursor:
            try:
                _ensure_stock_pool_schema(cursor)
                _ensure_seed_pools(cursor)
                cursor.execute("SELECT id, pool_name FROM stock_pools ORDER BY is_system DESC, id ASC")
                pools = cursor.fetchall()
            except Exception as e:
                print(f"Failed to load pools in sina_tech_score: {e}")

            token_symbols = []
            if symbol_input:
                tokens = symbol_input.replace('，', ',').replace(';', ',').replace('；', ',').replace(' ', ',').split(',')
                for token in tokens:
                    t = token.strip()
                    if not t:
                        continue
                    if t.isdigit():
                        token_symbols.append(t.zfill(6))
                    else:
                        cursor.execute(
                            "SELECT stock_code FROM a_share_stock_list WHERE stock_name = %s LIMIT 1",
                            (t,),
                        )
                        row = cursor.fetchone()
                        if row and row.get('stock_code'):
                            token_symbols.append(str(row['stock_code']).zfill(6))

            pool_symbols = []
            if pool_id:
                cursor.execute("SELECT symbol FROM stock_pool_items WHERE pool_id = %s", (pool_id,))
                pool_symbols = [str(r.get('symbol') or '').zfill(6) for r in cursor.fetchall() if r.get('symbol')]

            selected_symbols = sorted({x for x in (token_symbols + pool_symbols) if x and len(x) == 6})

            cursor.execute("SELECT MAX(trade_date) as max_date FROM score_rank_daily")
            res = cursor.fetchone() or {}
            latest_date = res.get('max_date')

            if latest_date and selected_symbols:
                placeholders = ','.join(['%s'] * len(selected_symbols))
                sql = f"""
                SELECT
                    symbol,
                    name,
                    score,
                    COALESCE(opt_score, 0) as opt_score,
                    COALESCE(claude_score, 0) as claude_score,
                    pool_type
                FROM score_rank_daily
                WHERE trade_date = %s
                  AND symbol IN ({placeholders})
                """
                cursor.execute(sql, tuple([latest_date] + selected_symbols))
                rows = cursor.fetchall()

    pyramid = build_pyramid(rows, DEFAULT_PARAMS['pyramid_min_score'], DEFAULT_PARAMS['pyramid_top_pct'], DEFAULT_PARAMS['pyramid_min_claude'])
    weighted_rank = build_weighted(rows, profile_a, profile_b, profile_c)
    quadrants, quadrant_base = build_quadrants(rows, DEFAULT_PARAMS['quadrant_min_score'], DEFAULT_PARAMS['quadrant_opt_cut'], DEFAULT_PARAMS['quadrant_claude_cut'])

    return render_template(
        'sina_tech_score.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        date=latest_date,
        pools=pools,
        pool_id=pool_id,
        symbol_input=symbol_input,
        selected_symbols=selected_symbols,
        total_candidates=len(rows),
        pyramid=pyramid,
        weighted_rank=weighted_rank,
        quadrants=quadrants,
        quadrant_base_count=len(quadrant_base),
    )


@app.route('/sina/monitor')
def sina_monitor():
    conn = get_db()
    
    # Parameters
    active_tab = request.args.get('tab', 'latest_b') # Default to 'latest_b'
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
    chart_data = [] # Initialize to avoid UnboundLocalError
    last_completed_date = None
    signal_stats_rows = []

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

        elif active_tab == 'latest_b':
            # Latest buy date must be newer than latest sell date (as-of state)
            subquery = """
                (
                    SELECT latest_buy.*
                    FROM bs_detection_results AS latest_buy
                    INNER JOIN (
                        SELECT
                            stock_code,
                            MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                            MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
                        FROM bs_detection_results
                        GROUP BY stock_code
                    ) AS summary
                        ON latest_buy.stock_code = summary.stock_code
                        AND latest_buy.batch_date = summary.latest_buy_date
                    WHERE latest_buy.has_buy_signal = 1
                      AND (summary.latest_sell_date IS NULL OR summary.latest_buy_date > summary.latest_sell_date)
                )
            """
            pagination, offset = get_pagination(cursor, f"{subquery} as sub", page, per_page)

            sql_holding = f"""
                SELECT sub.*, t_name.stock_name
                FROM {subquery} as sub
                LEFT JOIN a_share_stock_list t_name ON sub.stock_code = t_name.stock_code COLLATE utf8mb4_general_ci
                ORDER BY sub.batch_date DESC, sub.stock_code ASC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_holding, (per_page, offset))
            signals = cursor.fetchall()

        elif active_tab == 'stats':
            # Fetch historical stats for Chart.js
            days = request.args.get('days', 30, type=int)
            sql_history = """
                SELECT batch_date, 
                       COUNT(*) as total,
                       SUM(has_buy_signal) as buy_count,
                       SUM(has_sell_signal) as sell_count
                FROM bs_detection_results
                GROUP BY batch_date
                ORDER BY batch_date DESC
                LIMIT %s
            """
            cursor.execute(sql_history, (days,))
            chart_data = cursor.fetchall()[::-1] # Order chronologically for chart

        elif active_tab == 'signal_stats':
            where_stmt = ""
            params = []
            if query_date:
                where_stmt = "WHERE batch_date <= %s"
                params = [query_date]

            sub_table = f"""
                (
                    SELECT
                        batch_date,
                        COUNT(*) AS total,
                        SUM(has_buy_signal) AS buy_count,
                        SUM(has_sell_signal) AS sell_count,
                        MAX(created_at) AS last_update
                    FROM bs_detection_results
                    {where_stmt}
                    GROUP BY batch_date
                )
            """
            pagination, offset = get_pagination(cursor, f"{sub_table} as t", page, per_page, "", tuple(params) if params else None)

            sql_stats_rows = f"""
                SELECT *
                FROM {sub_table} AS t
                ORDER BY batch_date DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_stats_rows, tuple(params + [per_page, offset]))
            signal_stats_rows = cursor.fetchall()

        elif active_tab == 'signal_stats':
            where_stmt = ""
            params = []
            if query_date:
                where_stmt = "WHERE batch_date <= %s"
                params = [query_date]

            sub_table = f"""
                (
                    SELECT
                        batch_date,
                        COUNT(*) AS total,
                        SUM(has_buy_signal) AS buy_count,
                        SUM(has_sell_signal) AS sell_count,
                        MAX(created_at) AS last_update
                    FROM bs_detection_results
                    {where_stmt}
                    GROUP BY batch_date
                )
            """
            pagination, offset = get_pagination(cursor, f"{sub_table} as t", page, per_page, "", tuple(params) if params else None)

            sql_stats_rows = f"""
                SELECT *
                FROM {sub_table} AS t
                ORDER BY batch_date DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_stats_rows, tuple(params + [per_page, offset]))
            signal_stats_rows = cursor.fetchall()

        elif active_tab == 'signal_stats':
            where_stmt = ""
            params = []
            if query_date:
                where_stmt = "WHERE batch_date <= %s"
                params = [query_date]

            sub_table = f"""
                (
                    SELECT
                        batch_date,
                        COUNT(*) AS total,
                        SUM(has_buy_signal) AS buy_count,
                        SUM(has_sell_signal) AS sell_count,
                        MAX(created_at) AS last_update
                    FROM bs_detection_results
                    {where_stmt}
                    GROUP BY batch_date
                )
            """
            pagination, offset = get_pagination(cursor, f"{sub_table} as t", page, per_page, "", tuple(params) if params else None)

            sql_stats_rows = f"""
                SELECT *
                FROM {sub_table} AS t
                ORDER BY batch_date DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_stats_rows, tuple(params + [per_page, offset]))
            signal_stats_rows = cursor.fetchall()

        elif active_tab == 'signal_stats':
            where_stmt = ""
            params = []
            if query_date:
                where_stmt = "WHERE batch_date <= %s"
                params = [query_date]

            sub_table = f"""
                (
                    SELECT
                        batch_date,
                        COUNT(*) AS total,
                        SUM(has_buy_signal) AS buy_count,
                        SUM(has_sell_signal) AS sell_count,
                        MAX(created_at) AS last_update
                    FROM bs_detection_results
                    {where_stmt}
                    GROUP BY batch_date
                )
            """
            pagination, offset = get_pagination(cursor, f"{sub_table} as t", page, per_page, "", tuple(params) if params else None)

            sql_stats_rows = f"""
                SELECT *
                FROM {sub_table} AS t
                ORDER BY batch_date DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(sql_stats_rows, tuple(params + [per_page, offset]))
            signal_stats_rows = cursor.fetchall()

    return render_template('sina_monitor.html',
                           active_tab=active_tab,
                           query_date=query_date,
                           query_date_iso=query_date_iso,
                           daily_stats=daily_stats,
                           signals=signals,
                           pagination=pagination,
                           chart_data=chart_data,
                           last_completed_date=last_completed_date,
                           signal_stats_rows=signal_stats_rows,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/api/signal_stats')
def api_signal_stats():
    conn = get_db()
    with conn.cursor() as cursor:
        sql = """
            SELECT 
                batch_date, 
                COUNT(*) AS total,
                SUM(has_buy_signal) AS buy_count,
                SUM(has_sell_signal) AS sell_count,
                MAX(created_at) AS last_update
            FROM bs_detection_results
            GROUP BY batch_date
            ORDER BY batch_date DESC
            LIMIT 30
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        # Sort back to chronological order (Ascending batch_date) for Chart.js
        rows = rows[::-1]

        # Convert datetime to string for JSON serialization
        for row in rows:
            if row.get('last_update'):
                row['last_update'] = str(row['last_update'])
            if row.get('batch_date') and hasattr(row['batch_date'], 'isoformat'):
                row['batch_date'] = row['batch_date'].isoformat()
        return jsonify(rows)


@app.route('/api/m7/sell-signals/latest')
def api_m7_sell_signals_latest():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            _ensure_m7_sell_signal_table(cursor)

            source_raw = str(request.args.get('source') or 'm7_rebalance').strip()
            source = None if source_raw.lower() in {'', 'all'} else source_raw
            limit = request.args.get('limit', 200, type=int)
            limit = max(1, min(int(limit or 200), 1000))

            requested_date = request.args.get('date')
            signal_date = _coerce_to_date(requested_date) if requested_date else None
            if requested_date and signal_date is None:
                return jsonify({"error": "invalid date, expected YYYY-MM-DD or YYYYMMDD"}), 400

            if signal_date is None:
                if source:
                    cursor.execute(
                        "SELECT MAX(signal_date) AS d FROM m7_sell_signals WHERE source = %s",
                        (source,),
                    )
                else:
                    cursor.execute("SELECT MAX(signal_date) AS d FROM m7_sell_signals")
                signal_date = (cursor.fetchone() or {}).get("d")

            if signal_date is None:
                return jsonify(
                    {
                        "signal_date": None,
                        "source": source or "all",
                        "limit": limit,
                        "summary": {
                            "total_rows": 0,
                            "forced_exit_rows": 0,
                            "rebalance_rows": 0,
                            "pending_rows": 0,
                            "total_notional": 0.0,
                        },
                        "rows": [],
                    }
                )

            where_clauses = ["signal_date = %s"]
            params = [signal_date]
            if source:
                where_clauses.append("source = %s")
                params.append(source)
            where_sql = "WHERE " + " AND ".join(where_clauses)

            cursor.execute(
                f"""
                SELECT
                    symbol,
                    name,
                    sell_signal,
                    reason,
                    reason_code,
                    reason_detail_json,
                    rule_version,
                    score_date,
                    current_weight,
                    target_weight,
                    delta_weight,
                    price,
                    shares,
                    notional,
                    pending_flag,
                    pending_reason,
                    exec_status,
                    protect_window_hit,
                    market_risk_gate_hit,
                    source,
                    created_at,
                    updated_at
                FROM m7_sell_signals
                {where_sql}
                ORDER BY notional DESC, symbol ASC
                LIMIT %s
                """,
                tuple(params + [limit]),
            )
            rows = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT
                    COUNT(*) AS total_rows,
                    SUM(
                        CASE
                            WHEN COALESCE(reason_code, '') IN ('BS_REVERSAL', 'HARD_STOP', 'LIMIT_DOWN_EXIT', 'TRAILING_STOP', 'TIME_STOP', 'SCORE_EXIT')
                                 OR sell_signal = 'FORCED_EXIT'
                            THEN 1 ELSE 0
                        END
                    ) AS forced_exit_rows,
                    SUM(CASE WHEN COALESCE(reason_code, '') = 'REBALANCE_SELL' OR sell_signal = 'REBALANCE' THEN 1 ELSE 0 END) AS rebalance_rows,
                    SUM(CASE WHEN COALESCE(pending_flag, 0) = 1 OR UPPER(COALESCE(exec_status, '')) = 'PENDING' THEN 1 ELSE 0 END) AS pending_rows,
                    SUM(COALESCE(notional, 0)) AS total_notional
                FROM m7_sell_signals
                {where_sql}
                """,
                tuple(params),
            )
            summary = cursor.fetchone() or {}

            for row in rows:
                if row.get("created_at"):
                    row["created_at"] = str(row.get("created_at"))
                if row.get("updated_at"):
                    row["updated_at"] = str(row.get("updated_at"))
                if row.get("score_date"):
                    row["score_date"] = str(row.get("score_date"))

            signal_date_str = signal_date.isoformat() if hasattr(signal_date, "isoformat") else str(signal_date)
            payload = {
                "signal_date": signal_date_str,
                "source": source or "all",
                "limit": limit,
                "summary": {
                    "total_rows": int(summary.get("total_rows") or 0),
                    "forced_exit_rows": int(summary.get("forced_exit_rows") or 0),
                    "rebalance_rows": int(summary.get("rebalance_rows") or 0),
                    "pending_rows": int(summary.get("pending_rows") or 0),
                    "total_notional": float(summary.get("total_notional") or 0.0),
                },
                "rows": rows,
            }
            return jsonify(payload)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        reason_code = str(data.get('reason_code') or "").strip()
        score = data.get('score')

        if reason_code and reason_code not in reason:
            reason = f"{reason} [{reason_code}]"
        
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
            # 成交后清理挂起强制卖出状态（若仍有残余持仓）
            conn = None
            try:
                conn = pymysql.connect(**DB_CONFIG)
                with conn.cursor() as cursor:
                    _ensure_live_positions_m7_columns(cursor)
                    cursor.execute(
                        """
                        UPDATE live_positions
                        SET pending_forced_exit = 0,
                            pending_exit_reason = NULL
                        WHERE symbol = %s
                        """,
                        (symbol,),
                    )
                conn.commit()
            except Exception as e:
                print(f"Failed to clear pending_exit state after sell: {e}")
            finally:
                if conn:
                    conn.close()
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


def _coerce_to_date(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, datetime):
        return raw_value.date()
    if isinstance(raw_value, date):
        return raw_value

    s = str(raw_value).strip()
    if not s:
        return None

    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        pass

    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except Exception:
            return None
    return None


def _ensure_m7_sell_signal_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS m7_sell_signals (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            signal_date DATE NOT NULL,
            symbol VARCHAR(16) NOT NULL,
            name VARCHAR(64) NULL,
            sell_signal VARCHAR(32) NOT NULL DEFAULT 'REBALANCE',
            reason VARCHAR(255) NULL,
            current_weight DECIMAL(10,4) NULL,
            target_weight DECIMAL(10,4) NULL,
            delta_weight DECIMAL(10,4) NULL,
            price DECIMAL(12,4) NULL,
            shares INT NULL,
            notional DECIMAL(16,2) NULL,
            source VARCHAR(32) NOT NULL DEFAULT 'm7_rebalance',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_signal_symbol_source (signal_date, symbol, source),
            KEY idx_signal_date (signal_date),
            KEY idx_symbol (symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute("DESC m7_sell_signals")
    columns = {row["Field"] for row in cursor.fetchall()}
    alter_sql = {
        "reason_code": "ALTER TABLE m7_sell_signals ADD COLUMN reason_code VARCHAR(32) NULL COMMENT 'M7卖出主因码' AFTER reason",
        "reason_detail_json": "ALTER TABLE m7_sell_signals ADD COLUMN reason_detail_json JSON NULL COMMENT '结构化卖出依据' AFTER reason_code",
        "rule_version": "ALTER TABLE m7_sell_signals ADD COLUMN rule_version VARCHAR(32) NOT NULL DEFAULT 'v1' COMMENT '规则版本号' AFTER reason_detail_json",
        "score_date": "ALTER TABLE m7_sell_signals ADD COLUMN score_date DATE NULL COMMENT '评分日期' AFTER rule_version",
        "pending_flag": "ALTER TABLE m7_sell_signals ADD COLUMN pending_flag TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否挂起' AFTER score_date",
        "pending_reason": "ALTER TABLE m7_sell_signals ADD COLUMN pending_reason VARCHAR(128) NULL COMMENT '挂起原因' AFTER pending_flag",
        "exec_status": "ALTER TABLE m7_sell_signals ADD COLUMN exec_status VARCHAR(32) NOT NULL DEFAULT 'NEW' COMMENT 'NEW/PENDING/EXECUTED/FAILED' AFTER pending_reason",
        "protect_window_hit": "ALTER TABLE m7_sell_signals ADD COLUMN protect_window_hit TINYINT(1) NOT NULL DEFAULT 0 COMMENT '命中保护期' AFTER exec_status",
        "market_risk_gate_hit": "ALTER TABLE m7_sell_signals ADD COLUMN market_risk_gate_hit TINYINT(1) NOT NULL DEFAULT 0 COMMENT '命中系统性风控门禁' AFTER protect_window_hit",
    }
    for col, sql in alter_sql.items():
        if col not in columns:
            cursor.execute(sql)

    cursor.execute("SHOW INDEX FROM m7_sell_signals")
    existing_keys = {row.get("Key_name") for row in cursor.fetchall()}
    if "idx_signal_source" not in existing_keys:
        cursor.execute("CREATE INDEX idx_signal_source ON m7_sell_signals(source, signal_date)")
    if "idx_reason_code" not in existing_keys:
        cursor.execute("CREATE INDEX idx_reason_code ON m7_sell_signals(reason_code, signal_date)")
    if "idx_pending_status" not in existing_keys:
        cursor.execute("CREATE INDEX idx_pending_status ON m7_sell_signals(pending_flag, exec_status, signal_date)")


def _ensure_live_positions_m7_columns(cursor):
    cursor.execute("SHOW TABLES LIKE 'live_positions'")
    if cursor.fetchone() is None:
        return
    cursor.execute("DESC live_positions")
    columns = {row["Field"] for row in cursor.fetchall()}
    alter_sql = {
        "entry_date": "ALTER TABLE live_positions ADD COLUMN entry_date DATE NULL COMMENT '建仓日期' AFTER avg_cost",
        "highest_since_entry": "ALTER TABLE live_positions ADD COLUMN highest_since_entry DECIMAL(12,4) NULL COMMENT '建仓后最高价' AFTER current_price",
        "holding_trade_days": "ALTER TABLE live_positions ADD COLUMN holding_trade_days INT NOT NULL DEFAULT 0 COMMENT '持仓交易日天数' AFTER highest_since_entry",
        "pending_forced_exit": "ALTER TABLE live_positions ADD COLUMN pending_forced_exit TINYINT(1) NOT NULL DEFAULT 0 COMMENT '强制卖出挂起' AFTER holding_trade_days",
        "pending_exit_reason": "ALTER TABLE live_positions ADD COLUMN pending_exit_reason VARCHAR(128) NULL COMMENT '挂起原因' AFTER pending_forced_exit",
        "rebuy_cooldown_until": "ALTER TABLE live_positions ADD COLUMN rebuy_cooldown_until DATE NULL COMMENT '禁买截止日' AFTER pending_exit_reason",
    }
    for col, sql in alter_sql.items():
        if col not in columns:
            cursor.execute(sql)


def _sync_m7_sell_signals(conn, signal_date, orders, source="m7_rebalance", rule_version=M7_RULE_VERSION_V1):
    if conn is None:
        return 0

    signal_dt = _coerce_to_date(signal_date) or datetime.now().date()
    source = str(source or "m7_rebalance")
    rule_version = str(rule_version or M7_RULE_VERSION_V1)

    sell_orders = [
        o for o in (orders or [])
        if str(o.get("action") or "").upper() == "SELL"
    ]

    with conn.cursor() as cursor:
        _ensure_m7_sell_signal_table(cursor)
        cursor.execute(
            "DELETE FROM m7_sell_signals WHERE signal_date = %s AND source = %s",
            (signal_dt, source),
        )

        if sell_orders:
            sql = """
                INSERT INTO m7_sell_signals (
                    signal_date, symbol, name, sell_signal, reason,
                    current_weight, target_weight, delta_weight,
                    price, shares, notional, source,
                    reason_code, reason_detail_json, rule_version, score_date,
                    pending_flag, pending_reason, exec_status,
                    protect_window_hit, market_risk_gate_hit
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = []
            for o in sell_orders:
                detail = o.get("reason_detail_json")
                if isinstance(detail, (dict, list)):
                    detail = json.dumps(detail, ensure_ascii=False)
                params.append(
                    (
                        signal_dt,
                        str(o.get("symbol") or "").zfill(6),
                        o.get("name"),
                        str(o.get("sell_signal") or "REBALANCE"),
                        str(o.get("reason") or ""),
                        float(o.get("current_weight") or 0.0),
                        float(o.get("target_weight") or 0.0),
                        float(o.get("delta_weight") or 0.0),
                        float(o.get("price") or 0.0),
                        int(o.get("shares") or 0),
                        float(o.get("notional") or 0.0),
                        source,
                        str(o.get("reason_code") or o.get("sell_signal") or "REBALANCE_SELL"),
                        detail,
                        str(o.get("rule_version") or rule_version),
                        _coerce_to_date(o.get("score_date")) or signal_dt,
                        int(o.get("pending_flag") or 0),
                        (str(o.get("pending_reason") or "").strip() or None),
                        str(o.get("exec_status") or "NEW"),
                        int(o.get("protect_window_hit") or 0),
                        int(o.get("market_risk_gate_hit") or 0),
                    )
                )
            cursor.executemany(sql, params)

            # 将 pending 强制卖出状态同步到 live_positions，便于次日优先重试
            _ensure_live_positions_m7_columns(cursor)
            state_updates = []
            for o in sell_orders:
                symbol = str(o.get("symbol") or "").zfill(6)
                if not symbol:
                    continue
                reason_code = str(o.get("reason_code") or "")
                forced = int(o.get("forced_exit") or 0) == 1 or reason_code in M7_FORCED_REASON_CODES
                if not forced:
                    state_updates.append((0, None, symbol))
                    continue
                pending_flag = int(o.get("pending_flag") or 0)
                pending_reason = str(o.get("pending_reason") or "").strip() or None
                state_updates.append((pending_flag, pending_reason, symbol))
            if state_updates:
                cursor.executemany(
                    """
                    UPDATE live_positions
                    SET pending_forced_exit = %s,
                        pending_exit_reason = %s
                    WHERE symbol = %s
                    """,
                    state_updates,
                )

    conn.commit()
    return len(sell_orders)


def _fetch_live_positions_snapshot(conn):
    """Fetch current live positions as current portfolio snapshot."""
    if conn is None:
        return [], None

    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'live_positions'")
        has_pos = cursor.fetchone() is not None
        if not has_pos:
            return [], None

        _ensure_live_positions_m7_columns(cursor)
        cursor.execute(
            """
            SELECT
                symbol,
                name,
                shares,
                avg_cost,
                current_price,
                entry_date,
                highest_since_entry,
                holding_trade_days,
                pending_forced_exit,
                pending_exit_reason,
                rebuy_cooldown_until
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
                    'shares': int(r.get('shares') or 0),
                    'avg_cost': float(r.get('avg_cost') or 0),
                    'current_price': float(r.get('current_price') or 0),
                    'entry_date': r.get('entry_date'),
                    'highest_since_entry': float(r.get('highest_since_entry') or 0) if r.get('highest_since_entry') is not None else None,
                    'holding_trade_days': int(r.get('holding_trade_days') or 0),
                    'pending_forced_exit': int(r.get('pending_forced_exit') or 0),
                    'pending_exit_reason': r.get('pending_exit_reason'),
                    'rebuy_cooldown_until': r.get('rebuy_cooldown_until'),
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

    max_positions = request.args.get('max_positions', 3, type=int)
    capital = request.args.get('capital', 100000, type=float)
    rule_version = str(request.args.get('rule_version') or M7_RULE_VERSION_V21).strip() or M7_RULE_VERSION_V21
    if rule_version not in {M7_RULE_VERSION_V1, M7_RULE_VERSION_V21}:
        rule_version = M7_RULE_VERSION_V21

    min_trade_weight = request.args.get('min_trade_weight', 1.0, type=float)
    min_trade_notional = request.args.get('min_trade_notional', 5000.0, type=float)
    stop_loss_pct = request.args.get('stop_loss_pct', 6.0, type=float)
    bs_fresh_trade_days = request.args.get('bs_fresh_trade_days', 3, type=int)
    trail_activate_pct = request.args.get('trail_activate_pct', 12.0, type=float)
    trail_drawdown_pct = request.args.get('trail_drawdown_pct', 4.0, type=float)
    time_stop_days = request.args.get('time_stop_days', 8, type=int)
    time_stop_min_return_pct = request.args.get('time_stop_min_return_pct', 1.0, type=float)
    time_stop_rel_index_pct = request.args.get('time_stop_rel_index_pct', -3.0, type=float)
    min_hold_protect_days = request.args.get('min_hold_protect_days', 5, type=int)
    claude_floor = request.args.get('claude_floor', 45.0, type=float)
    score_floor = request.args.get('score_floor', 60.0, type=float)
    score_confirm_days = request.args.get('score_confirm_days', 2, type=int)
    rebuy_cooldown_days = request.args.get('rebuy_cooldown_days', 5, type=int)
    enable_market_risk_gate = str(request.args.get('enable_market_risk_gate') or '').strip().lower() in {'1', 'true', 'on', 'yes'}
    market_risk_gate_drop_pct = request.args.get('market_risk_gate_drop_pct', -2.0, type=float)
    is_post_close = str(request.args.get('is_post_close') or '1').strip().lower() not in {'0', 'false', 'off', 'no'}

    max_positions = int(clamp(max_positions or 3, 1, 20))
    capital = clamp(capital if capital is not None else 100000, 10000.0, 100000000.0)
    min_trade_weight = clamp(min_trade_weight if min_trade_weight is not None else 1.0, 0.0, 20.0)
    min_trade_notional = clamp(min_trade_notional if min_trade_notional is not None else 5000.0, 0.0, 100000000.0)
    stop_loss_pct = clamp(stop_loss_pct if stop_loss_pct is not None else 6.0, 0.0, 30.0)
    bs_fresh_trade_days = int(clamp(bs_fresh_trade_days if bs_fresh_trade_days is not None else 3, 1, 20))
    trail_activate_pct = clamp(trail_activate_pct if trail_activate_pct is not None else 12.0, 0.0, 80.0)
    trail_drawdown_pct = clamp(trail_drawdown_pct if trail_drawdown_pct is not None else 4.0, 0.1, 40.0)
    time_stop_days = int(clamp(time_stop_days if time_stop_days is not None else 8, 1, 120))
    time_stop_min_return_pct = clamp(time_stop_min_return_pct if time_stop_min_return_pct is not None else 1.0, -50.0, 50.0)
    time_stop_rel_index_pct = clamp(time_stop_rel_index_pct if time_stop_rel_index_pct is not None else -3.0, -50.0, 50.0)
    min_hold_protect_days = int(clamp(min_hold_protect_days if min_hold_protect_days is not None else 5, 0, 60))
    claude_floor = clamp(claude_floor if claude_floor is not None else 45.0, 0.0, 100.0)
    score_floor = clamp(score_floor if score_floor is not None else 60.0, 0.0, 100.0)
    score_confirm_days = int(clamp(score_confirm_days if score_confirm_days is not None else 2, 1, 10))
    rebuy_cooldown_days = int(clamp(rebuy_cooldown_days if rebuy_cooldown_days is not None else 5, 0, 90))
    market_risk_gate_drop_pct = clamp(
        market_risk_gate_drop_pct if market_risk_gate_drop_pct is not None else -2.0,
        -20.0,
        0.0,
    )

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
        min_trade_notional=min_trade_notional,
        conn=conn,
        stop_loss_pct=stop_loss_pct,
        rule_version=rule_version,
        asof_date=latest_date,
        bs_fresh_trade_days=bs_fresh_trade_days,
        trail_activate_pct=trail_activate_pct,
        trail_drawdown_pct=trail_drawdown_pct,
        time_stop_days=time_stop_days,
        time_stop_min_return_pct=time_stop_min_return_pct,
        time_stop_rel_index_pct=time_stop_rel_index_pct,
        min_hold_protect_days=min_hold_protect_days,
        enable_market_risk_gate=enable_market_risk_gate,
        market_risk_gate_drop_pct=market_risk_gate_drop_pct,
        claude_floor=claude_floor,
        score_floor=score_floor,
        score_confirm_days=score_confirm_days,
        is_post_close=is_post_close,
        rebuy_cooldown_days=rebuy_cooldown_days,
    )
    m7_sell_sync_count = 0
    try:
        m7_sell_sync_count = _sync_m7_sell_signals(
            conn=conn,
            signal_date=latest_date,
            orders=m7_eval.get('orders') or [],
            source='m7_rebalance',
            rule_version=rule_version,
        )
    except Exception as e:
        print(f"Failed to sync M7 sell signals: {e}")

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
            'rule_version': rule_version,
            'min_trade_weight': min_trade_weight,
            'min_trade_notional': min_trade_notional,
            'stop_loss_pct': stop_loss_pct,
            'bs_fresh_trade_days': bs_fresh_trade_days,
            'trail_activate_pct': trail_activate_pct,
            'trail_drawdown_pct': trail_drawdown_pct,
            'time_stop_days': time_stop_days,
            'time_stop_min_return_pct': time_stop_min_return_pct,
            'time_stop_rel_index_pct': time_stop_rel_index_pct,
            'min_hold_protect_days': min_hold_protect_days,
            'claude_floor': claude_floor,
            'score_floor': score_floor,
            'score_confirm_days': score_confirm_days,
            'rebuy_cooldown_days': rebuy_cooldown_days,
            'enable_market_risk_gate': 1 if enable_market_risk_gate else 0,
            'market_risk_gate_drop_pct': market_risk_gate_drop_pct,
            'is_post_close': 1 if is_post_close else 0,
            'm7_sell_sync_count': m7_sell_sync_count,
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
            return render_template('stock_pool.html', now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pools=[])

    return render_template(
        'stock_pool.html',
        now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        pools=pools,
    )


@app.route('/stock_pool/items')
def stock_pool_items():
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
            return redirect(url_for('stock_pool'))

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
        'stock_pool_items.html',
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
        SELECT latest_buy.stock_code AS symbol, COALESCE(a.stock_name, latest_buy.stock_code) AS stock_name
        FROM bs_detection_results AS latest_buy
        INNER JOIN (
            SELECT
                stock_code,
                MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
            FROM bs_detection_results
            WHERE batch_date <= %s
            GROUP BY stock_code
        ) AS summary
            ON latest_buy.stock_code = summary.stock_code
            AND latest_buy.batch_date = summary.latest_buy_date
        LEFT JOIN a_share_stock_list a ON latest_buy.stock_code = a.stock_code COLLATE utf8mb4_general_ci
        WHERE latest_buy.has_buy_signal = 1
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
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


def _lookup_stock_by_code(cursor, symbol):
    cursor.execute(
        """
        SELECT stock_code, stock_name
        FROM a_share_stock_list
        WHERE stock_code = %s
        LIMIT 1
        """,
        (symbol,),
    )
    return cursor.fetchone()


def _lookup_stock_by_name(cursor, stock_name):
    cursor.execute(
        """
        SELECT stock_code, stock_name
        FROM a_share_stock_list
        WHERE stock_name = %s
        LIMIT 2
        """,
        (stock_name,),
    )
    return cursor.fetchall()


def _resolve_stock_entry(cursor, symbol, stock_name):
    symbol = str(symbol or '').strip()
    stock_name = str(stock_name or '').strip()

    if symbol:
        code = symbol.zfill(6)
        if not code.isdigit() or len(code) != 6:
            return None, None, f'股票代码格式无效: {symbol}'
        row = _lookup_stock_by_code(cursor, code)
        if not row:
            return None, None, f'股票代码不存在: {code}'
        db_name = str(row.get('stock_name') or '').strip()
        if stock_name and stock_name != db_name:
            return None, None, f'股票代码与名称不匹配: {code} / {stock_name}'
        return code, db_name, None

    if stock_name:
        rows = _lookup_stock_by_name(cursor, stock_name)
        if not rows:
            return None, None, f'股票名称不存在: {stock_name}'
        if len(rows) > 1:
            return None, None, f'股票名称存在多条，请改用代码录入: {stock_name}'
        row = rows[0]
        return str(row.get('stock_code') or '').zfill(6), str(row.get('stock_name') or '').strip(), None

    return None, None, '请至少输入股票代码或股票名称。'


@app.route('/stock_pool/pool/add', methods=['POST'], endpoint='stock_pool_add')
def add_stock_pool():
    pool_name = (request.form.get('pool_name') or '').strip()
    if not pool_name:
        flash('股票池名称不能为空。', 'danger')
        return redirect(url_for('stock_pool'))

    conn = get_db()
    with conn.cursor() as cursor:
        try:
            # Generate a unique key for the custom pool
            pool_key = f"CUSTOM_{int(time.time())}"
            
            cursor.execute(
                """
                INSERT INTO stock_pools (pool_key, pool_name, source_type, is_system, is_editable)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (pool_key, pool_name, 'MANUAL', 0, 1)
            )
            conn.commit()
            flash(f'成功创建股票池：{pool_name}', 'success')
        except Exception as e:
            flash(f'创建股票池失败: {e}', 'danger')

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

        resolved = []
        invalid_msgs = []
        seen = set()

        if raw_codes:
            for i, code_raw in enumerate(raw_codes):
                name_hint = stock_name if (i == 0 and len(raw_codes) == 1 and not symbols_batch) else ''
                code, resolved_name, err = _resolve_stock_entry(cursor, code_raw, name_hint)
                if err:
                    invalid_msgs.append(err)
                    continue
                if code in seen:
                    continue
                seen.add(code)
                resolved.append((code, resolved_name))
        else:
            code, resolved_name, err = _resolve_stock_entry(cursor, '', stock_name)
            if err:
                flash(err, 'danger')
                return redirect(url_for('stock_pool', pool_id=pool_id))
            resolved.append((code, resolved_name))

        if not resolved:
            flash('没有可保存的有效股票，请检查代码或名称是否真实存在。', 'danger')
            return redirect(url_for('stock_pool', pool_id=pool_id))

        try:
            for code, resolved_name in resolved:
                final_name = resolved_name
                cursor.execute(
                    """
                    INSERT INTO stock_pool_items (pool_id, symbol, stock_name, note)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        stock_name = VALUES(stock_name),
                        note = VALUES(note),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (pool_id, code, final_name, note or None),
                )
            conn.commit()
            flash(f"已保存到【{pool['pool_name']}】：{len(resolved)} 只股票", 'success')
            for msg in invalid_msgs[:10]:
                flash(msg, 'danger')
            if len(invalid_msgs) > 10:
                flash(f'另有 {len(invalid_msgs)-10} 条无效输入未展示。', 'danger')
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
    task_results = []
    task_locks = []
    notification_channels = _default_notification_channels()
    active_tab = str(request.args.get("tab") or "center-tab")
    if active_tab not in {"center-tab", "lock-tab", "result-tab"}:
        active_tab = "center-tab"
    _reconcile_stale_task_states()
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            notification_channels = _load_notification_channels_from_cursor(cursor)
        task_results = _get_task_history(limit=200)
        task_locks = _get_task_lock_rows(limit=50)
    except Exception as e:
        print(f"Failed to load stock pools in admin: {e}")

    _refresh_task_next_runs()

    return render_template('admin.html', 
                           tasks=TASKS,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           now_date=now_date_iso,
                           task_results=task_results,
                           task_locks=task_locks,
                           notification_channels=notification_channels,
                           active_tab=active_tab)

def update_task_db(task_name):
    """Save task status to database"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            with TASKS_LOCK:
                task = TASKS[task_name]
                last_run = task['last_run']
                if last_run == "Never":
                    last_run_db = None
                else:
                    last_run_db = last_run
                next_run_dt = _compute_next_run(task.get('schedule_time'), bool(task.get('schedule_enabled')))
                task['next_run'] = next_run_dt.strftime('%Y-%m-%d %H:%M:%S') if next_run_dt else '-'

                sql = """
                INSERT INTO app_task_status (task_name, last_run, status, switched_day, schedule_enabled, schedule_time, next_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_run = VALUES(last_run),
                    status = VALUES(status),
                    switched_day = VALUES(switched_day),
                    schedule_enabled = VALUES(schedule_enabled),
                    schedule_time = VALUES(schedule_time),
                    next_run = VALUES(next_run)
                """
                cursor.execute(
                    sql,
                    (
                        task_name,
                        last_run_db,
                        task['status'],
                        int(task['switched_day']),
                        int(bool(task.get('schedule_enabled'))),
                        task.get('schedule_time') or '00:00',
                        next_run_dt,
                    ),
                )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to update task DB: {e}")


@app.route('/admin/task/<task_name>/schedule', methods=['POST'])
def update_task_schedule(task_name):
    if task_name not in TASKS:
        flash(f"Unknown task: {task_name}", 'danger')
        return redirect(url_for('admin'))

    schedule_time = _normalize_schedule_time(request.form.get('schedule_time'))
    if schedule_time is None:
        flash('调度时间格式非法，请使用 HH:MM。', 'danger')
        return redirect(url_for('admin'))

    schedule_enabled = request.form.get('schedule_enabled') == 'on'
    if schedule_enabled and task_name not in SCHEDULED_TASK_WHITELIST:
        schedule_enabled = False
        flash(f"任务 {TASKS[task_name]['name']} 不支持定时调度，已自动关闭。", 'warning')
    with TASKS_LOCK:
        TASKS[task_name]['schedule_time'] = schedule_time
        TASKS[task_name]['schedule_enabled'] = schedule_enabled
    update_task_db(task_name)
    flash(f"任务 {TASKS[task_name]['name']} 调度配置已更新。", 'success')
    return redirect(url_for('admin'))


@app.route('/admin/notification_channels', methods=['POST'])
def update_notification_channels():
    invalid_channels = []
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            _ensure_task_management_schema(cursor)
            for channel_key, channel_name in NOTIFICATION_CHANNEL_DEFS:
                raw_url = request.form.get(f"{channel_key}_webhook_url")
                raw_url = str(raw_url or "").strip()
                webhook_url = _normalize_webhook_url(raw_url)
                enabled_flag = request.form.get(f"{channel_key}_enabled") == "on"
                enabled = 1 if (enabled_flag and bool(webhook_url)) else 0
                if raw_url and not webhook_url:
                    invalid_channels.append(channel_name)
                cursor.execute(
                    """
                    INSERT INTO app_notification_channel (channel_key, channel_name, webhook_url, enabled)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        channel_name = VALUES(channel_name),
                        webhook_url = VALUES(webhook_url),
                        enabled = VALUES(enabled)
                    """,
                    (channel_key, channel_name, webhook_url, enabled),
                )
        conn.commit()
    except Exception as e:
        flash(f"通知渠道配置保存失败：{e}", "danger")
        return redirect(url_for("admin", tab="center-tab"))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if invalid_channels:
        flash(f"以下渠道 URL 非法（仅支持 http/https）：{', '.join(invalid_channels)}。已自动禁用这些渠道。", "warning")
    else:
        flash("通知渠道配置已更新。", "success")
    return redirect(url_for("admin", tab="center-tab"))


@app.route('/admin/run_task/<task_name>', methods=['POST'])
def run_task(task_name):
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (
        'application/json' in str(request.headers.get('Accept') or '').lower()
    )

    def _json_or_redirect(ok, message, category='info', http_code=200, **extra):
        if wants_json:
            payload = {'ok': bool(ok), 'message': message, 'category': category}
            payload.update(extra)
            return jsonify(payload), http_code
        flash(message, category)
        return redirect(url_for('admin'))

    if task_name not in TASKS:
        return _json_or_redirect(
            ok=False,
            message=f"Unknown task: {task_name}",
            category='danger',
            http_code=404,
            started=False,
            reason='UNKNOWN_TASK',
        )

    requested_datestr = _normalize_datestr(request.form.get('datestr') or request.args.get('datestr'))
    run_options = {}
    if requested_datestr:
        run_options['datestr'] = requested_datestr

    if TASKS[task_name].get('trading_day_only') and not requested_datestr and not _is_trading_day():
        return _json_or_redirect(
            ok=True,
            message=f"任务 {TASKS[task_name]['name']} 仅交易日执行，今日已跳过。",
            category='warning',
            started=False,
            reason='NON_TRADING_DAY',
            task_status=TASKS[task_name].get('status'),
        )

    started, reason = _trigger_task_execution(task_name, trigger_type='manual', run_options=run_options)
    date_suffix = f"（datestr={requested_datestr}）" if requested_datestr else ""
    if started:
        return _json_or_redirect(
            ok=True,
            message=f"任务 {TASKS[task_name]['name']} 已启动{date_suffix}，使用数据库锁保证同任务唯一运行。",
            category='success',
            started=True,
            reason='STARTED',
            task_status='Running...',
        )
    elif reason == 'RUNNING':
        return _json_or_redirect(
            ok=True,
            message=f"任务 {TASKS[task_name]['name']} 未启动{date_suffix}：已有同名任务正在运行。",
            category='warning',
            started=False,
            reason='RUNNING',
            task_status=TASKS[task_name].get('status'),
        )
    return _json_or_redirect(
        ok=False,
        message=f"任务 {TASKS[task_name]['name']} 启动失败{date_suffix}：{reason}",
        category='danger',
        http_code=500,
        started=False,
        reason=str(reason or 'FAILED'),
        task_status=TASKS[task_name].get('status'),
    )


def _run_scheduled_tasks_loop():
    while True:
        try:
            now = datetime.now()
            is_trade_day = _is_trading_day(now.date())
            due_tasks = []
            skipped_non_trade_tasks = []
            with TASKS_LOCK:
                for task_name, task in TASKS.items():
                    if task_name not in SCHEDULED_TASK_WHITELIST:
                        continue
                    if not bool(task.get('schedule_enabled')):
                        continue
                    schedule_time = _normalize_schedule_time(task.get('schedule_time'))
                    if not schedule_time:
                        continue
                    hour, minute = [int(x) for x in schedule_time.split(':')]
                    scheduled_for = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if now < scheduled_for:
                        continue
                    last_run_dt = _parse_task_last_run(task.get('last_run'))
                    if last_run_dt and last_run_dt >= scheduled_for:
                        continue
                    # Cross-process idempotence: prevent duplicate "catch-up" runs for the same slot.
                    if _has_scheduled_run_in_slot(task_name, scheduled_for):
                        continue
                    if not is_trade_day:
                        skipped_non_trade_tasks.append((task_name, scheduled_for))
                    else:
                        due_tasks.append(task_name)

            for task_name, scheduled_for in skipped_non_trade_tasks:
                _mark_scheduled_non_trading_success(task_name, scheduled_for)
                print(f"Scheduled task success-skip (non-trading day): {task_name}")

            for task_name in due_tasks:
                started, reason = _trigger_task_execution(task_name, trigger_type='schedule')
                if started:
                    print(f"Scheduled task started: {task_name}")
                elif reason == "RUNNING":
                    print(f"Scheduled task skipped (already running): {task_name}")
                else:
                    print(f"Scheduled task start failed: {task_name} ({reason})")

            time.sleep(20)
        except Exception as e:
            print(f"Task scheduler loop error: {e}")
            time.sleep(20)


def start_task_scheduler_loop():
    # When launched as `python web/app.py` with Werkzeug reloader, parent process
    # also imports this module. Skip scheduler in parent to avoid duplicate loops.
    if __name__ == "__main__" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    thread = threading.Thread(target=_run_scheduled_tasks_loop)
    thread.daemon = True
    thread.start()

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
        INSERT INTO live_positions (symbol, name, entry_date, shares, avg_cost, current_price, highest_since_entry, pending_forced_exit, pending_exit_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, NULL)
        ON DUPLICATE KEY UPDATE
            shares = shares + VALUES(shares),
            avg_cost = (avg_cost * shares + VALUES(avg_cost) * VALUES(shares)) / (shares + VALUES(shares)),
            current_price = VALUES(current_price),
            highest_since_entry = GREATEST(COALESCE(highest_since_entry, 0), COALESCE(VALUES(current_price), 0)),
            entry_date = VALUES(entry_date),
            pending_forced_exit = 0,
            pending_exit_reason = NULL
        """
        try:
            _ensure_live_positions_m7_columns(cursor)
            cursor.execute(sql, (symbol, name, date_str, shares, price, price, price))
            conn.commit()
            flash(f"Position {symbol} added/updated.", 'success')
        except Exception as e:
            flash(f"Failed to add position: {e}", 'danger')
            
    return redirect(url_for('admin'))

if os.environ.get("DISABLE_APP_SCHEDULER_LOOP") != "1":
    start_task_scheduler_loop()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
