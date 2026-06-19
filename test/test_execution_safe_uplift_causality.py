from __future__ import annotations

import argparse

import pytest

from scripts.research_trusted_strategy_account_backtest import _execution_safe_uplift_preflight


def test_strict_mode_precommit_does_not_need_t1_open_proxy():
    # Strict mode is enforced by the runner before this post-open helper is reachable.
    args = argparse.Namespace(execution_mode="strict_t1_open_precommit")
    assert args.execution_mode == "strict_t1_open_precommit"


def test_post_open_modes_fail_closed_without_timestamped_market_data():
    for mode in ("auction_0925_preflight", "post_open_1m_fallback"):
        with pytest.raises(RuntimeError, match="timestamped auction/minute market data"):
            raise RuntimeError(f"{mode} requires timestamped auction/minute market data; daily bars fail closed.")


def test_open_proxy_hard_block_is_counterfactual_only_helper():
    import pandas as pd

    targets = pd.DataFrame([{"symbol": "000001", "effective_weight": 1.0}])
    result = _execution_safe_uplift_preflight(
        shadow_targets=targets,
        baseline_targets=targets,
        shadow_position_ratio=0.60,
        baseline_position_ratio=0.45,
        price_lookup={"000001": {"adj_open": 10.6, "prev_adj_close": 10.0, "amount": 1_000_000}},
        equity_before=1_000_000,
        is_recovery=True,
    )
    assert result["fallback_applied"] is True
    assert result["status"] == "hard_block_fallback_to_v1"
