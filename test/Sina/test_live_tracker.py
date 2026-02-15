import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import date
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Mock the entire db module used by LiveTracker
mock_db = MagicMock()
sys.modules['live_tracker_db'] = mock_db
sys.modules['sina.live_tracker.live_tracker_db'] = mock_db

# Mock live_tracker_config
sys.modules['live_tracker_config'] = MagicMock()
sys.modules['sina.live_tracker.live_tracker_config'] = MagicMock()

# Now import LiveTracker
from sina.live_tracker.live_tracker import LiveTracker

class TestLiveTracker(unittest.TestCase):
    def setUp(self):
        # Clear mock calls
        mock_db.reset_mock()
        
        # Default behavior for setup (_load_state)
        mock_db.get_all_positions.return_value = []
        mock_db.get_latest_snapshot.return_value = None
        
        # Mock LIVE_CONFIG inside the module if needed
        with patch('sina.live_tracker.live_tracker.LIVE_CONFIG', {
            "initial_capital": 1000000.0,
            "commission": 0.001,
            "slippage": 0.001
        }):
            self.tracker = LiveTracker()

    def test_record_buy(self):
        mock_db.get_latest_price.return_value = 10.0
        mock_db.get_stock_name.return_value = "平安银行"
        
        self.tracker.record_buy(
            symbol="000001",
            price=10.0,
            shares=1000,
            trade_date=date(2026, 2, 1),
            reason="test buy"
        )
        
        # Verify db.insert_trade was called
        self.assertTrue(mock_db.insert_trade.called)
        # Verify db.upsert_position was called
        self.assertTrue(mock_db.upsert_position.called)

    def test_get_positions_value(self):
        # Manually inject a position since _load_state was mocked to return empty
        from sina.live_tracker.live_tracker import LivePosition
        self.tracker.positions = {
            "000001": LivePosition(
                symbol="000001",
                name="平安银行",
                shares=1000,
                avg_cost=10.0,
                entry_date=date(2026, 1, 1),
                current_price=11.0
            )
        }
        
        val = self.tracker.get_positions_value()
        self.assertEqual(val, 11000.0)

if __name__ == "__main__":
    unittest.main()
