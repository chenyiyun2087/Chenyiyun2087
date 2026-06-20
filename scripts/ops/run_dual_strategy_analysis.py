r"""Dual-strategy confrontation analysis + Feishu notification.

Runs both production and high-return strategies, normalizes their signals,
computes divergence/convergence metrics, and pushes a 4-card Feishu report:

  1. 【生产策略信号】— production Top5, weights, risk grade (FOR EXECUTION)
  2. 【高收益策略-OBS】— research Top5, risk level, research-only flag (OBSERVE ONLY)
  3. 【策略分歧预警】— overlap, divergence, consistency score, interpretation
  4. 【风险雷达】— risk gap comparison across dimensions

Usage:
  python scripts/ops/run_dual_strategy_analysis.py --date 20260620 [--notify-feishu]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Strategy pair configuration
PRODUCTION_STRATEGY = "production_governed_vol_position"
HIGH_RETURN_STRATEGY = "tiered_liquidity_then_bs_v2"
HIGH_RETURN_DISPLAY = "流动性分层B点进攻策略"

# Display names
STRATEGY_DISPLAY = {
    PRODUCTION_STRATEGY: "生产策略（波动仓位）",
    HIGH_RETURN_STRATEGY: HIGH_RETURN_DISPLAY,
}


def _normalize_date(raw: str | None) -> str:
    if not raw:
        return date.today().strftime("%Y-%m-%d")
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def load_strategy_signal(
    engine,
    strategy_id: str,
    asof_date: str,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """Load candidate signal from ads_trusted_strategy_candidates for a strategy."""
    from sqlalchemy import text

    sql = text(
        "SELECT symbol, stock_name, score, rank_score, effective_weight, industry "
        "FROM chenyiyun.ads_trusted_strategy_candidates "
        "WHERE strategy = :sid AND trade_date = :d "
        "ORDER BY rank_score DESC LIMIT :n"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sql, {"sid": strategy_id, "d": asof_date, "n": top_n}
            ).mappings().fetchall()
    except Exception:
        return None

    if not rows:
        return None

    candidates = [dict(r) for r in rows]
    top5_stocks = [str(r.get("symbol", "")).zfill(6) for r in candidates[:5]]
    top5_weights = [float(r.get("effective_weight", 0) or 0) for r in candidates[:5]]
    top5_scores = [float(r.get("rank_score", 0) or 0) for r in candidates[:5]]

    # Industry concentration
    industries = [r.get("industry") for r in candidates if r.get("industry")]
    industry_counts: dict[str, int] = {}
    for ind in industries:
        industry_counts[str(ind)] = industry_counts.get(str(ind), 0) + 1
    max_ind_count = max(industry_counts.values()) if industry_counts else 0
    concentration = max_ind_count / len(candidates) if candidates else 0.0

    return {
        "strategy_id": strategy_id,
        "strategy_display_name": STRATEGY_DISPLAY.get(strategy_id, strategy_id),
        "signal_date": asof_date,
        "top5_stocks": top5_stocks,
        "top5_weights": top5_weights,
        "top5_scores": top5_scores,
        "top5_names": [str(r.get("stock_name", "")) for r in candidates[:5]],
        "expected_return_score": float(candidates[0].get("rank_score", 0)) if candidates else 0,
        "risk_score": 0.0,
        "liquidity_score": 0.0,
        "concentration_score": round(concentration, 4),
        "position_ratio": 0.70,
        "total_candidates": len(candidates),
    }


def build_production_card(signal: dict[str, Any]) -> str:
    """Format production strategy Feishu card — FOR EXECUTION."""
    lines = [
        "🟢 【生产策略信号】",
        f"日期：{signal['signal_date']}",
        f"策略：{signal['strategy_display_name']}",
        f"仓位：{int(signal['position_ratio'] * 100)}%",
        "",
        "Top5 候选：",
    ]
    names = signal.get("top5_names", signal["top5_stocks"])
    for i, (sym, name, w, s) in enumerate(zip(
        signal["top5_stocks"], names,
        signal["top5_weights"], signal["top5_scores"],
    )):
        lines.append(f"  {i+1}. {sym} {name} 权重={w:.1%} 分={s:.2f}")

    lines.extend([
        "",
        "✔ 用于执行（唯一交易来源）",
    ])
    return "\n".join(lines)


def build_research_card(signal: dict[str, Any]) -> str:
    """Format high-return strategy Feishu card — OBSERVE ONLY."""
    lines = [
        "🔴 【高收益策略 · OBS】",
        f"日期：{signal['signal_date']}",
        f"策略：{signal['strategy_display_name']}",
        f"风险等级：HIGH",
        f"建议仓位：0–20%（仅研究）",
        "",
        "Top5 候选：",
    ]
    names = signal.get("top5_names", signal["top5_stocks"])
    for i, (sym, name, w, s) in enumerate(zip(
        signal["top5_stocks"], names,
        signal["top5_weights"], signal["top5_scores"],
    )):
        lines.append(f"  {i+1}. {sym} {name} 权重={w:.1%} 分={s:.2f}")

    lines.extend([
        "",
        "⚠️ 仅观察，不交易",
    ])
    return "\n".join(lines)


def build_divergence_card(report: dict[str, Any]) -> str:
    """Format divergence alert Feishu card."""
    consistency = report.get("consistency_score", 0)
    level = report.get("divergence_level", "?")
    shared = report.get("shared_stocks", [])

    emoji = "🟢" if consistency > 0.7 else ("🟡" if consistency > 0.4 else "🔴")

    lines = [
        f"{emoji} 【策略分歧预警】",
        f"日期：{report.get('signal_date', '?')}",
        f"一致性评分：{consistency:.2f}（{level}）",
        "",
        "重合股票：" + (", ".join(shared) if shared else "无"),
        f"重合度：{report['overlap_ratio']:.0%}",
        f"分歧度：{report['divergence_score']:.0%}",
        "",
        f"市场解读：{report['regime_signal']}",
        f"建议：{report['suggested_action']}",
    ]
    return "\n".join(lines)


def build_risk_radar_card(
    prod_signal: dict[str, Any],
    research_signal: dict[str, Any],
    risk_gap: dict[str, float],
) -> str:
    """Format risk radar Feishu card."""
    lines = [
        "📊 【风险雷达】",
        "",
        "高收益策略 vs 生产策略：",
    ]

    if "volatility_gap" in risk_gap:
        vg = risk_gap["volatility_gap"]
        arrow = "↑" if vg > 0 else "↓"
        lines.append(f"  波动率：{arrow} {abs(vg):.1%}")

    if "drawdown_gap" in risk_gap:
        dg = risk_gap["drawdown_gap"]
        arrow = "↑" if dg > 0 else "↓"
        lines.append(f"  回撤：{arrow} {abs(dg):.1%}")

    cg = risk_gap.get("concentration_gap", 0)
    arrow = "↑" if cg > 0 else "↓"
    lines.append(f"  集中度：{arrow} {abs(cg):.1%}")

    # Risk assessment
    conc = research_signal.get("concentration_score", 0)
    lines.append("")
    if conc > 0.6:
        lines.append("⚠️ 当前不适合资金跟随 — 风险特征过于激进")
    elif conc > 0.4:
        lines.append("⚡ 风险可控但偏高 — 适合小仓位观察")
    else:
        lines.append("✅ 风险特征在可接受范围内")

    return "\n".join(lines)


def run_dual_analysis(
    engine,
    asof_date: str,
    notify_feishu: bool = False,
) -> dict[str, Any]:
    """Run full dual-strategy analysis and optionally push Feishu cards."""

    # Load both signals
    prod = load_strategy_signal(engine, PRODUCTION_STRATEGY, asof_date)
    research = load_strategy_signal(engine, HIGH_RETURN_STRATEGY, asof_date)

    if not prod:
        return {"status": "SKIP", "reason": "No production signal found"}
    if not research:
        return {"status": "SKIP", "reason": "No research signal found"}

    # Compute divergence
    from runtime.strategy_signal import StrategySignal
    prod_sig = StrategySignal(**{k: v for k, v in prod.items() if k in StrategySignal.__dataclass_fields__})
    research_sig = StrategySignal(**{k: v for k, v in research.items() if k in StrategySignal.__dataclass_fields__})

    from runtime.divergence_engine import compute_divergence
    report = compute_divergence(prod_sig, research_sig)

    result = {
        "signal_date": asof_date,
        "production": prod,
        "research": research,
        "divergence": {
            "shared_stocks": report.shared_stocks,
            "overlap_ratio": report.overlap_ratio,
            "divergence_score": report.divergence_score,
            "divergence_level": report.divergence_level,
            "consistency_score": report.consistency_score,
            "risk_gap": report.risk_gap,
            "risk_warning": report.risk_warning,
            "regime_signal": report.regime_signal,
            "suggested_action": report.suggested_action,
        },
    }

    # Feishu push
    if notify_feishu:
        from scripts.ops.feishu_notifier import (
            load_feishu_webhook, send_feishu_text, strategy_identity_block,
        )
        webhook = load_feishu_webhook(engine)
        if webhook:
            cards = [
                ("生产策略", build_production_card(prod)),
                ("高收益策略-OBS", build_research_card(research)),
                ("策略分歧预警", build_divergence_card(result["divergence"])),
                ("风险雷达", build_risk_radar_card(prod, research, report.risk_gap)),
            ]
            results = {}
            for label, card in cards:
                full = f"{strategy_identity_block()}\n\n{card}"
                ok, reason = send_feishu_text(webhook, full)
                results[label] = "ok" if ok else f"FAIL: {reason}"
            result["feishu"] = results
        else:
            result["feishu"] = {"error": "no_webhook"}

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dual-strategy confrontation analysis + Feishu notification."
    )
    parser.add_argument("--date", default=None, help="Signal date YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--notify-feishu", action="store_true",
                       help="Push 4-card Feishu report.")
    parser.add_argument("--output", default=None,
                       help="Output JSON path (default: stdout).")
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from scoreRank.core.db_config import build_sqlalchemy_url

    engine = create_engine(build_sqlalchemy_url())
    asof = _normalize_date(args.date)

    result = run_dual_analysis(engine, asof, notify_feishu=args.notify_feishu)

    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
