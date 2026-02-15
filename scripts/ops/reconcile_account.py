import sys
from pathlib import Path
from datetime import date

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[2]))

from Sina.live_tracker.live_tracker import LiveTracker
from Sina.live_tracker import live_tracker_db as db
from Sina.live_tracker.live_tracker_config import LIVE_CONFIG

def reconcile():
    print("Starting reconciliation...")
    tracker = LiveTracker()
    
    # Reset to initial state
    tracker.cash = LIVE_CONFIG["initial_capital"]
    tracker.positions = {}
    
    # Fetch all trades ordered by date and ID
    trades = db.get_trades()
    trades.sort(key=lambda x: (x['trade_date'], x['id']))
    
    print(f"Found {len(trades)} trades.")
    
    for t in trades:
        symbol = t['symbol']
        shares = t['shares']
        price = float(t['price']) # this is actual_price (with slippage)
        amount = float(t['amount']) # actual_price * shares
        commission = float(t['commission'])
        direction = t['direction']
        
        total_cost = amount + commission
        
        if direction == 'buy':
            tracker.cash -= total_cost
            if symbol in tracker.positions:
                pos = tracker.positions[symbol]
                old_cost = pos.shares * pos.avg_cost
                new_cost = old_cost + amount
                new_shares = pos.shares + shares
                pos.avg_cost = new_cost / new_shares
                pos.shares = new_shares
            else:
                from Sina.live_tracker.live_tracker import LivePosition
                tracker.positions[symbol] = LivePosition(
                    symbol=symbol,
                    name=db.get_stock_name(symbol),
                    shares=shares,
                    avg_cost=amount / shares,
                    entry_date=t['trade_date'],
                    current_price=price
                )
        else:
            net_proceeds = amount - commission
            tracker.cash += net_proceeds
            if symbol in tracker.positions:
                pos = tracker.positions[symbol]
                pos.shares -= shares
                if pos.shares <= 0:
                    del tracker.positions[symbol]
    
    print(f"Reconciled Cash: {tracker.cash:.2f}")
    print(f"Reconciled Positions: {list(tracker.positions.keys())}")
    
    # Update Database
    # 1. Clear positions table for a clean sync
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM live_positions")
        conn.commit()
    finally:
        conn.close()

    # 2. Update positions table
    for symbol, pos in tracker.positions.items():
        db.upsert_position(
            symbol=pos.symbol,
            shares=pos.shares,
            avg_cost=pos.avg_cost,
            entry_date=pos.entry_date,
            name=pos.name,
            current_price=pos.current_price
        )
    
    # 2. Update snapshot
    tracker.calculate_daily_pnl(date.today())
    print("Account record successfully reconciled and persisted.")

if __name__ == "__main__":
    reconcile()
