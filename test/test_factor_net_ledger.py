import json
from pathlib import Path

from scripts.research.factor_evidence import (
    DEFAULT_BENCHMARK,
    DEFAULT_NAV,
    DEFAULT_POSITIONS,
    DEFAULT_SOURCE,
    build_partial_factor_evidence,
)
from scripts.research.factor_net_ledger import build_factor_net_ledger
from scripts.research.pit_factor_panel_audit import audit_pit_factor_sources


def test_pit_factor_source_audit_fails_closed_without_long_panel(
    tmp_path: Path,
):
    report = audit_pit_factor_sources(
        tmp_path / "pit", profile_name="alpha_v4_5"
    )
    assert report["status"] == "BLOCKED"
    assert report["automatic_fallback_to_short_panel"] is False
    assert report["capital_authority"] is False
    assert max(row["unique_dates"] for row in report["local_candidates"]) == 138
    assert "no_local_panel_has_252_trading_days" in report["blockers"]


def test_factor_net_ledger_is_t_plus_1_costed_and_blocked(
    tmp_path: Path,
):
    factor_dir = tmp_path / "factors"
    build_partial_factor_evidence(
        DEFAULT_SOURCE,
        DEFAULT_POSITIONS,
        DEFAULT_NAV,
        DEFAULT_BENCHMARK,
        factor_dir,
        profile_name="alpha_v4_5",
    )
    report = build_factor_net_ledger(
        factor_dir,
        DEFAULT_SOURCE,
        tmp_path / "ledger",
        profile_name="alpha_v4_5",
    )
    assert report["status"] == "BLOCKED"
    assert report["economic_alpha_status"] == "BLOCKED"
    assert report["capital_authority"] is False
    assert report["broker_permission"] is False
    assert report["short_leg_semantics"].startswith("SYNTHETIC_RESEARCH_ONLY")
    daily = report["scorecard"]["volatility"]["DAILY"]
    assert daily["all_t_plus_1"] is True
    assert daily["holding_periods"] == 77
    assert daily["data_completeness_status"] == "BLOCKED"
    assert daily["missing_open_prices"] > 0
    assert (
        "source_panel_not_verified_as_complete_daily_investable_universe"
        in report["blockers"]
    )
    base = daily["cost_scenarios"]["BASE_7P5_10"]
    assert base["net_long"]["total_return"] < daily["gross_long"]["total_return"]
    assert (
        base["net_synthetic_spread"]["total_return"]
        < daily["gross_synthetic_spread"]["total_return"]
    )
    assert (
        base["net_synthetic_spread_time_split"]["last_30pct_holdout"][
            "observations"
        ]
        > 0
    )
    overlap = report["factor_overlap_top_quantile_jaccard"]
    assert overlap["volatility"]["value"] < 0.5
    persisted = json.loads(
        (tmp_path / "ledger" / "factor_economic_scorecard.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["content_sha256"] == report["content_sha256"]
