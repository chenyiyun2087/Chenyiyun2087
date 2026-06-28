from datetime import date

import numpy as np
import pandas as pd

from scripts.ops.daily_batch_audit import ExpectedTask, classify_task
from scripts.research.review_orders_forward_performance import (
    build_forward_schedule,
    compute_forward_returns,
    summarize_by_signal_date,
)


class EmptyCursor:
    def execute(self, sql, params=()):
        return None

    def fetchone(self):
        return None


def test_weekly_task_skips_outside_configured_weekday():
    task = ExpectedTask("weekly_cleanup", "22:05", "cleanup.py", False, "weekly", 4)
    row = classify_task(EmptyCursor(), task, "20260622", trading_day=True)
    assert row["status"] == "SKIPPED_SCHEDULE"
    assert row["replay_required"] == 0


def test_forward_week_requires_all_five_trading_days():
    orders = pd.DataFrame([{"trade_date": 20260625, "ts_code": "000001", "strategy": "prod"}])
    calendar = [20260625, 20260626, 20260629]
    schedule, _ = build_forward_schedule(orders, calendar)
    prices = pd.DataFrame(
        [
            {"ts_code": "000001", "trade_date": 20260626, "adj_open": 10.0, "adj_close": 10.2},
            {"ts_code": "000001", "trade_date": 20260629, "adj_open": 10.2, "adj_close": 10.4},
        ]
    )
    result = compute_forward_returns(orders, prices, schedule).iloc[0]
    assert np.isnan(result["forward_ret_1w"])
    assert result["observation_status"] == "observing"


def test_win_rate_excludes_observing_rows():
    frame = pd.DataFrame(
        {
            "trade_date": [20260601, 20260601, 20260601],
            "ts_code": ["000001", "000002", "000003"],
            "forward_ret_d1": [0.01, -0.01, np.nan],
            "forward_ret_d2": [0.01, -0.01, np.nan],
            "forward_ret_d3": [0.01, -0.01, np.nan],
            "forward_ret_d4": [0.01, -0.01, np.nan],
            "forward_ret_d5": [0.01, -0.01, np.nan],
            "forward_ret_1w": [0.05, -0.02, np.nan],
            "forward_max_ret": [0.06, 0.01, np.nan],
        }
    )
    summary = summarize_by_signal_date(frame).iloc[0]
    assert summary["win_rate_1w"] == 0.5

