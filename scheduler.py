import datetime
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from project_network import build_direct_network_env, enforce_direct_network
from scripts.ops.production_config import load_production_config
from scripts.ops.data_readiness_gate import PreScoreGate, PostScoreGate
from scoreRank.core.db_config import build_sqlalchemy_url, validate_db_credentials

enforce_direct_network()

# Configuration
# ------------------------------------------------------------------------------
LOG_DIR = Path("logs/scheduler")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Project Paths
PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable
CATCH_UP_GRACE_SECONDS = int(os.getenv("SCHEDULER_CATCH_UP_GRACE_SECONDS", "90"))
PRODUCTION_CONFIG = load_production_config()

# Task Definitions
# ------------------------------------------------------------------------------
TASKS = {
    "sina_bs": {
        "time": "15:20",
        "script": "sina/bs_detection/main.py",
        "args": ["config_1"],
        "description": "sina B/S Detection",
        "type": "script",
        "trading_day_only": True,
    },
    "sina_bs_image_weekly_cleanup": {
        "time": "22:05",
        "script": "scripts/ops/cleanup_sina_bs_detection_images.py",
        "args": ["--execute"],
        "description": "Weekly cleanup for previous-week Sina B/S detection images",
        "type": "script",
        "friday_only": True,
        "trading_day_only": False,
        "append_date": False,
    },
    "eastmoney_data": {
        "time": "16:30",
        "script": "eastmoney/main.py",
        "args": ["config_1"],
        "description": "eastmoney Data Fetch",
        "type": "script",
        "trading_day_only": True,
    },
    "daily_pipeline": {
        "time": "21:00",
        "description": "Daily Data Pipeline",
        "type": "pipeline",
        "trading_day_only": True,
    }
}

# Setup Logging
# ------------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("Scheduler")


# Database Utils
# ------------------------------------------------------------------------------
# Global Engine
_ENGINE = None

def get_engine():
    global _ENGINE
    if _ENGINE is None:
        # Fail-closed: refuse to start with unsafe credentials in any environment.
        # Require non-root user or non-empty password or explicit DB URL.
        if not validate_db_credentials():
            raise RuntimeError(
                "Production DB credentials are not configured safely.\n"
                "  Required: non-root MySQL user with password, OR explicit CHENYIYUN_DB_URL.\n"
                "  Set CHENYIYUN_DB_USER / CHENYIYUN_DB_PASSWORD or CHENYIYUN_DB_URL.\n"
                "  Refusing to start scheduler with empty root password."
            )
        db_url = build_sqlalchemy_url()
        _ENGINE = create_engine(db_url, future=True, pool_size=5, max_overflow=10, pool_recycle=3600)
        logger.info("Database engine initialized with env-var credentials.")
    return _ENGINE


def is_trade_day(target_date):
    """Check if the given date is a trading day."""
    date_str = target_date.strftime("%Y%m%d")
    engine = get_engine()
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT is_open FROM chenyiyun.dim_trade_cal "
                    "WHERE cal_date = :date AND exchange = 'SSE' LIMIT 1"
                ),
                {"date": date_str}
            ).fetchone()
            if result:
                return result[0] == 1
            return False
    except Exception as e:
        logger.error(f"Error checking trade day: {e}")
        return False




# Task Execution
# ------------------------------------------------------------------------------
def run_script(script_rel_path, args, log_name):
    """Run a python script as a subprocess."""
    script_path = PROJECT_ROOT / script_rel_path
    if not script_path.exists():
        logger.error(f"Script not found: {script_path}")
        return False
    
    # Construct command
    cmd = [PYTHON_EXE, str(script_path)] + args
    
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOG_DIR / f"{log_name}_{timestamp}.log"
    
    logger.info(f"Starting {log_name}: {' '.join(cmd)}")
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            # Add project root to PYTHONPATH
            env = build_direct_network_env(os.environ, pythonpath_prefix=str(PROJECT_ROOT))
            
            subprocess.run(
                cmd, 
                stdout=f, 
                stderr=subprocess.STDOUT, 
                env=env,
                check=True
            )
        logger.info(f"Finished {log_name} successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Task {log_name} failed with code {e.returncode}. See {log_file}")
        return False
    except Exception as e:
        logger.error(f"Error running {log_name}: {e}")
        return False


def run_pipeline(target_date) -> bool:
    """Run the 21:00 pipeline tasks sequentially."""
    date_str = target_date.strftime("%Y%m%d")
    date_iso = target_date.strftime("%Y-%m-%d")
    
    # 1. Pre-Score Gate: validate market data before scoring
    logger.info("Pre-Score Gate: validating market data readiness...")
    import json as _json
    gate = PreScoreGate(get_engine())
    max_retries = 24  # 2 hours (5 min * 24)
    retries = 0
    while True:
        result = gate.all_checks(target_date)
        if result["status"] == "READY":
            logger.info("PreScoreGate: READY — all market data checks passed.")
            break
        if result["status"] == "READY_WITH_WARNING":
            logger.warning(
                f"PreScoreGate: READY_WITH_WARNING — "
                f"warnings={result.get('failed_warnings', [])}; proceeding."
            )
            break
        if retries >= max_retries:
            logger.error(
                f"PreScoreGate: BLOCKED after {max_retries} retries. "
                f"Failed critical checks: {result.get('failed_critical', [])}"
            )
            return False
        logger.info(
            f"PreScoreGate: BLOCKED — "
            f"failed={result.get('failed_critical', [])}; "
            f"retrying in 5 minutes (attempt {retries + 1}/{max_retries})..."
        )
        time.sleep(300)
        retries += 1

    logger.info("Pre-Score Gate passed. Starting scoring pipeline...")

    # 2. Run eastmoney Strategy
    # Note: eastmoney/run_strategy.py usually takes no args (uses current date/DB)
    # [UPDATED] Export to result/ directory
    if not run_script("eastmoney/run_strategy.py", ["--export", "result"], "eastmoney_strategy"):
        logger.error("Pipeline aborted at eastmoney Strategy.")
        return False

    # 3. Run scoreRank
    # scoreRank/run_daily.py is pinned to the pipeline trade date for deterministic reruns.
    if not run_script("scoreRank/run_daily.py", ["--date", date_str, "--force"], "score_rank"):
        logger.error("Pipeline aborted at scoreRank.")
        return False

    # 4. Backfill any empty industries before downstream strategy selection.
    if not run_script(
        "scripts/backfill_score_rank_daily_industry.py",
        ["--start-date", date_iso, "--end-date", date_iso, "--execute"],
        "score_rank_industry_backfill",
    ):
        logger.error("Pipeline aborted at score_rank_daily industry backfill.")
        return False

    # 5. Persist B-signal consensus scores used by /sina/scores.
    if not run_script("scoreRank/cli/build_bs_consensus.py", ["--date", date_str], "bs_consensus"):
        logger.error("Pipeline aborted at B-signal consensus scoring.")
        return False

    # 5b. Post-Score Gate: validate score data completeness before candidate export.
    logger.info("Post-Score Gate: validating score_rank_daily completeness...")
    post_gate = PostScoreGate(get_engine())
    post_result = post_gate.all_checks(target_date)
    if post_result["status"] == "BLOCKED":
        logger.error(
            f"PostScoreGate: BLOCKED — "
            f"failed critical: {post_result.get('failed_critical', [])}. "
            f"Aborting candidate export and downstream tasks."
        )
        return False
    if post_result["status"] == "READY_WITH_WARNING":
        logger.warning(
            f"PostScoreGate: READY_WITH_WARNING — "
            f"warnings={post_result.get('failed_warnings', [])}; "
            f"proceeding but review recommended."
        )
    else:
        logger.info("PostScoreGate: READY — score data validated.")

    # 5c. Check previous-day health state for today's order permissions.
    from scripts.ops.run_daily_strategy_health_monitor import (
        get_previous_trading_day_health,
        resolve_order_permission,
    )
    try:
        prev_health = get_previous_trading_day_health(get_engine(), date_iso)
    except Exception as _health_exc:
        logger.warning(f"Health gate: could not read previous health state ({_health_exc}); proceeding normally.")
        prev_health = None
    perm = resolve_order_permission(prev_health)
    logger.info(
        f"Health gate: grade={perm['health_grade']} "
        f"allow_new_buys={perm['allow_new_buys']} "
        f"emit_orders={perm['emit_orders']} "
        f"manual_confirmation={perm['manual_confirmation_required']}"
    )
    if perm["freeze_reason"]:
        logger.warning(f"Health gate freeze: {perm['freeze_reason']}")

    # 6. Export trusted full-pool strategy candidates for production review.
    #    Gated by: PostScoreGate (data quality) + previous-day health state.
    _export_args = [
        "--date", date_str,
        "--risk-profile", str(PRODUCTION_CONFIG["risk_profile"]),
        "--strategy", str(PRODUCTION_CONFIG["primary_strategy"]),
        "--top-n", str(PRODUCTION_CONFIG["top_n"]),
        "--max-total-positions", str(PRODUCTION_CONFIG["max_total_positions"]),
        "--write-db",
        "--notify-feishu",
        "--health-grade", perm["health_grade"],
        "--health-date", str(perm["health_date"] or ""),
    ]
    if not perm["emit_orders"]:
        # RED: do NOT pass --emit-orders at all (store_true default is False).
        # Also supersede any unexecuted BUY drafts from previous days so they
        # are not mistakenly displayed as current executable orders.
        logger.warning("Health RED: candidate export runs but order generation is SKIPPED.")
        try:
            from sqlalchemy import text as _text2
            with get_engine().begin() as _conn2:
                _result = _conn2.execute(
                    _text2(
                        "UPDATE chenyiyun.ads_local_strategy_orders "
                        "SET status = 'SUPERSEDED', memo = CONCAT(COALESCE(memo, ''), "
                        "  ' | superseded by health RED freeze on ', :today) "
                        "WHERE side = 'BUY' AND status IN ('PENDING', 'DRAFT') "
                        "  AND created_at < :today"
                    ),
                    {"today": date_iso},
                )
                _superseded = _result.rowcount
            if _superseded:
                logger.warning(
                    f"Health RED: superseded {_superseded} stale BUY draft(s) "
                    f"from previous days."
                )
        except Exception as _cleanup_exc:
            logger.error(f"Health RED: failed to cleanup stale BUY drafts: {_cleanup_exc}")
    else:
        _export_args.append("--emit-orders")
    if perm["manual_confirmation_required"]:
        _export_args.append("--manual-confirmation")
        logger.warning("Health YELLOW: orders require manual confirmation.")
    if not run_script(
        "scripts/ops/export_trusted_strategy_candidates.py",
        _export_args,
        "trusted_strategy_candidates",
    ):
        logger.error("Pipeline aborted at trusted strategy candidate export.")
        return False

    # 7. Review previous signal day's executable quality at today's open.
    if not run_script(
        "scripts/ops/run_trusted_strategy_shadow_monitor.py",
        [
            "--execution-date",
            date_str,
            "--write-db",
            "--notify-feishu",
            "--allow-empty",
        ],
        "trusted_strategy_shadow_monitor",
    ):
        logger.error("Pipeline aborted at trusted strategy shadow monitor.")
        return False

    # 7b. Run daily strategy health monitor — UPDATE health state for next trading day.
    #      Shadow data is now available, so execution quality grading is accurate.
    if not run_script(
        "scripts/ops/run_daily_strategy_health_monitor.py",
        ["--date", date_str, "--notify-feishu"],
        "strategy_health_monitor",
    ):
        logger.error("Pipeline aborted at strategy health monitor.")
        return False

    # 8. Build and push daily trusted strategy performance review.
    if not run_script(
        "scripts/ops/run_strategy_performance_review.py",
        [
            "--date",
            date_str,
            "--notify-feishu",
        ],
        "trusted_strategy_performance_review",
    ):
        logger.error("Pipeline aborted at trusted strategy performance review.")
        return False

    # 9. Build M1 event+kpi tables for strategy stages
    if not run_script("scoreRank/cli/build_b_event_kpi.py", [], "m1_event_kpi"):
        logger.error("Pipeline aborted at M1 Event KPI build.")
        return False

    # 10. Run M8 strategy regression + parameter search and persist
    if not run_script("scoreRank/cli/run_m8_cycle.py", ["--lookback-dates", "60"], "strategy_m8"):
        logger.error("Pipeline aborted at M8 cycle.")
        return False

    # 11. Run Live Sync
    if not run_script("sina/live_tracker/run_live_tracker.py", ["sync"], "live_sync"):
        logger.error("Pipeline aborted at Live Sync.")
        return False

    logger.info("Daily Pipeline completed successfully.")
    return True


# Main Scheduler Loop
# ------------------------------------------------------------------------------
def main():
    logger.info("Scheduler started.")
    
    executed_tasks = {} # Map task_name -> date of last execution

    while True:
        now = datetime.datetime.now()
        today = now.date()
        today_is_trade_day = is_trade_day(today)
        if not today_is_trade_day and now.minute == 0 and now.second < 30:
            logger.info(f"{today} is not a trading day. Scheduled tasks will be marked success-skip.")

        for task_name, config in TASKS.items():
            trigger_time = str(config.get("time") or "").strip()
            if not trigger_time:
                continue

            try:
                trigger_hour, trigger_minute = [int(x) for x in trigger_time.split(":")]
            except Exception:
                logger.error(f"Invalid trigger time for task {task_name}: {trigger_time}")
                continue

            trigger_dt = now.replace(hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0)
            if now < trigger_dt:
                continue

            # Check if already executed today
            last_exec = executed_tasks.get(task_name)
            if last_exec == today:
                continue

            if (now - trigger_dt).total_seconds() > CATCH_UP_GRACE_SECONDS:
                logger.info(
                    f"Skipping stale task: {task_name} scheduled at {trigger_time} "
                    f"(grace {CATCH_UP_GRACE_SECONDS}s)"
                )
                executed_tasks[task_name] = today
                continue

            if config.get("friday_only") and today.weekday() != 4:
                logger.info(f"Triggering task: {task_name} -> success-skip (Friday-only task)")
                executed_tasks[task_name] = today
                continue

            if bool(config.get("trading_day_only", True)) and not today_is_trade_day:
                logger.info(
                    f"Triggering task: {task_name} -> success-skip "
                    f"(non-trading day from chenyiyun.dim_trade_cal)"
                )
                executed_tasks[task_name] = today
                continue

            logger.info(f"Triggering task: {task_name}")

            date_str = today.strftime("%Y%m%d")

            if config.get("type") == "pipeline":
                if not run_pipeline(today):
                    logger.error("Task %s did not complete successfully.", task_name)
            else:
                # Run Script Task
                # For sina and eastmoney main, append date argument
                args = list(config["args"])
                if bool(config.get("append_date", True)):
                    args.append(date_str)
                elif task_name == "sina_bs_image_weekly_cleanup":
                    args.extend(["--date", date_str])
                run_script(config["script"], args, task_name)

            # Mark as executed
            executed_tasks[task_name] = today
        
        # Sleep
        time.sleep(30)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")
