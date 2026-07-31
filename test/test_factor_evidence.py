from pathlib import Path

from scripts.research.factor_evidence import (
    DEFAULT_BENCHMARK,
    DEFAULT_NAV,
    DEFAULT_POSITIONS,
    DEFAULT_SOURCE,
    build_partial_factor_evidence,
)


def test_partial_factor_builder_is_diagnostic_and_fail_closed(tmp_path: Path):
    result = build_partial_factor_evidence(
        DEFAULT_SOURCE,
        DEFAULT_POSITIONS,
        DEFAULT_NAV,
        DEFAULT_BENCHMARK,
        tmp_path,
    )
    assert result["status"] == "PARTIAL"
    assert result["evidence_level"] == "E2"
    assert result["capital_authority"] is False
    assert result["automatic_promotion_allowed"] is False
    assert result["source_unique_dates"] == 138
    assert result["unique_dates"] < 252
    assert result["forward_return_coverage"] == {
        "5": 1.0,
        "10": 1.0,
        "20": 1.0,
        "60": 1.0,
    }
    assert result["factor_coverage"]["industry"] == 0.0
    assert "formal_pit_manifest_missing" in result["blockers"]
    diagnostic = result["partial_attribution"]
    assert diagnostic["formal_evidence"] is False
    assert diagnostic["status"] == "BLOCKED"
    assert diagnostic["aligned_trading_days"] < 252
    assert diagnostic["alpha_tstat"] < 2.0
    assert diagnostic["unexplained_variance_ratio"] > 0.05
