import pandas as pd

from scripts.ops.export_trusted_strategy_candidates import _build_rebalance_orders
from scripts.research_trusted_strategy_account_backtest import (
    _build_dual_system_targets,
    _symbol_from_ts_code,
)


def test_rebalance_orders_respect_min_holding_gate_and_reserve_locked_weight():
    candidates = pd.DataFrame(
        [
            {
                "signal_date": "2026-05-12",
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.5,
                "latest_close": 10.0,
                "strategy": "demo",
            },
            {
                "signal_date": "2026-05-12",
                "symbol": "000002",
                "name": "B",
                "effective_weight": 0.5,
                "latest_close": 20.0,
                "strategy": "demo",
            },
        ]
    )
    positions = {
        "000003": {"shares": 2000, "holding_trade_days": 3},
    }

    orders = _build_rebalance_orders(
        candidates=candidates,
        positions=positions,
        latest_price_lookup={"000003": 10.0},
        total_equity=100000.0,
        lot_size=100,
        min_trade_value=100.0,
        include_sells=True,
        min_holding_days=10,
    )

    assert "000003" in orders.attrs["hold_gate_locked_symbols"]
    assert not ((orders["ts_code"] == "000003") & (orders["side"] == "SELL")).any()
    assert round(float(orders["target_weight"].sum()), 6) == 0.8


def test_symbol_from_ts_code_normalizes_exchange_suffix():
    assert _symbol_from_ts_code("600000.SH") == "600000"
    assert _symbol_from_ts_code("000001.SZ") == "000001"


def test_dual_system_targets_boost_intersection_and_apply_position_ratio():
    day_scores = pd.DataFrame(
        [
            {"trade_date": "2026-06-03", "symbol": "000001", "score": 80, "index_bucket": "index_strong", "market_amount_ratio_20": 1.2},
            {"trade_date": "2026-06-03", "symbol": "000002", "score": 70, "index_bucket": "index_strong", "market_amount_ratio_20": 1.2},
        ]
    )
    chenyiyun = pd.DataFrame(
        [
            {"symbol": "000001", "rank": 1, "rank_score": 80, "effective_weight": 0.5, "name": "A", "industry": "I1"},
            {"symbol": "000002", "rank": 2, "rank_score": 70, "effective_weight": 0.5, "name": "B", "industry": "I2"},
        ]
    )
    ashare = pd.DataFrame(
        [
            {
                "symbol": "000002",
                "strategy_version": "AUTO",
                "source_strategy": "trend_breakout_v1",
                "source_rank": 1,
                "source_score": 90,
                "risk_veto_flag": 0,
                "weekly_confirm_pass": 1,
                "entry_market_regime": "RISK_ON",
            }
        ]
    )

    targets, meta = _build_dual_system_targets(
        signal_date="2026-06-03",
        day_scores=day_scores,
        chenyiyun_targets=chenyiyun,
        ashare_day=ashare,
        top_n=2,
        strategy_name="dual_system_adaptive_route",
    )

    assert meta["target_position_ratio"] == 0.8
    assert meta["dual_intersection_count"] == 1
    assert targets.sort_values("rank").iloc[0]["symbol"] == "000002"


def test_dual_system_targets_freeze_on_high_ashare_veto_ratio():
    day_scores = pd.DataFrame(
        [{"trade_date": "2026-06-03", "symbol": "000001", "score": 80, "index_bucket": "index_neutral", "market_amount_ratio_20": 1.0}]
    )
    ashare = pd.DataFrame(
        [
            {"symbol": "000001", "source_strategy": "AUTO", "source_rank": 1, "source_score": 90, "risk_veto_flag": 1, "weekly_confirm_pass": 1},
            {"symbol": "000002", "source_strategy": "AUTO", "source_rank": 2, "source_score": 80, "risk_veto_flag": 1, "weekly_confirm_pass": 1},
        ]
    )
    targets, meta = _build_dual_system_targets(
        signal_date="2026-06-03",
        day_scores=day_scores,
        chenyiyun_targets=pd.DataFrame(),
        ashare_day=ashare,
        top_n=2,
        strategy_name="dual_system_adaptive_route",
    )

    assert targets.empty
    assert meta["target_position_ratio"] == 0.0
    assert meta["risk_veto_reason"] == "dual_freeze_ashare_crash_or_high_veto"
