import datetime
import sys
import os
from unittest.mock import MagicMock, patch


def main() -> None:
    # Add project root to path to import scheduler
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    # Mock modules before importing scheduler
    sys.modules["sqlalchemy"] = MagicMock()
    sys.modules["pandas"] = MagicMock()

    with patch("scheduler.is_trade_day", return_value=True), \
         patch("scheduler.is_data_ready", return_value=True), \
         patch("scheduler.run_script", return_value=True) as mock_run_script, \
         patch("time.sleep", side_effect=KeyboardInterrupt):

        import scheduler

        class MockDateTime(datetime.datetime):
            @classmethod
            def now(cls):
                return datetime.datetime(2026, 2, 9, 21, 0, 0)

        scheduler.datetime.datetime = MockDateTime

        print("Starting simulation...")
        try:
            scheduler.main()
        except KeyboardInterrupt:
            print("Simulation stopped.")

        print("\nVerifying calls:")
        expected_calls = [
            "eastmoney/run_strategy.py",
            "scoreRank/run_daily.py",
            "sina/live_tracker/run_live_tracker.py",
        ]

        calls = [args[0][0] for args in mock_run_script.call_args_list]
        for script in expected_calls:
            if script in calls:
                print(f"[PASS] {script} was called.")
            else:
                print(f"[FAIL] {script} was NOT called.")


if __name__ == "__main__":
    main()
