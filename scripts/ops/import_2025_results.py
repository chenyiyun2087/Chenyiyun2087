import os
import pandas as pd
import pymysql
from datetime import datetime
import glob
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_pymysql_config

# Constants
RESULT_DIR = "/Users/chenyiyun/PycharmProjects/Chenyiyun2087/sina/bs_detection/SinaAppBS/result/"
MYSQL_CONFIG = build_pymysql_config(dict_cursor=False)
MYSQL_CONFIG["autocommit"] = True

# Column Mapping (Excel -> DB)
COLUMN_MAP = {
    '股票代码': 'stock_code',
    '有买点信号': 'has_buy_signal',
    '有卖点信号': 'has_sell_signal',
    '买点信号描述': 'buy_signal_description',
    '卖点信号描述': 'sell_signal_description',
    '总B点数量': 'total_b_points',
    '总S点数量': 'total_s_points',
    '买点信号数量': 'buy_points_count',
    '卖点信号数量': 'sell_points_count',
    '处理时间': 'process_time',
    '图片路径': 'image_path'
}

INSERT_SQL = """
INSERT INTO bs_detection_results (
    batch_name,
    batch_date,
    stock_code,
    has_buy_signal,
    has_sell_signal,
    buy_signal_description,
    sell_signal_description,
    total_b_points,
    total_s_points,
    buy_points_count,
    sell_points_count,
    process_time,
    image_path,
    created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    has_buy_signal = VALUES(has_buy_signal),
    has_sell_signal = VALUES(has_sell_signal),
    buy_signal_description = VALUES(buy_signal_description),
    sell_signal_description = VALUES(sell_signal_description),
    total_b_points = VALUES(total_b_points),
    total_s_points = VALUES(total_s_points),
    buy_points_count = VALUES(buy_points_count),
    sell_points_count = VALUES(sell_points_count),
    process_time = VALUES(process_time),
    image_path = VALUES(image_path),
    created_at = VALUES(created_at);
"""

def import_excel_files():
    # Find all 2025 Excel files
    files = glob.glob(os.path.join(RESULT_DIR, "2025*.xlsx"))
    files.sort()
    
    if not files:
        print("No Excel files starting with 2025 found.")
        return

    print(f"Found {len(files)} files to import.")
    
    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        total_records = 0
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        for file_path in files:
            file_name = os.path.basename(file_path)
            # Batch date is YYYYMMDD from filename
            batch_date = file_name.split('.')[0]
            if '_' in batch_date:
                parts = batch_date.split('_')
                batch_name = parts[0]
                batch_date = parts[1]
            else:
                batch_name = "default"
            
            print(f"Importing {file_name} (Batch: {batch_name}, Date: {batch_date})...")
            
            try:
                df = pd.read_excel(file_path, engine='openpyxl')
                df = df.where(pd.notnull(df), None) # Handle NaNs
                
                rows_to_insert = []
                for _, row in df.iterrows():
                    rows_to_insert.append((
                        batch_name,
                        batch_date,
                        str(row['股票代码']).zfill(6),
                        int(bool(row['有买点信号'])),
                        int(bool(row['有卖点信号'])),
                        row.get('买点信号描述'),
                        row.get('卖点信号描述'),
                        row.get('总B点数量'),
                        row.get('总S点数量'),
                        row.get('买点信号数量'),
                        row.get('卖点信号数量'),
                        row.get('处理时间'),
                        row.get('图片路径'),
                        now_str
                    ))
                
                if rows_to_insert:
                    cursor.executemany(INSERT_SQL, rows_to_insert)
                    total_records += len(rows_to_insert)
                    print(f"  Loaded {len(rows_to_insert)} records.")
            
            except Exception as e:
                print(f"  Error importing {file_name}: {e}")
                continue
                
        conn.commit()
        print(f"\nImport complete. Total records processed: {total_records}")
        
    except Exception as e:
        print(f"Database connection error: {e}")
    finally:
        if 'conn' in locals() and conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    import_excel_files()
