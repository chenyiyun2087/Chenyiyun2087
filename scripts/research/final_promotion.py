"""PR15: Final promotion evaluation — stitched OOS NAV, comprehensive report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class StitchedOOSResult:
    """Aggregated OOS performance across all walk-forward windows."""
    cumulative_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    cvar_95: float = 0.0
    worst_day: float = 0.0
    ann_volatility: float = 0.0
    turnover: float = 0.0
    total_cost: float = 0.0
    trading_days: int = 0
    windows: list[str] = field(default_factory=list)


@dataclass
class FinalPromotionReport:
    strategy_id: str = "full_strategy_v3"
    recommend_promotion: bool = False
    stitched_oos: StitchedOOSResult | None = None
    gate_results: dict[str, bool] = field(default_factory=dict)
    evidence_count: int = 0
    evidence_passed: int = 0
    security_selection_pct: float = 0.0
    passes_10bp_stress: bool = False
    passes_capacity_1m: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    summary: str = ""


def stitch_oos_nav(fold_results: list[Any]) -> pd.Series:
    """Stitch OOS NAV by compounding fold returns across boundaries.

    Each fold may restart at NAV=1.  Direct concatenation would create an
    artificial boundary jump, so each fold is converted to returns first.
    """
    stitched_rows: list[dict] = []
    running_nav = 1.0
    seen_dates: set[str] = set()
    for fr in sorted(fold_results, key=lambda f: getattr(f, "fold_index", 0)):
        frame = pd.DataFrame(getattr(fr, "nav_rows", []))
        if frame.empty or not {"trade_date", "nav"}.issubset(frame.columns):
            continue
        frame = frame[["trade_date", "nav"]].dropna().drop_duplicates("trade_date").sort_values("trade_date")
        if len(frame) < 2:
            continue
        if not stitched_rows:
            base_date = str(frame.iloc[0]["trade_date"])
            stitched_rows.append({"trade_date": base_date, "nav": running_nav})
            seen_dates.add(base_date)
        returns = pd.to_numeric(frame["nav"], errors="coerce").pct_change()
        for idx in range(1, len(frame)):
            trade_date = str(frame.iloc[idx]["trade_date"])
            if trade_date in seen_dates:
                raise ValueError(f"overlapping OOS trade date: {trade_date}")
            daily_return = float(returns.iloc[idx])
            if not np.isfinite(daily_return):
                raise ValueError(f"invalid OOS return on {trade_date}")
            running_nav *= 1.0 + daily_return
            stitched_rows.append({"trade_date": trade_date, "nav": running_nav})
            seen_dates.add(trade_date)
    if not stitched_rows:
        return pd.Series([], dtype=float)
    return pd.Series(
        [row["nav"] for row in stitched_rows],
        index=pd.to_datetime([row["trade_date"] for row in stitched_rows]),
        dtype=float,
    )


def compute_stitched_metrics(nav: pd.Series) -> StitchedOOSResult:
    if nav.empty or len(nav) < 2:
        return StitchedOOSResult()
    total_ret = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    days = len(nav)
    ann_ret = float((nav.iloc[-1] / nav.iloc[0]) ** (252 / max(days, 1)) - 1.0)
    dd = (nav / nav.cummax() - 1.0)
    max_dd = float(dd.min())
    daily_rets = nav.pct_change().dropna()
    sharpe = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 0 else 0.0
    calmar = float(ann_ret / abs(max_dd)) if max_dd < -1e-9 else 0.0
    cvar = float(daily_rets.nsmallest(max(1, int(len(daily_rets) * 0.05))).mean())
    worst = float(daily_rets.min()) if not daily_rets.empty else 0.0
    ann_vol = float(daily_rets.std() * np.sqrt(252)) if not daily_rets.empty else 0.0
    return StitchedOOSResult(
        cumulative_return=total_ret, annualized_return=ann_ret,
        max_drawdown=max_dd, sharpe_ratio=sharpe, calmar_ratio=calmar,
        cvar_95=cvar, worst_day=worst, ann_volatility=ann_vol,
        trading_days=days,
    )


def generate_final_report(
    stitched_a9: StitchedOOSResult,
    stitched_c0: StitchedOOSResult,
    promotion_decision: Any,
    output_dir: Path,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    decision = promotion_decision
    excess = stitched_a9.annualized_return - stitched_c0.annualized_return
    dd_improvement = abs(stitched_c0.max_drawdown) - abs(stitched_a9.max_drawdown)

    report = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "strategy": "full_strategy_v3",
        "recommend_promotion": getattr(decision, "recommend_promotion", False),
        "stitched_oos": {
            "a9": {
                "cumulative_return": stitched_a9.cumulative_return,
                "annualized_return": stitched_a9.annualized_return,
                "max_drawdown": stitched_a9.max_drawdown,
                "sharpe_ratio": stitched_a9.sharpe_ratio,
                "calmar_ratio": stitched_a9.calmar_ratio,
                "cvar_95": stitched_a9.cvar_95,
                "worst_day": stitched_a9.worst_day,
                "trading_days": stitched_a9.trading_days,
            },
            "c0": {
                "cumulative_return": stitched_c0.cumulative_return,
                "annualized_return": stitched_c0.annualized_return,
                "max_drawdown": stitched_c0.max_drawdown,
                "sharpe_ratio": stitched_c0.sharpe_ratio,
                "calmar_ratio": stitched_c0.calmar_ratio,
            },
            "excess_return": excess,
            "dd_improvement": dd_improvement,
        },
        "gate_summary": {},
        "failure_reasons": getattr(decision, "failure_reasons", []),
        "evidence_count": getattr(decision, "conditions_total", 0),
        "evidence_passed": getattr(decision, "conditions_passed", 0),
        "overall_score": getattr(decision, "overall_score", 0.0),
    }

    # Gate summary
    if hasattr(decision, "evidence"):
        for e in decision.evidence:
            gate = e.gate_name if hasattr(e, "gate_name") else "unknown"
            if gate not in report["gate_summary"]:
                report["gate_summary"][gate] = "PASS"
            if hasattr(e, "passed") and not e.passed:
                report["gate_summary"][gate] = "FAIL"

    # Write reports
    json_path = output_dir / "promotion_decision.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Markdown report
    md_lines = [
        f"# Full Strategy V3 — Final Promotion Evaluation",
        "",
        f"**Recommendation**: {'✅ PROMOTE' if report['recommend_promotion'] else '❌ BLOCKED'}",
        f"**Overall Score**: {report['overall_score']:.1%}",
        "",
        "## Stitched OOS Performance",
        "",
        f"| Metric | A9 (V3) | C0 (Champion) |",
        f"|---|---|---|",
        f"| Cumulative Return | {stitched_a9.cumulative_return:.2%} | {stitched_c0.cumulative_return:.2%} |",
        f"| Annualized Return | {stitched_a9.annualized_return:.2%} | {stitched_c0.annualized_return:.2%} |",
        f"| Max Drawdown | {stitched_a9.max_drawdown:.2%} | {stitched_c0.max_drawdown:.2%} |",
        f"| Sharpe | {stitched_a9.sharpe_ratio:.2f} | {stitched_c0.sharpe_ratio:.2f} |",
        f"| Calmar | {stitched_a9.calmar_ratio:.2f} | {stitched_c0.calmar_ratio:.2f} |",
        f"| CVaR 95% | {stitched_a9.cvar_95:.2%} | — |",
        f"| Worst Day | {stitched_a9.worst_day:.2%} | — |",
        f"| Excess Return | {excess:.2%} | — |",
        "",
        "## Gate Results",
        "",
    ]
    for gate, status in report["gate_summary"].items():
        icon = "✅" if status == "PASS" else "❌"
        md_lines.append(f"- {icon} **{gate}**: {status}")
    if report["failure_reasons"]:
        md_lines.append("")
        md_lines.append("## Failure Reasons")
        for r in report["failure_reasons"]:
            md_lines.append(f"- {r}")

    md_path = output_dir / "promotion_report.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    return {"json_path": str(json_path), "md_path": str(md_path), "report": report}
