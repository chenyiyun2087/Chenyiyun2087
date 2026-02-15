import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Mock modules to prevent import errors in different environments
# 1. Mock internal submodules that have heavy dependencies
mock_bspoint_checker = MagicMock()
sys.modules['sina.bs_detection.BSpointChecker'] = mock_bspoint_checker
sys.modules['sina.bs_detection.SinaLatestBSShow'] = MagicMock()

# 2. Mock external dependencies if still needed by SinaBSDetector itself
sys.modules['cv2'] = MagicMock()
sys.modules['pytesseract'] = MagicMock()
sys.modules['selenium'] = MagicMock()
sys.modules['selenium.webdriver'] = MagicMock()
sys.modules['pymysql'] = MagicMock()

# Now import
from sina.bs_detection.SinaBSDetector import normalize_stock_codes, deduplicate_results, analyze_bs_points

class TestBSLogic(unittest.TestCase):
    def test_normalize_stock_codes(self):
        codes = ["1", "6000", "000001", "SH600000"]
        normalized = normalize_stock_codes(codes)
        self.assertEqual(normalized, ["000001", "006000", "000001", "600000"])

    def test_deduplicate_results(self):
        results = [
            {"stock_code": "000001", "val": 1},
            {"stock_code": "000001", "val": 2},
            {"stock_code": "600000", "val": 3}
        ]
        deduped = deduplicate_results(results)
        self.assertEqual(len(deduped), 2)
        # Check that one of 000001 is kept
        codes = [r["stock_code"] for r in deduped]
        self.assertIn("000001", codes)
        self.assertIn("600000", codes)

    def test_analyze_bs_points(self):
        # COORDINATE_THRESHOLD = 1810
        b_points = [(1820, 100, 10, 10), (100, 100, 10, 10)] # One buy signal
        s_points = [(100, 100, 10, 10)] # No sell signal
        
        result = analyze_bs_points(b_points, s_points, "000001")
        
        self.assertTrue(result["has_buy_signal"])
        self.assertFalse(result["has_sell_signal"])
        self.assertEqual(result["buy_points_count"], 1)
        self.assertEqual(result["total_b_points"], 2)

if __name__ == "__main__":
    unittest.main()
