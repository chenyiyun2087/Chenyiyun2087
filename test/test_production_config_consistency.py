from pathlib import Path
import os

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from scripts.ops.production_config import load_production_config
import scheduler
import web.app as web_app
from scripts.ops import export_trusted_strategy_candidates as export_candidates
from scripts.ops import run_strategy_performance_review as review

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shared_production_defaults_are_consistent():
    config = load_production_config()

    assert export_candidates.DEFAULT_RISK_PROFILE == config["risk_profile"]
    assert export_candidates.DEFAULT_STRATEGY == config["primary_strategy"]
    assert review.DEFAULT_RISK_PROFILE == config["risk_profile"]
    assert review.DEFAULT_STRATEGY == config["primary_strategy"]
    assert review.DEFAULT_TOP_N == config["top_n"]
    assert review.DEFAULT_MAX_TOTAL_POSITIONS == config["max_total_positions"]
    assert review.DEFAULT_POSITION_RATIO == config["position_ratio"]
    assert review.DEFAULT_HOLD_DAYS == config["hold_days"]
    assert web_app.TRUSTED_PRODUCTION_RISK_PROFILE == config["risk_profile"]
    assert web_app.TRUSTED_PRODUCTION_STRATEGY == config["primary_strategy"]
    assert config["primary_strategy"] == "production_governed_vol_position"
    assert config["primary_selection_strategy"] == "baseline_full_liquidity_detail_vol_position"
    assert config["research_shadow_candidate"]["enabled"] is False


def test_scheduler_uses_shared_production_defaults():
    config = load_production_config()
    task_args = scheduler.TASKS["daily_pipeline"]
    assert task_args["type"] == "pipeline"
    assert scheduler.PRODUCTION_CONFIG["primary_strategy"] == config["primary_strategy"]


def test_runbook_no_longer_declares_adaptive_as_primary_strategy():
    path = PROJECT_ROOT / "docs/00_project_overview/RUNBOOK.md"
    content = path.read_text(encoding="utf-8")
    assert "主策略为 `adaptive_market_style`" not in content
