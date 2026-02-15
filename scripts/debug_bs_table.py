from sqlalchemy import create_engine, text

# Use config from backtest_config if possible, or hardcode
db_url = "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4"
engine = create_engine(db_url)

with engine.connect() as conn:
    try:
        print("--- BS Table Create ---")
        res1 = conn.execute(text("SHOW CREATE TABLE bs_detection_results")).fetchone()
        print(res1[1] if res1 else "Not found")
        
        print("\n--- Stock Table Create ---")
        res2 = conn.execute(text("SHOW CREATE TABLE tushare_stock.dwd_stock_daily_standard")).fetchone()
        print(res2[1] if res2 else "Not found")
        
    except Exception as e:
        print(f"Error: {e}")
