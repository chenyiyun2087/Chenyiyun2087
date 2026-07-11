"""Tests for PR6: full strategy v3 promotion evaluation."""

import json
import pytest

from scripts.research.promotion_evaluation import (
    PromotionEvidence,
    PromotionDecision,
    PromotionEvaluator,
    PromotionReporter,
)

from scripts.research.walk_forward_metrics import (
    PromotionGate,
    PromotionGateResult,
    WindowMetrics,
)


def _make_metrics(total_return=0.10, sharpe=0.5, max_dd=-0.10, calmar=0.8):
    return WindowMetrics(
        window_label="2025Q1", experiment_id="test",
        total_return=total_return, sharpe_ratio=sharpe,
        max_drawdown=max_dd, calmar_ratio=calmar,
    )


# ---------------------------------------------------------------------------
# Evidence Tests
# ---------------------------------------------------------------------------


class TestEvidence:
    def test_evidence_all_passed(self):
        e = PromotionEvidence(
            gate_name="test", condition="check", passed=True,
            value=3, threshold="≥2",
        )
        assert e.passed is True
        d = e.to_dict()
        assert d["gate_name"] == "test"

    def test_evidence_with_failure(self):
        e = PromotionEvidence(
            gate_name="test", condition="check", passed=False,
            value=1, threshold="≥2", detail="only 1 of 2",
        )
        assert e.passed is False
        assert e.detail == "only 1 of 2"

    def test_evidence_to_dict(self):
        e = PromotionEvidence(
            gate_name="g1", condition="c1", passed=True, value=5, threshold="≥3",
        )
        d = e.to_dict()
        for k in ["gate_name", "condition", "passed", "value", "threshold"]:
            assert k in d

    def test_decision_overall_score(self):
        d = PromotionDecision(
            strategy_id="test", recommend_promotion=True,
            overall_score=0.8, gates_passed=4, gates_total=5,
            conditions_passed=8, conditions_total=10,
            summary="test decision",
        )
        assert d.overall_score == 0.8
        assert d.recommend_promotion is True
        dd = d.to_dict()
        assert dd["strategy_id"] == "test"


# ---------------------------------------------------------------------------
# Gate Aggregation Tests
# ---------------------------------------------------------------------------


class TestGateAggregation:
    def test_all_gates_passed_promotion_recommended(self):
        evidence = [
            PromotionEvidence("comparison", "a0_gate", True, "3/3", "≥2/3"),
            PromotionEvidence("industry_neutral_alpha", "all_factors_bh", True, 6, "≥6"),
            PromotionEvidence("risk_portfolio", "return_improved", True, True, "true"),
            PromotionEvidence("decay_exit", "has_decay_exits", True, True, "true"),
            PromotionEvidence("promotion", "cumulative_return", True, "0.15", "A9>A0"),
        ]
        decision = PromotionDecision(
            strategy_id="full_strategy_v3", recommend_promotion=True,
            overall_score=1.0, gates_passed=5, gates_total=5,
            conditions_passed=5, conditions_total=5,
            evidence=evidence, summary="PROMOTION RECOMMENDED",
        )
        assert decision.recommend_promotion is True
        assert decision.overall_score == 1.0

    def test_one_gate_failed_no_promotion(self):
        evidence = [
            PromotionEvidence("comparison", "a0_gate", True, "3/3", "≥2/3"),
            PromotionEvidence("industry_neutral_alpha", "all_factors_bh", False, 3, "≥6"),
            PromotionEvidence("risk_portfolio", "return_improved", True, True, "true"),
        ]
        evaluator = PromotionEvaluator()
        # Build fake gate results with a failed INDUSTRY_NEUTRAL_ALPHA gate
        fake_ina = type("GR", (), {
            "all_factors_bh_pass": False, "all_factors_oos_pass": True,
            "all_factors_stability_ok": True, "factors_bh_pass_count": 3,
            "factors_oos_pass_count": 6, "passed": False,
        })()
        fake_cg = type("GR", (), {"passed": True, "windows_passed": 3, "windows_total": 3})()
        fake_rpg = type("GR", (), {
            "return_improved": True, "drawdown_improved": True,
            "volatility_reduced": True, "concentration_ok": True, "passed": True,
        })()
        fake_pg = type("GR", (), {
            "passed": True, "conditions": {
                "cumulative_return": True, "sharpe": True,
                "drawdown": True, "calmar": True, "decay_exits": True,
            },
        })()
        gate_results = {
            "comparison_gate": fake_cg,
            "industry_neutral_alpha_gate": fake_ina,
            "risk_portfolio_gate": fake_rpg,
            "promotion_gate": fake_pg,
        }
        evaluator.gate_results = gate_results
        decision = evaluator.evaluate()
        assert decision.recommend_promotion is False
        assert decision.overall_score < 1.0

    def test_a9_beats_a0_on_all_metrics(self):
        """PromotionGate should pass when A9 dominates A0."""
        a0 = {
            "2025Q1": _make_metrics(total_return=0.10, sharpe=0.5, max_dd=-0.15, calmar=0.6),
            "2025Q3": _make_metrics(total_return=0.08, sharpe=0.4, max_dd=-0.12, calmar=0.5),
            "2026Q1": _make_metrics(total_return=0.12, sharpe=0.6, max_dd=-0.18, calmar=0.7),
        }
        a9 = {
            "2025Q1": _make_metrics(total_return=0.15, sharpe=0.8, max_dd=-0.08, calmar=1.2),
            "2025Q3": _make_metrics(total_return=0.12, sharpe=0.7, max_dd=-0.06, calmar=1.0),
            "2026Q1": _make_metrics(total_return=0.18, sharpe=0.9, max_dd=-0.10, calmar=1.5),
        }
        trades = [{"reason": "sell_alpha_decay:rank_drop"}]
        result = PromotionGate.evaluate(a0_metrics=a0, a9_metrics=a9, a9_trade_rows=trades)
        assert result.cumulative_return_improved is True
        assert result.sharpe_improved is True
        assert result.drawdown_improved is True
        assert result.calmar_improved is True
        assert result.has_decay_exits is True
        assert result.passed is True

    def test_promotion_requires_2_of_3_windows(self):
        """A9 must win return in ≥2/3 windows."""
        a0 = {
            "2025Q1": _make_metrics(total_return=0.10),
            "2025Q3": _make_metrics(total_return=0.15),
            "2026Q1": _make_metrics(total_return=0.20),
        }
        a9 = {
            "2025Q1": _make_metrics(total_return=0.12),
            "2025Q3": _make_metrics(total_return=0.10),
            "2026Q1": _make_metrics(total_return=0.18),
        }
        trades = [{"reason": "sell_alpha_decay:rank_drop"}]
        result = PromotionGate.evaluate(a0_metrics=a0, a9_metrics=a9, a9_trade_rows=trades)
        # Only wins 1/3 windows (2025H1)
        assert result.windows_passed < 2
        assert result.passed is False


# ---------------------------------------------------------------------------
# Reporter Tests
# ---------------------------------------------------------------------------


class TestReporter:
    def test_report_json_valid(self):
        decision = PromotionDecision(
            strategy_id="test", recommend_promotion=True,
            overall_score=1.0, gates_passed=5, gates_total=5,
            conditions_passed=10, conditions_total=10,
            evidence=[PromotionEvidence("g1", "c1", True, 1, "≥1")],
            summary="PROMOTION RECOMMENDED",
        )
        s = PromotionReporter.report_json(decision)
        parsed = json.loads(s)
        assert parsed["recommend_promotion"] is True

    def test_report_markdown_includes_evidence(self):
        decision = PromotionDecision(
            strategy_id="test", recommend_promotion=False,
            overall_score=0.5, gates_passed=2, gates_total=4,
            conditions_passed=5, conditions_total=10,
            evidence=[
                PromotionEvidence("g1", "c1", True, 1, "≥1"),
                PromotionEvidence("g2", "c2", False, 0, "≥1"),
            ],
            failure_reasons=["g2/c2: failed"],
            summary="BLOCKED",
        )
        md = PromotionReporter.report_markdown(decision)
        assert "BLOCKED" in md
        assert "g1" in md
        assert "g2" in md

    def test_report_summary_keys(self):
        decision = PromotionDecision(
            strategy_id="test", recommend_promotion=True,
            overall_score=1.0, gates_passed=3, gates_total=3,
            conditions_passed=6, conditions_total=6,
        )
        summary = PromotionReporter.report_summary(decision)
        assert "strategy_id" in summary
        assert "recommend_promotion" in summary
        assert "gate_results" in summary


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_results_no_promotion(self):
        evaluator = PromotionEvaluator(all_results={}, gate_results={})
        decision = evaluator.evaluate()
        assert decision.recommend_promotion is False
        assert decision.conditions_total == 0

    def test_missing_a0_or_a9_no_promotion(self):
        """PromotionGate with missing metrics should fail gracefully."""
        a0 = {"2025Q1": _make_metrics(total_return=0.10)}
        result = PromotionGate.evaluate(a0_metrics=a0, a9_metrics={})
        assert result.passed is False

    def test_partial_window_data(self):
        """Only 1 window available → can't pass ≥2 windows."""
        a0 = {"2025Q1": _make_metrics(total_return=0.10)}
        a9 = {"2025Q1": _make_metrics(total_return=0.12)}
        trades = [{"reason": "sell_alpha_decay:rank_drop"}]
        result = PromotionGate.evaluate(a0_metrics=a0, a9_metrics=a9, a9_trade_rows=trades)
        assert result.windows_passed == 1
        # 1 < 2 → fails even though A9 wins the only window
        assert result.passed is False


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_same_evidence_same_decision(self):
        e1 = PromotionDecision(
            strategy_id="test", recommend_promotion=True,
            overall_score=1.0, gates_passed=5, gates_total=5,
            conditions_passed=5, conditions_total=5,
        )
        e2 = PromotionDecision(
            strategy_id="test", recommend_promotion=True,
            overall_score=1.0, gates_passed=5, gates_total=5,
            conditions_passed=5, conditions_total=5,
        )
        assert e1.recommend_promotion == e2.recommend_promotion
        assert e1.overall_score == e2.overall_score

    def test_promotion_decision_roundtrip_json(self):
        decision = PromotionDecision(
            strategy_id="roundtrip_test", recommend_promotion=True,
            overall_score=0.9, gates_passed=4, gates_total=5,
            conditions_passed=8, conditions_total=10,
            evidence=[PromotionEvidence("g1", "c1", True, "val", "thresh")],
            summary="test",
        )
        s = PromotionReporter.report_json(decision)
        parsed = json.loads(s)
        assert parsed["strategy_id"] == "roundtrip_test"
        assert parsed["overall_score"] == 0.9

    def test_all_gate_names_present(self):
        evaluator = PromotionEvaluator()
        gate_names = evaluator.GATE_NAMES
        assert "comparison" in gate_names
        assert "industry_neutral_alpha" in gate_names
        assert "risk_portfolio" in gate_names
        assert "decay_exit" in gate_names
        assert "promotion" in gate_names


# ---------------------------------------------------------------------------
# PromotionGateResult
# ---------------------------------------------------------------------------


class TestPromotionGateResult:
    def test_default_not_passed(self):
        r = PromotionGateResult(passed=False)
        assert r.passed is False
        assert r.windows_passed == 0

    def test_gate_summary_keys(self):
        r = PromotionGateResult(passed=True, windows_passed=3, has_decay_exits=True)
        s = PromotionGate.gate_summary(r)
        assert s["passed"] is True
        assert "conditions" in s
