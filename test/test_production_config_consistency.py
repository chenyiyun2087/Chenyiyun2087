from pathlib import Path
import os

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from scripts.ops.production_config import load_production_config
import web.app as web_app
from scripts.ops import export_trusted_strategy_candidates as export_candidates
from scripts.ops import run_daily_strategy_backtest
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
    assert config["primary_strategy"] in set(run_daily_strategy_backtest.DAILY_STRATEGIES.split(","))


def test_strategy_registry_consistent_with_production_config():
    """验证 strategy_cards 与 production_strategy.yaml 一致。"""
    from strategy_registry import load_all_cards

    config = load_production_config()
    cards = load_all_cards()

    # 生产策略卡必须存在
    prod_id = config["primary_selection_strategy"]
    assert prod_id in cards, f"Production strategy '{prod_id}' not in strategy_cards/"
    prod_card = cards[prod_id]
    assert prod_card.is_production, f"'{prod_id}' should be PRODUCTION, is {prod_card.status}"

    # chenyiyun_selected 必须是 LEGACY
    legacy = cards.get("chenyiyun_selected")
    assert legacy is not None, "chenyiyun_selected missing from strategy_cards/"
    assert legacy.status == "LEGACY", f"chenyiyun_selected should be LEGACY, is {legacy.status}"
    assert not legacy.can_generate_orders, "LEGACY strategy must not generate orders"


def test_scheduler_is_archived():
    """scheduler.py 已归档，不应存在于项目根目录。"""
    assert not (PROJECT_ROOT / "scheduler.py").exists(), (
        "scheduler.py should be archived to archive/scheduler.py"
    )
    assert (PROJECT_ROOT / "archive" / "scheduler.py").exists(), (
        "archive/scheduler.py must exist for historical reference"
    )


def test_chenyiyun_selected_archived():
    """旧 Chenyiyun 策略脚本已归档。"""
    for script in [
        "run_chenyiyun_signal_check.py",
        "run_chenyiyun_weekly_rebalance.py",
        "run_chenyiyun_limitup_check.py",
        "run_chenyiyun_position_update.py",
    ]:
        assert not (PROJECT_ROOT / "scripts" / "ops" / script).exists(), (
            f"{script} should be archived to archive/"
        )
        assert (PROJECT_ROOT / "archive" / script).exists(), (
            f"archive/{script} must exist for historical reference"
        )


def test_runbook_no_longer_declares_adaptive_as_primary_strategy():
    path = PROJECT_ROOT / "docs/00_project_overview/RUNBOOK.md"
    content = path.read_text(encoding="utf-8")
    assert "主策略为 `adaptive_market_style`" not in content
