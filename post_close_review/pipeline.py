from __future__ import annotations

import pandas as pd

from .config import PipelineConfig
from .factors import compute_raw_factors
from .inventory import InventoryStateMachine
from .scoring import score_cross_section


def _board_limit(symbol: str, board_limit: dict[str, float]) -> float:
    code = str(symbol)
    for prefix, limit in board_limit.items():
        if prefix != "default" and code.startswith(prefix):
            return limit
    return board_limit["default"]


def run_daily_review(
    price_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    cfg: PipelineConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cfg = cfg or PipelineConfig()

    panel = compute_raw_factors(price_df, benchmark_df, cfg)
    inv = InventoryStateMachine()
    scored_days: list[pd.DataFrame] = []

    for date in sorted(panel["trade_date"].unique()):
        day = panel[panel["trade_date"] == date].copy()
        inv_state = inv.update(day)
        active = inv_state[inv_state["status"] == "in"]["symbol"].tolist()
        if not active:
            continue

        day = day[day["symbol"].isin(active)].copy()
        day["limit_up"] = day["symbol"].map(lambda x: _board_limit(x, cfg.board_limit))
        day["is_limit_up"] = day["close"] >= day["prev_close"] * (1 + day["limit_up"] - 1e-3)

        scored = score_cross_section(day, cfg)
        scored_days.append(scored)

    scored_panel = pd.concat(scored_days, ignore_index=True) if scored_days else pd.DataFrame()
    if scored_panel.empty:
        return scored_panel, scored_panel, inv.state

    last_date = scored_panel["trade_date"].max()
    latest = scored_panel[scored_panel["trade_date"] == last_date].copy()
    trade = latest[latest["label"] == "trade"].sort_values("score_total", ascending=False)
    watch = latest[latest["label"] == "watch"].sort_values("score_total", ascending=False)
    return trade, watch, inv.state
