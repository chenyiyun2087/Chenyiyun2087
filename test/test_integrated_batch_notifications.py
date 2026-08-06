from __future__ import annotations

from pathlib import Path

import yaml

from scripts.ops.run_integrated_batch_audit import attention_rows
from scripts.ops.run_integrated_strategy_review import build_rolling_score_section
from web.task_commands import TaskCommandContext, build_task_script_parts


def _context() -> TaskCommandContext:
    root = Path(__file__).resolve().parents[1]
    payload = yaml.safe_load(
        (root / "task_registry" / "pipeline.yaml").read_text(encoding="utf-8")
    )
    tasks = {}
    for group in payload.values():
        for task in group.get("tasks", []):
            tasks[task["id"]] = task
    return TaskCommandContext(
        tasks=tasks,
        normalize_datestr=lambda value: str(value).replace("-", "") if value else None,
        trusted_strategy="production_governed_vol_position",
        trusted_risk_profile="adaptive",
        trusted_config={"config_sha": "abc123", "top_n": 5, "max_total_positions": 10},
        daily_audit_task="ops_daily_batch_audit",
    )


def test_routine_source_jobs_are_silent_and_digest_job_notifies():
    context = _context()
    options = {"datestr": "20260806"}

    rolling = build_task_script_parts("rolling_strategy_scorer", options, context)
    candidates = build_task_script_parts("trusted_strategy_candidates", options, context)
    shadow = build_task_script_parts("trusted_strategy_shadow_monitor", options, context)
    digest = build_task_script_parts("trusted_strategy_performance_review", options, context)
    audit = build_task_script_parts("ops_daily_batch_audit", options, context)

    assert "--no-push" in rolling
    assert "--notify-feishu" not in candidates
    assert "--notify-feishu" not in shadow
    assert "--notify-feishu" in digest
    assert "--notify-feishu" in audit


def test_pipeline_routes_to_integrated_wrappers_and_orders_dependencies():
    context = _context()
    digest = context.tasks["trusted_strategy_performance_review"]
    audit = context.tasks["ops_daily_batch_audit"]

    assert digest["script"] == "scripts/ops/run_integrated_strategy_review.py"
    assert audit["script"] == "scripts/ops/run_integrated_batch_audit.py"
    assert set(digest["depends_on"]) == {
        "trusted_strategy_backtest",
        "rolling_strategy_scorer",
        "trusted_strategy_candidates",
        "trusted_strategy_shadow_monitor",
    }


def test_integrated_score_section_contains_rank_weight_and_risk_context():
    section = build_rolling_score_section(
        {
            "performance_date": "2026-08-06",
            "weight_date": "2026-08-06",
            "effective_exposure": 0.7,
            "circuit_breaker_active": False,
            "rows": [
                {
                    "strategy": "strategy_a",
                    "final_score": 82.5,
                    "smooth_weight": 0.4,
                    "total_return": 0.12,
                    "sharpe": 1.5,
                    "calmar": 1.1,
                    "max_drawdown": -0.08,
                }
            ],
            "warnings": [],
        }
    )

    assert "滚动策略评分与权重" in section
    assert "#1 strategy_a" in section
    assert "评分 82.5" in section
    assert "平滑权重 +40.0%" in section
    assert "有效敞口：+70%" in section
    assert "不替代生产风险总闸或资金授权" in section


def test_batch_audit_is_anomaly_only():
    summary = {
        "rows": [
            {"task_name": "a", "status": "OK"},
            {"task_name": "b", "status": "SKIPPED_NON_TRADING"},
            {"task_name": "c", "status": "NOTIFICATION_MISSING"},
            {"task_name": "d", "status": "FAILED"},
        ]
    }
    assert [row["task_name"] for row in attention_rows(summary)] == ["c", "d"]
