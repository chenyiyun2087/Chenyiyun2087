import json
import pandas as pd
import os

def export_trades():
    json_path = "backtest/results/chenyiyun_full_2020_2026.json"
    csv_path = "backtest/results/chenyiyun_trades_2020_2026.csv"
    
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return
        
    with open(json_path, 'r') as f:
        data = json.load(f)
        
    trades = data.get("trades", [])
    if not trades:
        print("No trades found in the backtest result.")
        return
        
    df = pd.DataFrame(trades)
    
    # Save to CSV
    df.to_csv(csv_path, index=False)
    print(f"Successfully exported {len(df)} trades to {csv_path}")
    
    # Print the whole transaction list
    print("\n" + "="*80)
    print("FULL TRANSACTION LIST")
    print("="*80)
    pd.set_option('display.max_rows', None)  # Ensure all rows are printed
    pd.set_option('display.width', 1000)
    print(df.to_string(index=False))
    print("="*80)
    print(f"Total Transactions: {len(df)}")

if __name__ == "__main__":
    export_trades()
