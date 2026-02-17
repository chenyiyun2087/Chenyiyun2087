import pymysql
from pathlib import Path
import sys

# Add project root to path to import config
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Adjust path for sina/live_tracker imports
SINA_DIR = REPO_ROOT / "sina"
LIVE_TRACKER_DIR = SINA_DIR / "live_tracker"
sys.path.insert(0, str(LIVE_TRACKER_DIR))

try:
    from live_tracker_config import LIVE_CONFIG
except ImportError:
    # If standard import fails, try relative
    sys.path.append(str(LIVE_TRACKER_DIR))
    from live_tracker_config import LIVE_CONFIG

def reset_db():
    db_url = LIVE_CONFIG["db_url"]
    parts = db_url.replace("mysql+pymysql://", "").split("@")
    user_pass = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    db_name = host_db[1].split("?")[0]

    conn = pymysql.connect(
        host=host_port[0],
        port=int(host_port[1]) if len(host_port) > 1 else 3306,
        user=user_pass[0],
        password=user_pass[1] if len(user_pass) > 1 else "",
        db=db_name,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        with conn.cursor() as cursor:
            # Table to truncate
            tables = [
                'live_trades',
                'live_positions',
                'live_daily_snapshots',
                'live_signals'
            ]
            
            print("Resetting database...")
            # Disable foreign key checks if any (InnoDB)
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            
            for table in tables:
                print(f"Truncating {table}...")
                cursor.execute(f"TRUNCATE TABLE {table};")
            
            # Insert initial snapshot for today
            from datetime import date
            today = date.today()
            initial_cap = LIVE_CONFIG["initial_capital"]
            print(f"Initializing balance for {today} with ¥{initial_cap:,.2f}...")
            cursor.execute("""
                INSERT INTO live_daily_snapshots (snapshot_date, cash, positions_value, total_equity, created_at)
                VALUES (%s, %s, %s, %s, NOW())
            """, (today, initial_cap, 0, initial_cap))
            
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
            conn.commit()
            print("Successfully reset all live tracker data.")
            
    finally:
        conn.close()

if __name__ == "__main__":
    reset_db()
