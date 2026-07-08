import argparse

import pandas as pd

from scripts.ops import export_trusted_strategy_candidates as export_candidates_mod
from scripts.ops.export_trusted_strategy_candidates import (
    _apply_risk_profile_defaults,
    _build_canary_orders,
    _build_rebalance_orders,
    _check_candidate_tradability,
    _format_order_notification,
)
from scripts.ops.production_config import load_production_config
from scripts.research_trusted_strategy_account_backtest import (
    ASHARE_ADAPTIVE_VERSION,
    ASHARE_WEEKLY_UNCONFIRMED_WEIGHT,
    _ashare_weight_cache_key,
    _build_dual_system_targets,
    _resolve_ashare_weight_config,
    _symbol_from_ts_code,
)
from scripts.research_full_pool_liquidity_strategies import build_strategy_specs


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


def test_adaptive_risk_profile_defaults_to_governed_main_push():
    args = argparse.Namespace(risk_profile="adaptive", strategy=None, hold_days=None, position_ratio=None)

    resolved = _apply_risk_profile_defaults(args)

    assert resolved.strategy == "production_governed_vol_position"
    assert resolved.hold_days == 10
    assert resolved.position_ratio == 0.7


def test_rebalance_orders_effective_weight_uses_total_account_equity():
    candidates = pd.DataFrame(
        [
            {
                "signal_date": "2026-06-18",
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.7,
                "latest_close": 10.0,
                "strategy": "demo",
            }
        ]
    )

    orders = _build_rebalance_orders(
        candidates=candidates,
        positions={},
        latest_price_lookup={},
        total_equity=1_000_000.0,
        lot_size=100,
        min_trade_value=100.0,
        include_sells=True,
    )

    assert len(orders) == 1
    assert orders.iloc[0]["side"] == "BUY"
    assert orders.iloc[0]["allocated_shares"] == 70000
    assert orders.iloc[0]["allocated_shares"] * orders.iloc[0]["price"] == 700000.0


def test_canary_orders_use_independent_small_capital_base():
    candidates = pd.DataFrame(
        [
            {
                "signal_date": "2026-07-07",
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.25,
                "latest_close": 10.0,
                "strategy": "production_governed_vol_position",
            },
            {
                "signal_date": "2026-07-07",
                "symbol": "000002",
                "name": "B",
                "effective_weight": 0.25,
                "latest_close": 20.0,
                "strategy": "production_governed_vol_position",
            },
        ]
    )

    orders = _build_canary_orders(
        candidates,
        total_equity=100_000.0,
        lot_size=100,
        min_trade_value=100.0,
        allow_new_buys=True,
        min_holding_days=10,
        max_total_positions=5,
    )

    assert not orders.empty
    assert set(orders["side"]) == {"BUY"}
    assert (orders["allocated_shares"] * orders["price"]).sum() <= 50_000.0
    assert orders.attrs["max_total_positions"] == 5


def test_order_notification_shows_governor_version_and_canary_preview():
    candidates = pd.DataFrame(
        [
            {
                "rank": 1,
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.5,
                "rank_score": 90,
            }
        ]
    )
    candidates.attrs["risk_governor"] = {
        "risk_decision": "reduce_position",
        "risk_governor_version": "v1",
        "target_position_ratio": 0.5,
        "allow_new_buys": True,
        "reasons": ["shadow_validation_reduce"],
    }
    orders = pd.DataFrame(
        [
            {
                "side": "BUY",
                "ts_code": "000001",
                "stock_name": "A",
                "delta_shares": 1000,
                "allocated_shares": 1000,
                "price": 10.0,
            }
        ]
    )
    orders.attrs["hold_gate_min_days"] = 10
    orders.attrs["hold_gate_locked_symbols"] = []
    orders.attrs["max_total_positions"] = 5
    orders.attrs["position_cap_skipped_symbols"] = []
    canary_orders = orders.copy()

    text = _format_order_notification(
        asof_date="2026-07-07",
        strategy="production_governed_vol_position",
        candidates=candidates,
        orders=orders,
        files={"markdown": "/tmp/candidates.md"},
        total_equity_used=500_000.0,
        canary_orders=canary_orders,
        canary_total_equity=100_000.0,
        canary_orders_path="/tmp/canary.csv",
    )

    assert "风险总闸：版本=v1；reduce_position" in text
    assert "Canary人工试运行：资金基数=100,000.00" in text
    assert "仅人工确认，不写入正式订单表" in text


def test_rebalance_orders_freeze_buy_keeps_sells_only():
    candidates = pd.DataFrame(
        [
            {
                "signal_date": "2026-06-18",
                "symbol": "000001",
                "name": "A",
                "effective_weight": 0.5,
                "latest_close": 10.0,
                "strategy": "demo",
            }
        ]
    )

    orders = _build_rebalance_orders(
        candidates=candidates,
        positions={"000002": {"shares": 1000, "holding_trade_days": 30}},
        latest_price_lookup={"000002": 10.0},
        total_equity=100_000.0,
        lot_size=100,
        min_trade_value=100.0,
        include_sells=True,
        allow_new_buys=False,
    )

    assert not orders.empty
    assert set(orders["side"]) == {"SELL"}
    assert orders.iloc[0]["ts_code"] == "000002"


def test_production_default_strategy_is_not_model_risk():
    config = load_production_config()
    specs = {spec.name: spec for spec in build_strategy_specs()}
    spec = specs[config["primary_selection_strategy"]]

    assert config["allow_model_risk_fields"] is False
    assert spec.pit_status == "trusted"


def test_candidate_tradability_allows_label_schema_without_suspended_column(monkeypatch):
    captured_sql = []

    class FakeResult:
        def mappings(self):
            return self

        def fetchall(self):
            return [{"symbol": "000001", "issue": None}]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            captured_sql.append(str(sql))
            return FakeResult()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(
        export_candidates_mod,
        "_columns_for_table",
        lambda engine, table: {"ts_code", "trade_date", "is_st"},
    )

    result = _check_candidate_tradability(
        FakeEngine(),
        pd.DataFrame([{"symbol": "000001"}]),
        "2026-06-23",
    )

    assert result == []
    assert captured_sql
    assert "is_suspended" not in captured_sql[0]
    assert "is_st" in captured_sql[0]


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


def test_dual_system_targets_downweight_weekly_unconfirmed_without_dropping():
    day_scores = pd.DataFrame(
        [
            {"trade_date": "2026-06-03", "symbol": "000001", "score": 80, "index_bucket": "index_neutral", "market_amount_ratio_20": 1.0},
            {"trade_date": "2026-06-03", "symbol": "000002", "score": 70, "index_bucket": "index_neutral", "market_amount_ratio_20": 1.0},
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
                "source_strategy": "AUTO",
                "source_rank": 1,
                "source_score": 90,
                "risk_veto_flag": 0,
                "weekly_confirm_pass": 0,
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

    row = targets[targets["symbol"].eq("000002")].iloc[0]
    assert row["ashare_hit"] == 1
    assert row["ashare_weight_penalty"] == ASHARE_WEEKLY_UNCONFIRMED_WEIGHT
    assert meta["ashare_weekly_penalty_count"] == 1


def test_dual_system_targets_cap_ashare_supplements_when_chenyiyun_is_concentrated():
    day_scores = pd.DataFrame(
        [
            {"trade_date": "2026-06-03", "symbol": f"00000{i}", "score": 80 - i, "index_bucket": "index_neutral", "market_amount_ratio_20": 1.0}
            for i in range(1, 9)
        ]
    )
    chenyiyun = pd.DataFrame(
        [
            {"symbol": f"00000{i}", "rank": i, "rank_score": 80 - i, "effective_weight": 0.2, "name": f"C{i}", "industry": "I1"}
            for i in range(1, 6)
        ]
    )
    ashare = pd.DataFrame(
        [
            {
                "symbol": f"00000{i}",
                "strategy_version": "AUTO",
                "source_strategy": "AUTO",
                "source_rank": i,
                "source_score": 100 - i,
                "risk_veto_flag": 0,
                "weekly_confirm_pass": 1,
                "stock_name": f"A{i}",
            }
            for i in range(6, 9)
        ]
    )

    targets, meta = _build_dual_system_targets(
        signal_date="2026-06-03",
        day_scores=day_scores,
        chenyiyun_targets=chenyiyun,
        ashare_day=ashare,
        top_n=5,
        strategy_name="dual_system_adaptive_route",
    )

    assert int(targets["ashare_supplement"].fillna(0).sum()) <= 2
    assert meta["ashare_supplement_count"] <= 2


def test_ashare_weight_profile_rejects_unknown_profile():
    try:
        _resolve_ashare_weight_config(profile="not_a_profile")
    except ValueError as exc:
        assert "Unknown AShare weight profile" in str(exc)
    else:
        raise AssertionError("unknown AShare weight profile should fail")


def test_ashare_research_stage2_allows_three_supplements_and_records_v22_fields():
    day_scores = pd.DataFrame(
        [
            {"trade_date": "2026-06-03", "symbol": f"00000{i}", "score": 80 - i, "index_bucket": "index_neutral", "market_amount_ratio_20": 1.0}
            for i in range(1, 10)
        ]
    )
    chenyiyun = pd.DataFrame(
        [
            {"symbol": f"00000{i}", "rank": i, "rank_score": 80 - i, "effective_weight": 0.2, "name": f"C{i}", "industry": "I1"}
            for i in range(1, 6)
        ]
    )
    ashare = pd.DataFrame(
        [
            {
                "symbol": f"00000{i}",
                "strategy_version": "AUTO",
                "source_strategy": "AUTO",
                "source_rank": i,
                "source_score": 100 - i,
                "risk_veto_flag": 0,
                "weekly_confirm_pass": 1,
                "stock_name": f"A{i}",
            }
            for i in range(6, 10)
        ]
    )

    targets, meta = _build_dual_system_targets(
        signal_date="2026-06-03",
        day_scores=day_scores,
        chenyiyun_targets=chenyiyun,
        ashare_day=ashare,
        top_n=5,
        strategy_name="dual_system_adaptive_route",
        weight_profile="research_stage2",
    )

    assert int(targets["ashare_supplement"].fillna(0).sum()) <= 3
    assert meta["ashare_supplement_limit"] == 3
    assert meta["adaptive_version"] == ASHARE_ADAPTIVE_VERSION
    assert meta["ashare_weight_profile"] == "research_stage2"
    assert "v2.2" in meta["ashare_weight_cache_key"]


def test_ashare_weight_cache_key_includes_release_tier_and_limit():
    config = _resolve_ashare_weight_config(
        profile="prod_stage1",
        release_tier="custom_tier",
        supplement_limit=1,
    )
    key = _ashare_weight_cache_key(config, "2026-06-03", "baseline_full_liquidity", 5)

    assert "custom_tier" in key
    assert "limit1" in key
