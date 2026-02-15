
import logging
import sys
import os
import pandas as pd

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Eastmoney.EastmoneyController import EastmoneyController

# Configure logging to show info
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_margin_trading():
    stock_code = "301251"
    print(f"Testing Margin Trading fetch for {stock_code}...")
    
    # Initialize controller (no DB config needed if just fetching, but init requires it or none)
    # We pass None for mysql_config if we just want to test fetch, 
    # but sync needs DB. Let's test fetch first to see data.
    controller = EastmoneyController()  # Use default DB config
    
    try:
        # 1. Test Sync (Insert)
        print(f"Syncing data for {stock_code} to database...")
        inserted_count = controller.sync_margin_trading(stock_code)
        print(f"Sync complete. Inserted/Updated {inserted_count} records.")

        # 2. Verify by Fetching (optional, but good for visual)
        # Note: sync_margin_trading already fetches. We can query DB or just trust the count.
        # Let's fetch again to show what data we worked with (from API)
        df = controller.fetch_margin_trading(stock_code)
        if not df.empty:
            print("Last 5 records fetched from API:")
            cols = [
                "trade_date", "close_price", "change_pct", 
                "rzye", "rzye_ratio", "rzmre", "rzche", "rzjme", 
                "rqye", "rqyl", "rqmcl", "rqchl", "rqjmg", 
                "rzrqye", "rzrqye_diff"
            ]
            available_cols = [c for c in cols if c in df.columns]
            print(df.tail()[available_cols].to_string())
        else:
            print("No data found or fetch failed.")

    except Exception as e:
        print(f"Error during test: {e}")

if __name__ == "__main__":
    test_margin_trading()
