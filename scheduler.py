import datetime
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# Configuration
# ------------------------------------------------------------------------------
DB_URL = "mysql+pymysql://root:19871019@localhost:3306/tushare_stock?charset=utf8mb4"
LOG_DIR = Path("logs/scheduler")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Project Paths
PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable

# Task Definitions
# ------------------------------------------------------------------------------
TASKS = {
    "sina_bs": {
        "time": "15:20",
        "script": "Sina/bs_detection/main.py",
        "args": ["config_1"],
        "description": "Sina B/S Detection",
        "type": "script"
    },
    "eastmoney_data": {
        "time": "16:30",
        "script": "Eastmoney/main.py",
        "args": ["config_1"],
        "description": "Eastmoney Data Fetch",
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
            # Query dim_trade_cal. Note: adjust table/schema as needed.
            # Assuming tushare_stock.dim_trade_cal
            result = conn.execute(
                text("SELECT is_open FROM dim_trade_cal WHERE cal_date = :date AND exchange = 'SSE'"),
                {"date": date_str}
            ).fetchone()
            if result:
                return result[0] == 1
            return False
    except Exception as e:
        logger.error(f"Error checking trade day: {e}")
        # Default to False to be safe, or True to retry? 
        # For a scheduler, safer to skip or retry. Let's return False for now.
        return False


def is_data_ready(target_date):
    """Check if tushare_stock.dwd_stock_daily_standard has data for the date."""
    date_str = target_date.strftime("%Y%m%d")
    engine = get_engine()
    try:
        with engine.connect() as conn:
            # Check for a small count
            result = conn.execute(
                text("SELECT count(*) FROM dwd_stock_daily_standard WHERE trade_date = :date"),
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
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
            
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


def run_pipeline(target_date):
    """Run the 21:00 pipeline tasks sequentially."""
    date_str = target_date.strftime("%Y%m%d")
    
    # 1. Wait for Data
    logger.info("Waiting for TuShare data readiness...")
    # Simple retry loop
    max_retries = 24  # 2 hours (5 min * 24)
    retries = 0
    while not is_data_ready(target_date):
        if retries >= max_retries:
             logger.error("Timeout waiting for data readiness.")
             return
        logger.info(f"Data for {date_str} not ready yet. Retrying in 5 minutes...")
        time.sleep(300)
        retries += 1
    
    logger.info("Data is ready! Starting pipeline tasks...")

    # 2. Run Eastmoney Strategy
    # Note: Eastmoney/run_strategy.py usually takes no args (uses current date/DB)
    if not run_script("Eastmoney/run_strategy.py", [], "eastmoney_strategy"):
        logger.error("Pipeline aborted at Eastmoney Strategy.")
        return

    # 3. Run ScoreRank
    # ScoreRank/run_daily.py runs for "latest available date".
    if not run_script("ScoreRank/run_daily.py", [], "score_rank"):
        logger.error("Pipeline aborted at ScoreRank.")
        return

    # 4. Run Live Sync
    if not run_script("Sina/live_tracker/run_live_tracker.py", ["sync"], "live_sync"):
        logger.error("Pipeline aborted at Live Sync.")
        return
        
    logger.info("Daily Pipeline completed successfully.")


# Main Scheduler Loop
# ------------------------------------------------------------------------------
def main():
    logger.info("Scheduler started.")
    
    executed_tasks = {} # Map task_name -> date of last execution

    while True:
        now = datetime.datetime.now()
        today = now.date()
        current_time = now.strftime("%H:%M")

        # Basic check: Is today a trading day?
        if not is_trade_day(today):
            # Log once per hour to avoid spam
            if now.minute == 0 and now.second < 30: # approximate once per hour check
                logger.info(f"{today} is not a trading day. Idling...")
            time.sleep(30)
            continue

        for task_name, config in TASKS.items():
            trigger_time = config.get("time")
            
            # Check if time matches (simple minute precision)
            if current_time == trigger_time:
                # Check if already executed today
                last_exec = executed_tasks.get(task_name)
                if last_exec == today:
                    continue

                logger.info(f"Triggering task: {task_name}")
                
                date_str = today.strftime("%Y%m%d")
                
                if config.get("type") == "pipeline":
                    run_pipeline(today)
                else:
                    # Run Script Task
                    # For Sina and Eastmoney main, append date argument
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
