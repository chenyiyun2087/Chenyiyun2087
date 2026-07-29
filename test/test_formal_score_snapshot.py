from __future__ import annotations

import pandas as pd
import pytest

from runtime.formal_contract import FORMAL_STRATEGIES
from scripts.research_trusted_strategy_account_backtest import (
    _normalize_formal_score_snapshot,
)
from scripts.research.backfill_formal_scores_v2 import select_target_dates


def _cube() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": "2013-01-04",
                "symbol": "000001",
                "strategy": strategy,
                "score": 80 if strategy == FORMAL_STRATEGIES[1] else 70,
                "score_path": "dynamic_factor_score"
                if strategy == FORMAL_STRATEGIES[1]
                else "liquidity_detail_score",
                "source_score": 65,
                "s_liquidity": 20,
                "available_at": "2013-01-04T15:30:00+08:00",
            }
            for strategy in FORMAL_STRATEGIES
        ]
    )


def test_formal_score_cube_collapses_to_shared_features_and_named_scores() -> None:
    result = _normalize_formal_score_snapshot(_cube())
    assert len(result) == 1
    assert result.iloc[0]["score"] == 65
    assert result.iloc[0]["liquidity_detail_score"] == 70
    assert result.iloc[0]["dynamic_factor_score"] == 80


def test_formal_score_cube_rejects_feature_disagreement() -> None:
    frame = _cube()
    frame.loc[0, "s_liquidity"] = 19
    with pytest.raises(ValueError, match="feature_mismatch"):
        _normalize_formal_score_snapshot(frame)


def test_formal_score_backfill_uses_calendar_and_coverage_not_price_dates() -> None:
    calendar = pd.DataFrame(
        {"trade_date": ["2012-01-04", "2013-01-04", "2013-01-07"]}
    )
    coverage = pd.DataFrame(
        {
            "trade_date": ["2013-01-04", "2013-01-07"],
            "coverage_ratio": [0.99, 0.97],
        }
    )
    assert select_target_dates(
        calendar, coverage, threshold=0.98, formal_start="2013-01-01"
    ) == ["2012-01-04", "2013-01-07"]
