from __future__ import annotations

import numpy as np
import pandas as pd

from post_close_review.backtest import run_t1_backtest
from post_close_review.config import BacktestConfig, PipelineConfig
from post_close_review.pipeline import run_daily_review
from post_close_review.validation import bootstrap_sharpe, compute_rank_ic


def make_demo_data(n_symbols: int = 50, n_days: int = 220, seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    symbols = [f"{i:06d}" for i in range(1, n_symbols + 1)]

    rows = []
    for sym in symbols:
        price = 10 + rng.uniform(0, 30)
        for d in dates:
            ret = rng.normal(0.0006, 0.02)
            open_p = price * (1 + rng.normal(0, 0.004))
            close = max(1.0, open_p * (1 + ret))
            high = max(open_p, close) * (1 + abs(rng.normal(0, 0.01)))
            low = min(open_p, close) * (1 - abs(rng.normal(0, 0.01)))
            amount = float(rng.lognormal(17.5, 0.6))
            volume = max(1000.0, amount / max(close, 0.1))
            rows.append((d, sym, open_p, high, low, close, price, volume, amount))
            price = close

    df = pd.DataFrame(rows, columns=["trade_date", "symbol", "open", "high", "low", "close", "prev_close", "volume", "amount"])
    bm = pd.DataFrame({"trade_date": dates})
    bm["close"] = 4000 * (1 + rng.normal(0.0003, 0.008, size=len(dates))).cumprod()
    return df, bm


def main() -> None:
    price_df, benchmark_df = make_demo_data()
    trade, watch, inventory = run_daily_review(price_df, benchmark_df, PipelineConfig())

    print("Latest trade list:", len(trade))
    print(trade[["trade_date", "symbol", "score_total", "label"]].head(10).to_string(index=False))
    print("Latest watch list:", len(watch))

    # end-to-end panel for validation/backtest
    from post_close_review.factors import compute_raw_factors
    from post_close_review.scoring import score_cross_section

    panel = compute_raw_factors(price_df, benchmark_df, PipelineConfig())
    all_scored = []
    for d, g in panel.groupby("trade_date"):
        g = g.copy()
        g["is_limit_up"] = False
        all_scored.append(score_cross_section(g, PipelineConfig()))
    scored_panel = pd.concat(all_scored, ignore_index=True)

    ic_df = compute_rank_ic(scored_panel, "score_total", horizon=10)
    nav = run_t1_backtest(scored_panel, BacktestConfig())
    stat = bootstrap_sharpe(nav["net_ret"])

    print(f"RankIC mean: {ic_df['rank_ic'].mean():.4f}")
    print(f"Sharpe={stat['sharpe']:.3f}, 95%CI=({stat['ci_low']:.3f}, {stat['ci_high']:.3f})")


if __name__ == "__main__":
    main()
