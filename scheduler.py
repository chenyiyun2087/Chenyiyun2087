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

enforce_direct_network()

# Configuration
# ------------------------------------------------------------------------------
DB_URL = "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4"
LOG_DIR = Path("logs/scheduler")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Project Paths
PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable
CATCH_UP_GRACE_SECONDS = int(os.getenv("SCHEDULER_CATCH_UP_GRACE_SECONDS", "90"))

# Task Definitions
# ------------------------------------------------------------------------------
TASKS = {
    "sina_bs": {
        "time": "15:20",
        "script": "sina/bs_detection/main.py",
        "args": ["config_1"],
        "description": "sina B/S Detection",
        "type": "script"
    },
    "eastmoney_data": {
        "time": "16:30",
        "script": "eastmoney/main.py",
        "args": ["config_1"],
        "description": "eastmoney Data Fetch",
        "type": "script"
    },
    "daily_pipeline": {
        "time": "21:00",
        "description": "Daily Data Pipeline",
        "type": "pipeline"
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
        _ENGINE = create_engine(DB_URL, future=True, pool_size=5, max_overflow=10, pool_recycle=3600)
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


def is_data_ready(target_date):
    """Check if tushare_stock.dwd_stock_daily_standard has data for the date."""
    date_str = target_date.strftime("%Y%m%d")
    engine = get_engine()
    try:
        with engine.connect() as conn:
            # Check for a small count
            result = conn.execute(
                text("SELECT count(*) FROM tushare_stock.dwd_stock_daily_standard WHERE trade_date = :date"),
                {"date": date_str}
            ).fetchone()
            return result[0] > 1000  # Assuming >1000 records means success
    except Exception as e:
        logger.error(f"Error checking data readiness: {e}")
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
    
    # 1. Wait for Data
    logger.info("Waiting for TuShare data readiness...")
    # Simple retry loop
    max_retries = 24  # 2 hours (5 min * 24)
    retries = 0
    while not is_data_ready(target_date):
        if retries >= max_retries:
             logger.error("Timeout waiting for data readiness.")
             return False
        logger.info(f"Data for {date_str} not ready yet. Retrying in 5 minutes...")
        time.sleep(300)
        retries += 1
    
    logger.info("Data is ready! Starting pipeline tasks...")

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

    # 6. Export trusted full-pool strategy candidates for production review.
    if not run_script(
        "scripts/ops/export_trusted_strategy_candidates.py",
        [
            "--date",
            date_str,
            "--strategy",
            "tiered_liquidity_then_bs_v2",
            "--top-n",
            "5",
            "--hold-days",
            "10",
            "--max-total-positions",
            "5",
            "--write-db",
            "--emit-orders",
            "--notify-feishu",
        ],
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

    # 8. Build M1 event+kpi tables for strategy stages
    if not run_script("scoreRank/cli/build_b_event_kpi.py", [], "m1_event_kpi"):
        logger.error("Pipeline aborted at M1 Event KPI build.")
        return False

    # 9. Run M8 strategy regression + parameter search and persist
    if not run_script("scoreRank/cli/run_m8_cycle.py", ["--lookback-dates", "60"], "strategy_m8"):
        logger.error("Pipeline aborted at M8 cycle.")
        return False

    # 10. Run Live Sync
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

            if not today_is_trade_day:
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
                args = config["args"] + [date_str]
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
