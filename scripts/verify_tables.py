import sys
from pathlib import Path
from sqlalchemy import create_engine, text

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoreRank.core.config import CONFIG

def verify_tables():
    # Construct connection string to tushare_stock explicitly if needed, 
    # but CONFIG likely points to a user DB.
    # However, db_io.py uses "tushare_stock.dim_stock".
    # Let's try to connect and query tushare_stock tables.
    
    # We assume the user has access to tushare_stock database on the same host
    # The URL in CONFIG might be for 'chenyiyun', so we just create engine from it
    # and reference tushare_stock.table_name
    
    print(f"Connecting to DB using config...")
    engine = create_engine(CONFIG["db_url"], future=True)
    
    tables_to_check = [
        # Check tushare_stock with correct prefixes
        "tushare_stock.ods_margin_detail",    # Margin Detail
        "tushare_stock.dwd_daily_basic",
        "tushare_stock.dwd_fina_indicator" 
    ]
    
    
    with engine.connect() as conn:
        for table in tables_to_check:
            try:
                # Get column info
                sql = f"SHOW COLUMNS FROM {table}"
                result = conn.execute(text(sql)).fetchall()
                print(f"\n[OK] Table '{table}' columns:")
                cols = [row[0] for row in result]
                print(cols)
                
                # Sample data
                sql = f"SELECT * FROM {table} LIMIT 1"
                result = conn.execute(text(sql)).mappings().fetchone()
                if result:
                    print(f"Sample: {dict(result)}")
            except Exception as e:
                print(f"[FAIL] Table '{table}' access failed: {e}")

if __name__ == "__main__":
    verify_tables()
