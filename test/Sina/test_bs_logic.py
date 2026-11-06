import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Test isolation: save real modules first so mocks do not leak into other test modules
_real_bspoint_checker = sys.modules.get('sina.bs_detection.BSpointChecker')
_real_latest_bs_show = sys.modules.get('sina.bs_detection.SinaLatestBSShow')
_real_cv2 = sys.modules.get('cv2')
_real_pytesseract = sys.modules.get('pytesseract')
_real_selenium = sys.modules.get('selenium')
_real_selenium_webdriver = sys.modules.get('selenium.webdriver')
_real_pymysql = sys.modules.get("pymysql")

# Mock modules to prevent import errors in different environments
mock_bspoint_checker = MagicMock()
sys.modules['sina.bs_detection.BSpointChecker'] = mock_bspoint_checker
sys.modules['sina.bs_detection.SinaLatestBSShow'] = MagicMock()
sys.modules['cv2'] = MagicMock()
sys.modules['pytesseract'] = MagicMock()
sys.modules['selenium'] = MagicMock()
sys.modules['selenium.webdriver'] = MagicMock()
sys.modules['pymysql'] = MagicMock()

# Now import
from sina.bs_detection.SinaBSDetector import (
    analyze_bs_points,
    deduplicate_results,
    ensure_tesseract_available,
    normalize_stock_codes,
)

# Test isolation: restore real modules after import chain that needed mocks.
# SinaBSDetector's own namespace already holds references to the mocks,
# so this file's tests remain isolated while later files get the real packages.
for _mod_name, _real_mod in [
    ("sina.bs_detection.BSpointChecker", _real_bspoint_checker),
    ("sina.bs_detection.SinaLatestBSShow", _real_latest_bs_show),
    ("cv2", _real_cv2),
    ("pytesseract", _real_pytesseract),
    ("selenium", _real_selenium),
    ("selenium.webdriver", _real_selenium_webdriver),
    ("pymysql", _real_pymysql),
]:
    if _real_mod is not None:
        sys.modules[_mod_name] = _real_mod
    else:
        sys.modules.pop(_mod_name, None)

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

    @patch("sina.bs_detection.SinaBSDetector.os.access", return_value=True)
    @patch("sina.bs_detection.SinaBSDetector.os.path.isfile", return_value=True)
    @patch("sina.bs_detection.SinaBSDetector.shutil.which", return_value=None)
    @patch.dict("os.environ", {"TESSERACT_CMD": "/custom/tesseract"})
    def test_tesseract_env_override(self, _which, _isfile, _access):
        self.assertEqual(ensure_tesseract_available(), "/custom/tesseract")

    @patch("sina.bs_detection.SinaBSDetector.os.access", return_value=False)
    @patch("sina.bs_detection.SinaBSDetector.os.path.isfile", return_value=False)
    @patch("sina.bs_detection.SinaBSDetector.shutil.which", return_value=None)
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_tesseract_fails_before_batch_write(self, _which, _isfile, _access):
        with self.assertRaisesRegex(RuntimeError, "Tesseract OCR不可用"):
            ensure_tesseract_available()

if __name__ == "__main__":
    unittest.main()
