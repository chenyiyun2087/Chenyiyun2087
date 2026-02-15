import unittest
import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from ScoreRank.perf_utils import enrich_scored_with_market_metrics

class TestPerfUtils(unittest.TestCase):
    def setUp(self):
        # Create dummy scored dataframe
        self.scored = pd.DataFrame({
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "score": [80.0, 60.0],
            "buy_point_close": [10.0, 20.0]
        })
        
        # Create dummy features dataframe
        self.features = pd.DataFrame({
            "symbol": ["000001", "600000", "000001", "600000"],
            "trade_date": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
            "close": [10.5, 21.0, 11.0, 22.0],
            "ret1": [0.05, 0.05, 0.047, 0.047] # ret1 = (11-10.5)/10.5 approx 0.047
        })

    def test_enrich_scored_with_market_metrics(self):
        enriched = enrich_scored_with_market_metrics(self.scored, self.features)
        
        self.assertIn("close_price", enriched.columns)
        self.assertIn("is_limit_up", enriched.columns)
        self.assertIn("price_change_ratio", enriched.columns)
        
        # Check values for 000001
        # Latest close is 11.0. Buy point was 10.0.
        # Ratio should be (11 - 10) / 10 * 100 = 10.0
        row_000001 = enriched[enriched["symbol"] == "000001"].iloc[0]
        self.assertEqual(row_000001["close_price"], 11.0)
        self.assertAlmostEqual(row_000001["price_change_ratio"], 10.0)
        
        # is_limit_up should be 0 because 4.7% < 9.5%
        self.assertEqual(row_000001["is_limit_up"], 0)

    def test_limit_up_detection(self):
        # Create a feature indicating limit up
        limit_up_features = pd.DataFrame({
            "symbol": ["000001"],
            "trade_date": ["2026-01-02"],
            "close": [11.0],
            "ret1": [0.1] # 10% > 9.5%
        })
        enriched = enrich_scored_with_market_metrics(self.scored, limit_up_features)
        row = enriched[enriched["symbol"] == "000001"].iloc[0]
        self.assertEqual(row["is_limit_up"], 1)

if __name__ == "__main__":
    unittest.main()
