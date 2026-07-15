"""Shared Evidence V4 fixtures for the 10-quarter Cartesian contract."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


V4_WINDOWS = {
    "2024Q1": ("2024-01-01", "2024-03-31"),
    "2024Q2": ("2024-04-01", "2024-06-30"),
    "2024Q3": ("2024-07-01", "2024-09-30"),
    "2024Q4": ("2024-10-01", "2024-12-31"),
    "2025Q1": ("2025-01-01", "2025-03-31"),
    "2025Q2": ("2025-04-01", "2025-06-30"),
    "2025Q3": ("2025-07-01", "2025-09-30"),
    "2025Q4": ("2025-10-01", "2025-12-31"),
    "2026Q1": ("2026-01-01", "2026-03-31"),
    "2026Q2": ("2026-04-01", "2026-06-30"),
}
V4_CORE_EXPERIMENTS = ("P0", "C0", "A7", "A8", "A9", "REV_A7")
V4_RANDOM_EXPERIMENTS = ("RND_TOP30", "RND_FULL")


def write_v4_cartesian_evidence(root: Path, *, include_semantic_files: bool = False) -> None:
    """Write deterministic, complete test evidence without weakening V4 gates."""
    all_dates = pd.DatetimeIndex([])
    for start, end in V4_WINDOWS.values():
        all_dates = all_dates.append(pd.date_range(start, end, freq="B"))
    all_dates = all_dates.drop_duplicates().sort_values()
    nav_values = 1.0 + np.linspace(0.0, 0.20, len(all_dates))
    nav = pd.DataFrame(
        {
            "trade_date": all_dates,
            "nav": nav_values,
            "cash": nav_values * 0.30,
            "market_value": nav_values * 0.70,
            "accrued_cost": 0.0,
        }
    )
    ledger = pd.DataFrame(
        {
            "trade_date": all_dates[::20],
            "symbol": [f"{index:06d}" for index in range(len(all_dates[::20]))],
        }
    )

    for experiment in V4_CORE_EXPERIMENTS:
        exp_dir = root / experiment
        exp_dir.mkdir(parents=True, exist_ok=True)
        nav.to_parquet(exp_dir / "daily_nav.parquet", index=False)
        ledger.to_parquet(exp_dir / "trade_ledger.parquet", index=False)

    for experiment in V4_RANDOM_EXPERIMENTS:
        exp_dir = root / experiment
        exp_dir.mkdir(parents=True, exist_ok=True)
        flat_rows: list[dict[str, object]] = []
        for window in V4_WINDOWS:
            quarter_dir = exp_dir / window
            quarter_dir.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "window": window,
                    "seed_id": f"seed_{seed}",
                    "path_hash": f"{experiment}-{window}-{seed}",
                    "total_return": 0.01 + seed / 100_000,
                }
                for seed in range(95)
            ]
            pd.DataFrame(rows).to_csv(quarter_dir / "random_seed_results.csv", index=False)
            flat_rows.extend(rows)
        pd.DataFrame(flat_rows).to_csv(exp_dir / "random_seed_results.csv", index=False)
        (exp_dir / "status.json").write_text(
            json.dumps({"status": "PASSED"}), encoding="utf-8"
        )

    (root / "calendar_snapshot.json").write_text(
        json.dumps({"trading_dates": [value.date().isoformat() for value in all_dates]}),
        encoding="utf-8",
    )

    if not include_semantic_files:
        return

    factor_states = {
        experiment: {window: {"status": "FITTED"} for window in V4_WINDOWS}
        for experiment in ("A7", "A8", "A9")
    }
    (root / "factor_state_by_fold.json").write_text(
        json.dumps(factor_states), encoding="utf-8"
    )
    decisions = pd.DataFrame(
        {
            "trade_date": all_dates[:5],
            "symbol": [f"{index:06d}" for index in range(5)],
            "rank_score": np.linspace(95.0, 75.0, 5),
            "final_portfolio_weight": 0.14,
        }
    )
    decisions[["trade_date", "symbol", "rank_score"]].to_parquet(
        root / "daily_candidates.parquet", index=False
    )
    decisions[["trade_date", "symbol", "final_portfolio_weight"]].to_parquet(
        root / "daily_weights.parquet", index=False
    )
