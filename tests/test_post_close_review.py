import pandas as pd

from post_close_review.config import PipelineConfig
from post_close_review.example_run import make_demo_data
from post_close_review.pipeline import run_daily_review


def test_pipeline_outputs_trade_watch_inventory():
    price_df, bm = make_demo_data(n_symbols=20, n_days=150, seed=1)
    trade, watch, inv = run_daily_review(price_df, bm, PipelineConfig())

    assert isinstance(trade, pd.DataFrame)
    assert isinstance(watch, pd.DataFrame)
    assert isinstance(inv, pd.DataFrame)
    assert {"symbol", "status", "ret_since_in"}.issubset(inv.columns)
    # At least one stock should have been evaluated in inventory after warmup window
    assert len(inv) > 0
