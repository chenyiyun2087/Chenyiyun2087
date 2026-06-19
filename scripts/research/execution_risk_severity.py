"""Shared execution-risk severity helpers for research shadow reports."""

from __future__ import annotations

from typing import Mapping

import pandas as pd


LARGE_SLIPPAGE_WARNING_THRESHOLD = 0.03
OPEN_GAP_HARD_BLOCK_THRESHOLD = 0.05
LIMIT_UP_BUY_HARD_BLOCK_THRESHOLD = 0.20
LIMIT_DOWN_SELL_HARD_BLOCK_THRESHOLD = 0.20
TURNOVER_IMPACT_HARD_BLOCK_THRESHOLD = 0.03


def _number(value: object) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(number) else float(number)


def execution_hard_block_reasons(row: Mapping[str, object]) -> list[str]:
    reasons: list[str] = []
    open_gap = _number(row.get("open_gap_proxy"))
    if open_gap is not None and abs(open_gap) > OPEN_GAP_HARD_BLOCK_THRESHOLD:
        reasons.append("open_gap_proxy")
    limit_up = _number(row.get("limit_up_buy_ratio"))
    if limit_up is not None and limit_up > LIMIT_UP_BUY_HARD_BLOCK_THRESHOLD:
        reasons.append("limit_up_buy_ratio")
    limit_down = _number(row.get("limit_down_sell_ratio"))
    if limit_down is not None and limit_down > LIMIT_DOWN_SELL_HARD_BLOCK_THRESHOLD:
        reasons.append("limit_down_sell_ratio")
    turnover = _number(row.get("estimated_turnover_impact"))
    if turnover is not None and turnover > TURNOVER_IMPACT_HARD_BLOCK_THRESHOLD:
        reasons.append("estimated_turnover_impact")
    return reasons


def execution_warning_reasons(row: Mapping[str, object]) -> list[str]:
    if execution_hard_block_reasons(row):
        return []
    large_slippage = _number(row.get("large_slippage_proxy"))
    if large_slippage is not None and large_slippage > LARGE_SLIPPAGE_WARNING_THRESHOLD:
        return ["large_slippage_proxy"]
    return []


def execution_severity(row: Mapping[str, object]) -> dict[str, object]:
    hard_reasons = execution_hard_block_reasons(row)
    warning_reasons = execution_warning_reasons(row)
    return {
        "execution_hard_block": bool(hard_reasons),
        "execution_slippage_warning": bool(warning_reasons),
        "execution_hard_block_reasons": "|".join(hard_reasons),
        "execution_warning_reasons": "|".join(warning_reasons),
        "execution_v22_severity": "hard_block" if hard_reasons else ("warning" if warning_reasons else "pass"),
    }


def add_execution_severity_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        for col in (
            "execution_hard_block",
            "execution_slippage_warning",
            "execution_hard_block_reasons",
            "execution_warning_reasons",
            "execution_v22_severity",
        ):
            out[col] = pd.Series(dtype=object)
        return out
    severity = pd.DataFrame([execution_severity(row) for row in out.to_dict("records")], index=out.index)
    for col in severity.columns:
        out[col] = severity[col]
    return out

