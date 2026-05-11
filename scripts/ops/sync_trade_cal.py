import pymysql
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_pymysql_config

# Database configurations
DB_CONFIG_SRC = build_pymysql_config(dict_cursor=True)
DB_CONFIG_SRC["database"] = "tushare_stock"

DB_CONFIG_DST = build_pymysql_config(dict_cursor=True)

def sync_trade_cal():
    print("Starting trade calendar synchronization...")
    try:
        # Connect to databases
        conn_src = pymysql.connect(**DB_CONFIG_SRC)
        conn_dst = pymysql.connect(**DB_CONFIG_DST)
        
        with conn_src.cursor() as cursor_src, conn_dst.cursor() as cursor_dst:
            # 1. Create target table if it doesn't exist
            print("Ensuring target table dim_trade_cal exists in chenyiyun...")
            cursor_dst.execute("""
                CREATE TABLE IF NOT EXISTS dim_trade_cal (
                    exchange VARCHAR(8) NOT NULL,
                    cal_date VARCHAR(16) NOT NULL,
                    is_open TINYINT NOT NULL,
                    pretrade_date VARCHAR(16) NULL,
                    PRIMARY KEY (exchange, cal_date),
                    KEY idx_cal_date (cal_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            
            # 2. Fetch data from source
            print("Fetching data from tushare_stock.dim_trade_cal...")
            cursor_src.execute("SELECT exchange, cal_date, is_open, pretrade_date FROM dim_trade_cal")
            rows = cursor_src.fetchall()
            print(f"Fetched {len(rows)} records.")
            
            # 3. Insert/Update into destination
            print("Inserting/Updating records into chenyiyun.dim_trade_cal...")
            sql = """
                INSERT INTO dim_trade_cal (exchange, cal_date, is_open, pretrade_date)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_open = VALUES(is_open),
                    pretrade_date = VALUES(pretrade_date)
            """
            
            # Batch process for efficiency
            batch_size = 1000
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                values = [(r['exchange'], r['cal_date'], r['is_open'], r['pretrade_date']) for r in batch]
                cursor_dst.executemany(sql, values)
            
            conn_dst.commit()
            print("Synchronization complete!")
            
        conn_src.close()
        conn_dst.close()
        return True
    except Exception as e:
        print(f"Error during synchronization: {e}")
        return False

if __name__ == "__main__":
    if sync_trade_cal():
        sys.exit(0)
    else:
        sys.exit(1)
