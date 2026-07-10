"""Full strategy v3 promotion evaluation — the PR6 capstone.

PromotionEvaluator aggregates gate results from PR2-5 and adds a final
head-to-head PromotionGate (A9 vs A0) to determine whether the full v3
strategy should replace the current production baseline.

Design: purely evaluative — no new strategy logic. Reads existing
walk-forward results and gates, builds evidence, makes a recommendation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PromotionEvidence:
    """Structured evidence for one gate condition."""

    gate_name: str        # "comparison", "industry_neutral_alpha", ...
    condition: str        # "return_improved", "all_factors_bh", ...
    passed: bool
    value: Any            # measured value
    threshold: Any        # required threshold
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_name": self.gate_name,
            "condition": self.condition,
            "passed": self.passed,
            "value": str(self.value),
            "threshold": str(self.threshold),
            "detail": self.detail,
        }


@dataclass
class PromotionDecision:
    """Final promotion decision with complete evidence trail."""

    strategy_id: str
    recommend_promotion: bool
    overall_score: float      # fraction of conditions passed
    gates_passed: int
    gates_total: int
    conditions_passed: int
    conditions_total: int
    evidence: list[PromotionEvidence] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "recommend_promotion": self.recommend_promotion,
            "overall_score": self.overall_score,
            "gates_passed": self.gates_passed,
            "gates_total": self.gates_total,
            "conditions_passed": self.conditions_passed,
            "conditions_total": self.conditions_total,
            "evidence": [e.to_dict() for e in self.evidence],
            "failure_reasons": self.failure_reasons,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# PromotionEvaluator
# ---------------------------------------------------------------------------


class PromotionEvaluator:
    """Evaluates A9 (full v3) promotion readiness by aggregating all gates."""

    GATE_NAMES = [
        "comparison",
        "industry_neutral_alpha",
        "risk_portfolio",
        "decay_exit",
        "promotion",
    ]

    def __init__(
        self,
        all_results: dict[str, list[Any]] | None = None,
        gate_results: dict[str, Any] | None = None,
    ) -> None:
        self.all_results = all_results or {}
        self.gate_results = gate_results or {}

    def evaluate(self) -> PromotionDecision:
        """Run the complete promotion evaluation.

        Returns PromotionDecision with full evidence trail.
        """
        evidence: list[PromotionEvidence] = []
        failure_reasons: list[str] = []

        # 1. ComparisonGate (PR2) — A0 vs A1/A2/A3
        cg = self.gate_results.get("comparison_gate")
        if cg is not None:
            evidence.append(PromotionEvidence(
                gate_name="comparison", condition="a0_gate",
                passed=bool(getattr(cg, "passed", False)),
                value=getattr(cg, "windows_passed", 0),
                threshold="≥2/3 windows",
                detail=f"{getattr(cg, 'windows_passed', 0)}/{getattr(cg, 'windows_total', 3)} windows passed",
            ))

        # 2. IndustryNeutralAlphaGate (PR3) — A7 factor diagnostics
        ina = self.gate_results.get("industry_neutral_alpha_gate")
        if ina is not None:
            evidence.append(PromotionEvidence(
                gate_name="industry_neutral_alpha", condition="all_factors_bh",
                passed=bool(getattr(ina, "all_factors_bh_pass", False)),
                value=getattr(ina, "factors_bh_pass_count", 0),
                threshold="6/6 factors BH q≤0.10",
            ))
            evidence.append(PromotionEvidence(
                gate_name="industry_neutral_alpha", condition="all_factors_oos",
                passed=bool(getattr(ina, "all_factors_oos_pass", False)),
                value=getattr(ina, "factors_oos_pass_count", 0),
                threshold="6/6 factors OOS",
            ))
            evidence.append(PromotionEvidence(
                gate_name="industry_neutral_alpha", condition="all_factors_stability",
                passed=bool(getattr(ina, "all_factors_stability_ok", False)),
                value="ok" if getattr(ina, "all_factors_stability_ok", False) else "fail",
                threshold="industry/cap stability < 0.50",
            ))

        # 3. RiskPortfolioGate (PR4) — A8 vs A7
        rpg = self.gate_results.get("risk_portfolio_gate")
        if rpg is not None:
            for cond in ["return_improved", "drawdown_improved", "volatility_reduced", "concentration_ok"]:
                evidence.append(PromotionEvidence(
                    gate_name="risk_portfolio", condition=cond,
                    passed=bool(getattr(rpg, cond, False)),
                    value=getattr(rpg, cond, False),
                    threshold="true",
                ))

        # 4. DecayExitGate (PR5) — A9 has decay exits
        deg = self.gate_results.get("decay_exit_gate")
        if deg is not None:
            evidence.append(PromotionEvidence(
                gate_name="decay_exit", condition="has_decay_exits",
                passed=bool(getattr(deg, "has_decay_exits", False)),
                value=getattr(deg, "has_decay_exits", False),
                threshold="true",
            ))
        elif "A9" in self.all_results:
            # Check trade rows for decay exits
            a9_results = self.all_results["A9"]
            decay_count = 0
            for fr in a9_results:
                for tr in getattr(fr, "trade_rows", []):
                    if isinstance(tr.get("reason", ""), str) and "sell_alpha_decay" in tr["reason"]:
                        decay_count += 1
            has_decay = decay_count > 0
            evidence.append(PromotionEvidence(
                gate_name="decay_exit", condition="has_decay_exits",
                passed=has_decay, value=decay_count, threshold=">0",
                detail=f"{decay_count} decay exit trades found",
            ))

        # 5. PromotionGate (PR6) — A9 vs A0 head-to-head
        pg = self.gate_results.get("promotion_gate")
        if pg is not None:
            for cond in ["cumulative_return", "sharpe", "drawdown", "calmar", "decay_exits"]:
                evidence.append(PromotionEvidence(
                    gate_name="promotion", condition=cond,
                    passed=bool(getattr(pg, "conditions", {}).get(cond, False)),
                    value=getattr(pg, "conditions", {}).get(cond, False),
                    threshold="A9 > A0",
                ))

        # Aggregate
        conditions_passed = sum(1 for e in evidence if e.passed)
        conditions_total = len(evidence)
        gates_passed = len({e.gate_name for e in evidence if e.passed})
        gates_total = len({e.gate_name for e in evidence})
        overall_score = conditions_passed / max(conditions_total, 1)

        # Collect failures
        for e in evidence:
            if not e.passed:
                failure_reasons.append(f"{e.gate_name}/{e.condition}: failed")

        # Require at least one piece of evidence to recommend promotion
        recommend = len(evidence) > 0 and all(e.passed for e in evidence)

        summary = (
            f"PROMOTION RECOMMENDED" if recommend
            else f"PROMOTION BLOCKED"
        )
        summary += f" — {conditions_passed}/{conditions_total} conditions "
        summary += f"({gates_passed}/{gates_total} gates passed, "
        summary += f"score={overall_score:.1%})"

        return PromotionDecision(
            strategy_id="full_strategy_v3",
            recommend_promotion=recommend,
            overall_score=overall_score,
            gates_passed=gates_passed,
            gates_total=gates_total,
            conditions_passed=conditions_passed,
            conditions_total=conditions_total,
            evidence=evidence,
            failure_reasons=failure_reasons,
            summary=summary,
        )

    def _extract_metrics(self, exp_id: str) -> dict[str, Any]:
        """Extract per-window metrics for an experiment."""
        results = self.all_results.get(exp_id, [])
        metrics: dict[str, Any] = {}
        for fr in results:
            if getattr(fr, "metrics", None) and getattr(fr, "window_label", ""):
                metrics[fr.window_label] = fr.metrics
        return metrics


# ---------------------------------------------------------------------------
# PromotionReporter
# ---------------------------------------------------------------------------


class PromotionReporter:
    """Generates human-readable promotion reports."""

    @staticmethod
    def report_json(decision: PromotionDecision) -> str:
        """Serialize the full decision as JSON."""
        return json.dumps(decision.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def report_summary(decision: PromotionDecision) -> dict[str, Any]:
        """Return a compact summary dict."""
        return {
            "strategy_id": decision.strategy_id,
            "recommend_promotion": decision.recommend_promotion,
            "overall_score": decision.overall_score,
            "gates_passed": decision.gates_passed,
            "gates_total": decision.gates_total,
            "conditions_passed": decision.conditions_passed,
            "conditions_total": decision.conditions_total,
            "gate_results": {
                name: "PASS" if any(
                    e.passed for e in decision.evidence if e.gate_name == name
                ) and all(
                    e.passed for e in decision.evidence if e.gate_name == name
                ) else "FAIL"
                for name in sorted({e.gate_name for e in decision.evidence})
            },
        }

    @staticmethod
    def report_markdown(decision: PromotionDecision) -> str:
        """Generate a markdown promotion report."""
        lines = [
            f"# Strategy Promotion Evaluation: {decision.strategy_id}",
            "",
            f"**Recommendation**: {'✅ PROMOTE' if decision.recommend_promotion else '❌ BLOCKED'}",
            f"**Overall Score**: {decision.overall_score:.1%} "
            f"({decision.conditions_passed}/{decision.conditions_total} conditions, "
            f"{decision.gates_passed}/{decision.gates_total} gates)",
            "",
            "## Gate Results",
            "",
            "| Gate | Conditions | Status |",
            "|------|-----------|--------|",
        ]

        gates: dict[str, list[PromotionEvidence]] = {}
        for e in decision.evidence:
            gates.setdefault(e.gate_name, []).append(e)

        for gate_name in sorted(gates):
            ev_list = gates[gate_name]
            passed_count = sum(1 for e in ev_list if e.passed)
            total = len(ev_list)
            status = "✅ PASS" if passed_count == total else f"❌ FAIL ({passed_count}/{total})"
            conds = ", ".join(e.condition for e in ev_list)
            lines.append(f"| {gate_name} | {conds} | {status} |")

        # Details
        lines.append("")
        lines.append("## Evidence Details")
        lines.append("")
        for e in decision.evidence:
            icon = "✅" if e.passed else "❌"
            lines.append(f"- {icon} **{e.gate_name}/{e.condition}**: "
                         f"value={e.value}, threshold={e.threshold}")

        if decision.failure_reasons:
            lines.append("")
            lines.append("## Failure Reasons")
            for reason in decision.failure_reasons:
                lines.append(f"- ❌ {reason}")

        return "\n".join(lines)
