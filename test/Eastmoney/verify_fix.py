
import logging
from eastmoney.EastmoneyController import EastmoneyController
import pandas as pd

# Setup logging
logging.basicConfig(level=logging.INFO)

def verify_fix():
    controller = EastmoneyController()
    stock_code = "000001" # Ping An Bank
    try:
        # We only test the API fetching part, mocking if necessary or just calling it
        # Since we modified _fetch_fund_flow_from_api, we can call it directly if we make it public or access via private
        print(f"Fetching data for {stock_code}...")
        df = controller.fetch_fund_flow(stock_code)
        
        if df.empty:
            print("Error: DataFrame is empty.")
            return

        print("\nLast 5 rows:")
        print(df.tail())
        
        # Check specific values for the latest date (assuming we know what to expect roughly)
        # e.g. Close price should be reasonable (e.g. 10-20), not 1e8
        last_row = df.iloc[-1]
        print("\nLatest Row Data:")
        print(last_row)
        
        close_price = last_row["close_price"]
        main_net = last_row["main_net_amount"]
        
        print(f"\nClose Price: {close_price}")
        print(f"Main Net Amount: {main_net}")
        
        if close_price > 1000 and abs(main_net) < 100:
            print("FAILURE: Close price seems too high and Main Net too low. Mapping might be swapped.")
        elif close_price < 1000 and abs(main_net) > 10000:
            print("SUCCESS: Close price and Main Net Amount ranges look reasonable.")
        else:
            print("WARNING: Manual check required.")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_fix()
