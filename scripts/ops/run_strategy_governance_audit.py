"""Produce a reproducible, fail-closed audit inventory for strategy governance.

The report intentionally distinguishes candidate generation from permission to
trade.  It never submits an order and does not enable the configured canary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.canary_governance import decision_payload, evaluate_canary_eligibility
from scripts.ops.production_config import load_production_config


ORDER_DETAIL_STRATEGIES = (
    "tiered_liquidity_then_bs_v2", "baseline_full_dynamic_factor_industry_cap2",
    "baseline_full_liquidity_detail", "baseline_full_liquidity_detail_hold12_shadow",
    "baseline_full_liquidity_detail_market_gate_pos50_shadow", "baseline_full_liquidity_shadow",
    "baseline_full_liquidity_detail_vol_position_shadow", "baseline_full_liquidity_detail_hist_mdd_position_shadow",
    "baseline_full_score", "adaptive_style_switch_dynamic_position", "adaptive_style_shadow",
    "ashare_auto_shadow", "ashare_trend_breakout_shadow", "ashare_hybrid_conservative_shadow",
    "dual_system_adaptive_route",
)
GOVERNED_RESEARCH = (
    "production_governed_vol_position_v1_1_recovery",
    "production_governed_vol_position_v1_2_recovery",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_adaptive_pattern_guard",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strategy_inventory(config: dict[str, Any]) -> list[dict[str, Any]]:
    primary = str(config["primary_strategy"])
    selection = str(config["primary_selection_strategy"])
    rows = [
        {"strategy": primary, "tier": "production", "live_candidate": True},
        {"strategy": selection, "tier": "production_selection", "live_candidate": selection == primary},
        {"strategy": str(config["shadow_risk_strategy"]), "tier": "shadow_risk", "live_candidate": False},
        {"strategy": str(config["defensive_fallback_strategy"]), "tier": "defensive", "live_candidate": False},
    ]
    rows += [{"strategy": item, "tier": "shadow_or_historical", "live_candidate": False} for item in ORDER_DETAIL_STRATEGIES]
    rows += [{"strategy": item, "tier": "research_only", "live_candidate": False} for item in GOVERNED_RESEARCH]
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped.setdefault(row["strategy"], row)
    return list(deduped.values())


def build_audit(config: dict[str, Any]) -> dict[str, Any]:
    canary = dict(config["live_canary"])
    decision = evaluate_canary_eligibility(
        canary, strict_ledger_passed=False, enabled_shadow_passed=False,
        shadow_real_trading_days=0, completed_round_trips=0,
        health_grade="UNKNOWN", release_approved=False,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_status": "BLOCKED_PENDING_EVIDENCE",
        "future_function_controls": {
            "signal_execution": "T-day signal; T+1 execution only",
            "dynamic_weight_history": "exit_date < signal_date",
            "model_risk_fields_allowed": bool(config["allow_model_risk_fields"]),
            "strict_precommit_required_for_promotion": True,
        },
        "execution_controls": {
            "broker_api_enabled": False,
            "manual_confirmation_only": True,
            "lot_size": 100,
            "max_positions": config["max_total_positions"],
            "configured_execution_mode": config["execution_mode"],
        },
        "config": config,
        "strategy_inventory": strategy_inventory(config),
        "canary_decision_without_runtime_evidence": decision_payload(decision),
        "required_evidence": [
            "strict ledger VERIFIED", "enabled shadow >= 20 real trading days",
            "GREEN daily health", "recorded release approval", "manual broker fill reconciliation",
        ],
    }


def write_audit(output_dir: Path) -> dict[str, Path]:
    config = load_production_config()
    report = build_audit(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "strategy_governance_audit.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path = output_dir / "strategy_governance_audit.md"
    lines = ["# 全策略治理审计", "", f"- 状态：`{report['audit_status']}`", f"- 配置 SHA：`{config['config_sha']}`", "", "## 实盘结论", "", "当前没有可提交的新买单；系统仅允许人工确认 Canary，且必须补齐严格账本、影子盘、健康度和审批证据。", "", "## 策略台账", "", "|策略|分层|实盘候选|", "|---|---|---|"]
    lines += [f"|{r['strategy']}|{r['tier']}|{'是' if r['live_candidate'] else '否'}|" for r in report["strategy_inventory"]]
    lines += ["", "## 未来函数控制", "", "- T 日信号、T+1 执行；动态权重仅使用 `exit_date < signal_date`。", "- `bs_model_*` 等 model-risk 字段维持禁用。"]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a read-only strategy governance audit.")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "exports" / "strategy_governance" / stamp
    paths = write_audit(out)
    print(json.dumps({k: str(v) for k, v in paths.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
