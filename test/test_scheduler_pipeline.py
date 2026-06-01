import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import scheduler  # noqa: E402


class TestSchedulerPipeline(unittest.TestCase):
    def test_run_pipeline_returns_true_and_runs_full_order(self):
        target_date = datetime.date(2026, 5, 11)

        with patch("scheduler.is_data_ready", return_value=True), patch("scheduler.run_script", return_value=True) as run_script:
            self.assertTrue(scheduler.run_pipeline(target_date))

        self.assertEqual(
            [call.args[0] for call in run_script.call_args_list],
            [
                "eastmoney/run_strategy.py",
                "scoreRank/run_daily.py",
                "scoreRank/cli/build_bs_consensus.py",
                "scoreRank/cli/build_b_event_kpi.py",
                "scoreRank/cli/run_m8_cycle.py",
                "sina/live_tracker/run_live_tracker.py",
            ],
        )

    def test_run_pipeline_returns_false_on_first_failed_stage(self):
        target_date = datetime.date(2026, 5, 11)

        with patch("scheduler.is_data_ready", return_value=True), patch(
            "scheduler.run_script",
            side_effect=[True, False],
        ) as run_script:
            self.assertFalse(scheduler.run_pipeline(target_date))

        self.assertEqual(
            [call.args[0] for call in run_script.call_args_list],
            ["eastmoney/run_strategy.py", "scoreRank/run_daily.py"],
        )


if __name__ == "__main__":
    unittest.main()
