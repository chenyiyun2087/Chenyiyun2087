"""
eastmoney daily run task.
Chains eastmoney.main (sentiment scanning) and run_strategy.py (strategy execution).
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime, timedelta

def get_recent_trading_day():
    """
    Returns the most recent trading day.
    - If today is Sat/Sun, returns last Friday.
    - If today is mid-week but before 16:30 (data usually ready after close),
      returns the previous trading day.
    """
    dt = datetime.now()
    # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    weekday = dt.weekday()
    
    # If it's a weekday but before 16:30, we likely want "yesterday" (or last Friday)
    # as today's post-market data might not be ready.
    if weekday < 5 and dt.hour < 16 or (dt.hour == 16 and dt.minute < 30):
        dt -= timedelta(days=1)
        weekday = dt.weekday() # Update weekday after shift

    # Now handle weekends from the shifted (or original) date
    if weekday == 5: # Saturday
        dt -= timedelta(days=1)
    elif weekday == 6: # Sunday
        dt -= timedelta(days=2)
        
    return dt

def main():
    parser = argparse.ArgumentParser(description="eastmoney Daily Run Task")
    parser.add_argument("date", nargs="?", help="Trading date in YYYYMMDD or YYYY-MM-DD (default: most recent trading day)")
    args = parser.parse_args()

    if args.date:
        # Normalize date format
        date_clean = args.date.replace("-", "")
        try:
            dt = datetime.strptime(date_clean, "%Y%m%d")
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYYMMDD or YYYY-MM-DD.")
            sys.exit(1)
    else:
        dt = get_recent_trading_day()
    
    date_yyyymmdd = dt.strftime("%Y%m%d")
    date_dash = dt.strftime("%Y-%m-%d")

    # Get absolute paths to ensure scripts are found
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    strategy_script = os.path.join(current_dir, "run_strategy.py")

    print(f"=== Starting eastmoney Daily Run for {date_dash} ===")
    
    # Set up environment with project root in PYTHONPATH
    env = os.environ.copy()
    if project_root not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

    # 1. Run eastmoney.main config_1
    # We run it as a module from the project root
    cmd1 = [sys.executable, "-m", "eastmoney.main", "config_1", date_yyyymmdd]
    print(f"\n[Step 1/2] Executing: {' '.join(cmd1)}")
    res1 = subprocess.run(cmd1, cwd=project_root, env=env)
    
    if res1.returncode != 0:
        print(f"\nError: eastmoney.main failed with return code {res1.returncode}")
        sys.exit(res1.returncode)

    # 2. Run eastmoney/run_strategy.py
    cmd2 = [sys.executable, strategy_script, "--date", date_dash]
    print(f"\n[Step 2/2] Executing: {' '.join(cmd2)}")
    res2 = subprocess.run(cmd2, cwd=project_root, env=env)
    
    if res2.returncode != 0:
        print(f"\nError: run_strategy.py failed with return code {res2.returncode}")
        sys.exit(res2.returncode)

    print(f"\n=== eastmoney Daily Run for {date_dash} completed successfully! ===")

if __name__ == "__main__":
    main()
