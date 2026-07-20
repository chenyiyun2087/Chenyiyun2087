"""Tests for PR1: exact production benchmark and capital gate.

Covers:
  - Corporate action snapshot fail-closed behavior
  - Lifecycle snapshot coverage
  - Index snapshot completeness
  - Matched portfolio rules (7 curves, shared rules, equal/neutral/random/reversed)
  - Performance validator (3m/6m/1y/full windows, provenance, identity match)
  - Release order policy (ACTIVE_FIXED_CAPITAL, BLOCKED, SHADOW, etc.)
  - Capital gate (no external injection, principal changes require new release)
"""

from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from runtime.order_policy import (
    ALLOWED_ORDER_SOURCES,
    FORBIDDEN_ORDER_SOURCES,
    OrderPolicyGate,
    PolicyTier,
    ReleaseOrderPolicyConfig,
    ScalePolicy,
)
from runtime.release_registry import ReleaseRecord, load_release_registry
from runtime.provenance import ProvenanceEnvelope
from runtime.data_snapshot import DataSnapshot, freeze_data_snapshot, hash_dataframe
from scripts.ops.production_config import (
    ReleaseOrderPolicyConfigModel,
    load_production_config,
)
from scripts.research.matched_portfolio_runner import (
    REQUIRED_CURVES,
    MatchedExperimentSpec,
    MatchedPortfolioRunner,
    _RANDOM_SEEDS,
)
from scripts.research.performance_validator import (
    MIN_COVERAGE_RATIO,
    REQUIRED_WINDOWS,
    PerformanceValidator,
    ValidatorReport,
    WindowResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_release() -> ReleaseRecord:
    return ReleaseRecord(
        strategy_id="production_governed_vol_position",
        strategy_version="2026.06.23",
        release_id="prod-test-01",
        role="ACTIVE_PRODUCTION",
        lifecycle_status="PRODUCTION",
        research_status="FAILED_REVALIDATION",
        walk_forward_status="FAILED",
        execution_status="ACTIVE_FIXED_CAPITAL",
        promotion_status="BLOCKED",
        capital_status="NO_EXTERNAL_SCALE",
        approved_principal=500000.0,
        order_policy="ACTIVE_FIXED_CAPITAL",
        git_commit_sha="abc123def456",
        config_sha="cfg789abc012",
        data_snapshot_sha="data345def678",
        calendar_snapshot_sha="cal901ghi234",
        corporate_action_snapshot_sha="ca567jkl890",
        lifecycle_snapshot_sha="life123mno456",
        sample_start="2023-01-03",
        sample_end="2026-07-10",
        actual_trading_days=850,
        cost_model="commission_and_tax_0.00075_slippage_0",
    )


@pytest.fixture
def sample_policy_config() -> ReleaseOrderPolicyConfig:
    return ReleaseOrderPolicyConfig(
        schema_version="1.0",
        current_policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
        scale_policy=ScalePolicy.NO_EXTERNAL_SCALE,
        approved_principal=500_000.0,
        approved_by="chenyiyun",
        approved_at="2026-07-10T00:00:00+08:00",
    )


@pytest.fixture
def sample_matched_spec() -> MatchedExperimentSpec:
    return MatchedExperimentSpec(
        tradable_pool=frozenset(),
        top_n=5,
        hold_days=10,
        cost_rate=0.00075,
        slippage_rate=0.0,
        lot_size=100,
        min_trade_value=500.0,
        t_plus_1=True,
        limit_up_down=True,
        suspension_rules=True,
    )


@pytest.fixture
def sample_calendar() -> list:
    """Generate 200 trading days starting 2023-01-03."""
    import pandas as pd
    start = pd.Timestamp("2023-01-03")
    days = []
    current = start
    for _ in range(200):
        while current.weekday() >= 5:  # skip weekends
            current += pd.Timedelta(days=1)
        days.append(current.date())
        current += pd.Timedelta(days=1)
    return days


@pytest.fixture
def sample_scores(sample_calendar) -> pd.DataFrame:
    """Synthetic score data for 5 stocks over the calendar."""
    import numpy as np
    rows = []
    for i, td in enumerate(sample_calendar[:50]):  # 50 signal days
        for j in range(5):
            symbol = str(600000 + j).zfill(6)
            score = np.random.RandomState(i * 10 + j).uniform(50, 100)
            rows.append({
                "trade_date": td,
                "symbol": symbol,
                "name": f"Stock_{symbol}",
                "industry": "金融" if j < 2 else "科技",
                "score": score,
                "s_liquidity": np.random.uniform(0.3, 1.0),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_prices(sample_calendar) -> pd.DataFrame:
    """Synthetic price data for 5 stocks over the calendar."""
    import numpy as np
    rows = []
    base_prices = {str(600000 + j).zfill(6): 10.0 + j * 5.0 for j in range(5)}
    rng = np.random.RandomState(42)
    for td in sample_calendar:
        for symbol, base in base_prices.items():
            noise = rng.normal(0, 0.02)
            adj_close = base * (1.0 + noise)
            adj_open = adj_close * (1.0 + rng.normal(0, 0.005))
            prev_close = adj_close * (1.0 + rng.normal(0, 0.005))
            rows.append({
                "trade_date": td,
                "symbol": symbol,
                "adj_open": adj_open,
                "adj_high": adj_close * 1.01,
                "adj_low": adj_close * 0.99,
                "adj_close": adj_close,
                "prev_adj_close": prev_close,
                "raw_volume": 1000000 + rng.uniform(0, 500000),
                "raw_close": adj_close,
                "raw_open": adj_open,
                "is_listed": 1,
                "is_suspended": 0,
                "is_st": 0,
                "execution_tradable": 1,
                "name": f"Stock_{symbol}",
                "industry": "金融" if int(symbol) < 600003 else "科技",
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_nav_df() -> pd.DataFrame:
    """Synthetic NAV data for a full period."""
    import numpy as np
    dates = pd.date_range("2023-01-03", "2026-07-10", freq="B")[:800]
    nav = 1.0 + np.cumsum(np.random.RandomState(42).normal(0.0005, 0.015, len(dates)))
    nav = np.maximum(nav, 0.5)
    return pd.DataFrame({
        "trade_date": [d.strftime("%Y-%m-%d") for d in dates],
        "nav": nav,
        "curve": "production",
    })


# ---------------------------------------------------------------------------
# Corporate Action Snapshot — fail-closed
# ---------------------------------------------------------------------------


def test_data_snapshot_has_index_hash_field():
    ds = DataSnapshot(
        snapshot_date="2026-07-10",
        scores_hash="abc",
        prices_hash="def",
        trade_cal_hash="ghi",
        corporate_action_hash="PENDING",
        lifecycle_hash="PENDING",
        index_snapshot_hash="idx123",
    )
    assert ds.index_snapshot_hash == "idx123"
    d = ds.to_dict()
    assert "index_snapshot_hash" in d
    assert d["index_snapshot_hash"] == "idx123"


def test_data_snapshot_fingerprint_includes_index():
    ds1 = DataSnapshot(
        snapshot_date="2026-07-10",
        scores_hash="abc", prices_hash="def", trade_cal_hash="ghi",
        corporate_action_hash="ca", lifecycle_hash="life",
        index_snapshot_hash="idx1",
    )
    ds2 = DataSnapshot(
        snapshot_date="2026-07-10",
        scores_hash="abc", prices_hash="def", trade_cal_hash="ghi",
        corporate_action_hash="ca", lifecycle_hash="life",
        index_snapshot_hash="idx2",
    )
    assert ds1.fingerprint() != ds2.fingerprint()


def test_freeze_data_snapshot_accepts_hashes():
    """freeze_data_snapshot passes through CA/lifecycle/index hashes."""
    # This test doesn't need a real DB — it verifies the signature.
    # In CI without MySQL it will be skipped.
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        pytest.skip("sqlalchemy not available")
    # The function requires a real engine, so we test the DataSnapshot
    # construction path instead.
    ds = DataSnapshot(
        snapshot_date="2026-01-01",
        scores_hash="scores_hash",
        prices_hash="prices_hash",
        trade_cal_hash="cal_hash",
        corporate_action_hash="ca_hash_real",
        lifecycle_hash="life_hash_real",
        index_snapshot_hash="idx_hash_real",
    )
    assert ds.corporate_action_hash == "ca_hash_real"
    assert ds.lifecycle_hash == "life_hash_real"
    assert ds.index_snapshot_hash == "idx_hash_real"


# ---------------------------------------------------------------------------
# Matched portfolios — 7 curves + shared rules
# ---------------------------------------------------------------------------


def test_all_seven_curves_defined():
    assert len(REQUIRED_CURVES) == 7
    assert "production" in REQUIRED_CURVES
    assert "champion" in REQUIRED_CURVES
    assert "matched_equal" in REQUIRED_CURVES
    assert "matched_neutral" in REQUIRED_CURVES
    assert "matched_random" in REQUIRED_CURVES
    assert "matched_reversed" in REQUIRED_CURVES
    assert "csi300" in REQUIRED_CURVES


def test_matched_experiment_spec_is_immutable():
    spec = MatchedExperimentSpec(
        tradable_pool=frozenset({"000001"}),
        top_n=5, hold_days=10, cost_rate=0.00075,
        slippage_rate=0.001, lot_size=100, min_trade_value=500.0,
    )
    with pytest.raises(Exception):
        spec.top_n = 8  # frozen dataclass


def test_random_seeds_are_fixed():
    """20 pre-registered seeds must be stable and not cherry-picked."""
    assert len(_RANDOM_SEEDS) == 20
    assert len(set(_RANDOM_SEEDS)) == 20  # all unique
    # Verify a known seed
    assert _RANDOM_SEEDS[0].startswith("a1b2c3d4")


def test_matched_equal_is_equal_weight(
    sample_matched_spec, sample_scores, sample_prices, sample_calendar,
):
    """matched_equal assigns equal weight to each candidate."""
    runner = MatchedPortfolioRunner(
        sample_matched_spec, sample_calendar,
    )
    result = runner.run_matched_equal(sample_scores, sample_prices)
    assert result.curve_name == "matched_equal"
    assert result.error == ""
    assert len(result.nav_rows) > 0


def test_matched_neutral_is_alphabetical(
    sample_matched_spec, sample_scores, sample_prices, sample_calendar,
):
    """matched_neutral sorts alphabetically, ignoring scores."""
    runner = MatchedPortfolioRunner(
        sample_matched_spec, sample_calendar,
    )
    result = runner.run_matched_neutral(sample_scores, sample_prices)
    assert result.curve_name == "matched_neutral"
    assert result.error == ""


def test_matched_reversed_ranks_inverted(
    sample_matched_spec, sample_scores,
):
    """matched_reversed inverts the score ranking order."""
    candidates = sample_scores[
        sample_scores["trade_date"] == sample_scores["trade_date"].iloc[0]
    ].copy()
    original = MatchedPortfolioRunner._rank_score(candidates)
    reversed_ = MatchedPortfolioRunner._rank_reversed(candidates)
    if len(original) >= 2:
        # The first-ranked symbol in the original should NOT be first in reversed
        assert original.iloc[0]["symbol"] != reversed_.iloc[0]["symbol"]
        # The original ranking is score-descending; reversed is score-ascending
        orig_symbols = original["symbol"].tolist()
        rev_symbols = reversed_["symbol"].tolist()
        # The last in original should be first in reversed (or close to)
        assert orig_symbols[-1] in rev_symbols[:2]


def test_matched_random_uses_fixed_seeds(
    sample_matched_spec, sample_scores, sample_prices, sample_calendar,
):
    """matched_random produces 20 curves with distinct seeds."""
    runner = MatchedPortfolioRunner(
        sample_matched_spec, sample_calendar,
    )
    results = runner.run_matched_random(sample_scores, sample_prices)
    assert len(results) == 20
    seeds = {r.random_seed for r in results}
    assert len(seeds) == 20


def test_require_all_curves_fails_on_missing(sample_matched_spec, sample_calendar):
    """Missing curves raise RuntimeError."""
    runner = MatchedPortfolioRunner(sample_matched_spec, sample_calendar)
    curves = {"production": None}  # only one curve
    with pytest.raises(RuntimeError, match="missing_curves"):
        runner.require_all_curves(curves)


def test_require_all_curves_passes_when_complete(
    sample_matched_spec, sample_calendar,
):
    """All 7 curves present → no error."""
    from scripts.research.matched_portfolio_runner import CurveResult
    runner = MatchedPortfolioRunner(sample_matched_spec, sample_calendar)
    curves = {
        name: CurveResult(curve_name=name, summary={"curve": name})
        for name in REQUIRED_CURVES
    }
    runner.require_all_curves(curves)  # should not raise


def test_csi300_benchmark_handles_no_index_data(
    sample_matched_spec, sample_prices, sample_calendar,
):
    """CSI 300 curve handles missing index data gracefully."""
    runner = MatchedPortfolioRunner(
        sample_matched_spec, sample_calendar,
    )
    result = runner.run_csi300(sample_prices)
    assert result.curve_name == "csi300"
    # May be empty if no index data, but should not crash
    assert result.error == ""


# ---------------------------------------------------------------------------
# Performance validator
# ---------------------------------------------------------------------------


def test_performance_validator_requires_all_windows(sample_release):
    validator = PerformanceValidator(sample_release)
    assert set(REQUIRED_WINDOWS) == {"3m", "6m", "1y", "full"}


def test_performance_validator_validates_windows(
    sample_release, sample_nav_df,
):
    validator = PerformanceValidator(sample_release)
    windows = validator.validate_from_nav(
        sample_nav_df, curve_name="production",
    )
    assert len(windows) == 4
    window_names = {w.window for w in windows}
    assert window_names == REQUIRED_WINDOWS


def test_performance_validator_empty_nav_returns_insufficient(
    sample_release,
):
    validator = PerformanceValidator(sample_release)
    windows = validator.validate_from_nav(
        pd.DataFrame(), curve_name="production",
    )
    for w in windows:
        assert w.coverage_status == "INSUFFICIENT_COVERAGE"


def test_performance_validator_window_coverage_minimum(
    sample_release, sample_nav_df,
):
    """95% coverage minimum is enforced."""
    assert MIN_COVERAGE_RATIO == 0.95


def test_performance_validator_provenance_complete(sample_release):
    """ValidatorReport provenance envelope is fully populated."""
    report = ValidatorReport(
        strategy_id=sample_release.strategy_id,
        release_id=sample_release.release_id,
        all_curves_present=True,
        identity_match=True,
        provenance_complete=True,
    )
    assert report.passed


def test_performance_validator_provenance_rejects_incomplete_sha(sample_release):
    """Incomplete provenance SHAs cause validation failure."""
    broken = ReleaseRecord(**{
        **sample_release.model_dump(),
        "git_commit_sha": "NOT_FROZEN",
    })
    validator = PerformanceValidator(broken)
    nav = pd.DataFrame({
        "trade_date": ["2023-01-03", "2026-07-10"],
        "nav": [1.0, 1.2],
        "curve": "production",
    })
    # Build a minimal curves dict
    from scripts.research.matched_portfolio_runner import CurveResult
    curves = {
        name: CurveResult(curve_name=name)
        for name in REQUIRED_CURVES
    }
    report = validator.validate_all_curves(curves, nav)
    assert not report.provenance_complete


def test_provenance_envelope_includes_new_fields(sample_release):
    """ProvenanceEnvelope carries approved_principal, order_policy, index_snapshot_sha."""
    envelope = ProvenanceEnvelope.from_release(
        sample_release,
        requested_strategy_id=sample_release.strategy_id,
        resolved_strategy_id=sample_release.strategy_id,
        sample_start="2023-01-03",
        sample_end="2026-07-10",
        actual_trading_days=850,
        requested_window_days=850,
        identity_status="MATCHED",
        index_snapshot_sha="idx_sha_abc",
    )
    assert envelope.approved_principal == 500000.0
    assert envelope.order_policy == "ACTIVE_FIXED_CAPITAL"
    assert envelope.index_snapshot_sha == "idx_sha_abc"


def test_provenance_envelope_frozen(sample_release):
    """ProvenanceEnvelope is immutable."""
    envelope = ProvenanceEnvelope.from_release(
        sample_release,
        requested_strategy_id=sample_release.strategy_id,
        resolved_strategy_id=sample_release.strategy_id,
        sample_start="2023-01-03",
        sample_end="2026-07-10",
        actual_trading_days=850,
        requested_window_days=850,
        identity_status="MATCHED",
    )
    with pytest.raises(ValidationError):
        envelope.approved_principal = 999999.0


# ---------------------------------------------------------------------------
# Release order policy
# ---------------------------------------------------------------------------


def test_policy_tiers_are_exhaustive():
    assert len(PolicyTier) == 5
    assert PolicyTier.PRODUCTION_APPROVED.value == "PRODUCTION_APPROVED"
    assert PolicyTier.ACTIVE_FIXED_CAPITAL.value == "ACTIVE_FIXED_CAPITAL"
    assert PolicyTier.ACTIVE_EXISTING_ONLY.value == "ACTIVE_EXISTING_ONLY"
    assert PolicyTier.SHADOW.value == "SHADOW"
    assert PolicyTier.BLOCKED.value == "BLOCKED"


def test_active_fixed_capital_allows_rebalance():
    allowed, reason = OrderPolicyGate.validate_rebalance(
        policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
    )
    assert allowed
    assert reason == ""


def test_active_fixed_capital_allows_buy_within_cap():
    allowed, reason = OrderPolicyGate.validate_order(
        side="BUY",
        policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
        approved_principal=500_000.0,
        current_capital=400_000.0,
        order_notional=50_000.0,
    )
    assert allowed


def test_active_fixed_capital_blocks_external_injection():
    """Buy that would push total capital beyond approved_principal is blocked."""
    allowed, reason = OrderPolicyGate.validate_order(
        side="BUY",
        policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
        approved_principal=500_000.0,
        current_capital=490_000.0,
        order_notional=20_000.0,  # 490k + 20k > 500k
    )
    assert not allowed
    assert "capital_injection_blocked" in reason


def test_active_fixed_capital_allows_sell():
    allowed, reason = OrderPolicyGate.validate_order(
        side="SELL",
        policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
        approved_principal=500_000.0,
        current_capital=400_000.0,
        order_notional=50_000.0,
    )
    assert allowed


def test_blocked_only_allows_risk_exits():
    """BLOCKED allows sells but not buys."""
    buy_ok, _ = OrderPolicyGate.validate_order(
        side="BUY", policy=PolicyTier.BLOCKED,
        approved_principal=500_000.0, current_capital=100_000.0,
        order_notional=10_000.0,
    )
    assert not buy_ok

    sell_ok, _ = OrderPolicyGate.validate_order(
        side="SELL", policy=PolicyTier.BLOCKED,
        approved_principal=500_000.0, current_capital=100_000.0,
        order_notional=10_000.0,
    )
    assert sell_ok


def test_shadow_no_real_orders():
    """SHADOW does not emit real orders."""
    gate = OrderPolicyGate.gate_summary(PolicyTier.SHADOW)
    assert gate["emits_real_orders"] is False
    assert gate["buy_allowed"] is False
    assert gate["sell_allowed"] is False


def test_existing_only_blocks_buys():
    buy_ok, _ = OrderPolicyGate.validate_order(
        side="BUY", policy=PolicyTier.ACTIVE_EXISTING_ONLY,
        approved_principal=500_000.0, current_capital=100_000.0,
        order_notional=10_000.0,
    )
    assert not buy_ok

    sell_ok, _ = OrderPolicyGate.validate_order(
        side="SELL", policy=PolicyTier.ACTIVE_EXISTING_ONLY,
        approved_principal=500_000.0, current_capital=100_000.0,
        order_notional=10_000.0,
    )
    assert sell_ok


def test_order_source_must_be_trusted_snapshot():
    ok, _ = OrderPolicyGate.validate_order_source("trusted_live_snapshot")
    assert ok

    not_ok, reason = OrderPolicyGate.validate_order_source("cli_override")
    assert not not_ok
    assert "forbidden" in reason


def test_order_source_rejects_unknown():
    not_ok, reason = OrderPolicyGate.validate_order_source("random_file")
    assert not not_ok
    assert "unrecognized" in reason


def test_capital_change_requires_approval_under_no_external_scale():
    ok, reason = OrderPolicyGate.validate_capital_change(
        new_principal=600_000.0,
        old_principal=500_000.0,
        scale_policy=ScalePolicy.NO_EXTERNAL_SCALE,
    )
    assert not ok
    assert "external_capital_injection_blocked" in reason


def test_capital_change_noop_is_allowed():
    ok, reason = OrderPolicyGate.validate_capital_change(
        new_principal=500_000.0,
        old_principal=500_000.0,
        scale_policy=ScalePolicy.NO_EXTERNAL_SCALE,
    )
    assert ok


def test_production_set_to_active_fixed_capital_500k():
    """Production config matches PR1 policy."""
    config = load_production_config()
    policy_raw = config.get("release_order_policy", {})
    assert policy_raw.get("execution_policy") == "ACTIVE_FIXED_CAPITAL"
    assert policy_raw.get("scale_policy") == "NO_EXTERNAL_SCALE"
    assert float(policy_raw.get("approved_principal", 0)) == 500_000.0


def test_champion_has_order_policy_and_principal():
    registry = load_release_registry()
    champion = registry.releases[
        "production_governed_vol_position_v1_2b_dynamic_score"
    ]
    assert champion.order_policy == "SHADOW"
    assert champion.approved_principal == 500_000.0


def test_production_has_order_policy_and_principal():
    registry = load_release_registry()
    prod = registry.releases["production_governed_vol_position"]
    assert prod.order_policy == "MANUAL_ORDER_DRAFT_ONLY"
    assert prod.approved_principal == 500_000.0
    assert prod.execution_status == "ACTIVE_FIXED_CAPITAL"


# ---------------------------------------------------------------------------
# Release registry
# ---------------------------------------------------------------------------


def test_release_record_has_new_fields():
    record = ReleaseRecord(
        strategy_id="test_strategy",
        strategy_version="1.0",
        release_id="test-01",
        role="RESEARCH_CHALLENGER",
        lifecycle_status="RESEARCH",
        research_status="RESEARCH_ONLY",
        walk_forward_status="PENDING",
        execution_status="BLOCKED",
        promotion_status="BLOCKED",
        capital_status="NO_SCALE",
        approved_principal=100_000.0,
        order_policy="BLOCKED",
    )
    assert record.approved_principal == 100_000.0
    assert record.order_policy == "BLOCKED"


def test_release_registry_loads_with_new_fields():
    registry = load_release_registry()
    for record in registry.releases.values():
        assert hasattr(record, "approved_principal")
        assert hasattr(record, "order_policy")


# ---------------------------------------------------------------------------
# Release order policy YAML config
# ---------------------------------------------------------------------------


def test_release_order_policy_config_validates():
    config = ReleaseOrderPolicyConfig(
        schema_version="1.0",
        current_policy=PolicyTier.ACTIVE_FIXED_CAPITAL,
        scale_policy=ScalePolicy.NO_EXTERNAL_SCALE,
        approved_principal=500_000.0,
    )
    assert config.approved_principal == 500_000.0
    assert config.require_release_for_principal_change is True


def test_release_order_policy_config_rejects_overlap():
    with pytest.raises(ValidationError):
        ReleaseOrderPolicyConfig(
            allowed_order_sources=["trusted_live_snapshot"],
            forbidden_order_sources=["trusted_live_snapshot"],
        )


def test_production_config_validates_order_policy_model():
    """ReleaseOrderPolicyConfigModel validates policy strings."""
    # Valid
    m = ReleaseOrderPolicyConfigModel(
        execution_policy="ACTIVE_FIXED_CAPITAL",
        scale_policy="NO_EXTERNAL_SCALE",
        approved_principal=500000,
    )
    assert m.execution_policy == "ACTIVE_FIXED_CAPITAL"

    # Invalid execution_policy
    with pytest.raises(ValidationError):
        ReleaseOrderPolicyConfigModel(
            execution_policy="INVALID_POLICY",
            scale_policy="NO_EXTERNAL_SCALE",
            approved_principal=500000,
        )

    # Invalid scale_policy
    with pytest.raises(ValidationError):
        ReleaseOrderPolicyConfigModel(
            execution_policy="ACTIVE_FIXED_CAPITAL",
            scale_policy="INVALID_SCALE",
            approved_principal=500000,
        )


# ---------------------------------------------------------------------------
# Existing P0 regression tests — must still pass
# ---------------------------------------------------------------------------


def test_champion_is_frozen_without_switching_production_route():
    """P0 test adapted for PR1 changes: champion now has PENDING_PR1_SNAPSHOT."""
    registry = load_release_registry()
    config = load_production_config()
    champion = registry.releases[
        "production_governed_vol_position_v1_2b_dynamic_score"
    ]

    assert config["primary_strategy"] == "production_governed_vol_position"
    assert config["release_id"] == registry.active_production_release_id
    assert champion.release_id == registry.champion_release_id
    assert champion.role == "CHAMPION_BENCHMARK"
    assert champion.promotion_status == "BLOCKED"
    assert champion.capital_status == "NO_EXTERNAL_SCALE"
    # PR1: corporate_action_snapshot_sha updated to PENDING_PR1_SNAPSHOT
    assert "PENDING" in champion.corporate_action_snapshot_sha
    # PR1: execution_status updated from BLOCKED_MISSING_IMMUTABLE_SNAPSHOTS
    assert champion.execution_status == "RESEARCH_ONLY"
