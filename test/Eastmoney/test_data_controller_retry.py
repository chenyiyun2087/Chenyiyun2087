import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from eastmoney.data_controller import DuokongSnapshot, _worker_task  # noqa: E402


class TestEastmoneyRetry(unittest.TestCase):
    def test_worker_retries_with_driver_refresh(self):
        calls = []

        def fake_fetch(code, debug=False, force_driver_refresh=False):
            calls.append((code, debug, force_driver_refresh))
            if len(calls) == 1:
                raise RuntimeError("Timeout")
            return DuokongSnapshot(code=code, bulls_percent=60.0, bears_percent=40.0), 0.1

        with patch("eastmoney.data_controller._fetch_duokong_snapshot_selenium", side_effect=fake_fetch):
            result = _worker_task("000001", debug=False, max_retries=1)

        self.assertIsNotNone(result.snapshot)
        self.assertIsNone(result.error)
        self.assertEqual([item[2] for item in calls], [False, True])

    def test_worker_returns_last_error_after_retry_budget(self):
        with patch(
            "eastmoney.data_controller._fetch_duokong_snapshot_selenium",
            side_effect=RuntimeError("Timeout"),
        ):
            result = _worker_task("000001", debug=False, max_retries=1)

        self.assertIsNone(result.snapshot)
        self.assertIn("Timeout", result.error)


if __name__ == "__main__":
    unittest.main()
