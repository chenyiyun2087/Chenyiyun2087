import json
from pathlib import Path

from scripts.ops import runtime_preflight
from scripts import run_bs_signal_enhancement_cycle as cycle
from web import app as web_app


def test_pipeline_is_only_scheduling_authority():
    assert web_app.SCHEDULED_TASK_WHITELIST == web_app.PIPELINE_TASK_NAMES
    assert "db_bs_detect" not in web_app.SCHEDULED_TASK_WHITELIST
    assert web_app.TASKS["db_bs_detect"]["schedule_enabled"] is False


def test_all_daily_commands_receive_explicit_business_date():
    options = {"datestr": "20260701"}
    assert web_app._build_task_script_parts("adc_bs_detect", options)[-2:] == ["--date", "20260701"]
    assert web_app._build_task_script_parts("sina_score", options)[-3:] == ["--date", "20260701", "--force"]
    monthly = web_app._build_task_script_parts("bs_signal_monthly_cycle", options)
    assert monthly[-2:] == ["--date", "2026-07-01"]
    assert "--force" not in monthly


def test_retry_only_allows_transient_failures():
    assert web_app._classify_task_failure("Failed", 1, "ModuleNotFoundError: pandas") == ("DEPENDENCY", False)
    assert web_app._classify_task_failure("Failed", 2, "usage: tool --date") == ("ARGUMENT", False)
    assert web_app._classify_task_failure("Failed", 1, "pytest AssertionError") == ("TEST_GATE", False)
    assert web_app._classify_task_failure("Failed", 1, "Deadlock found (1213)") == ("TRANSIENT", True)


def test_runtime_preflight_detects_wrong_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_preflight, "PROJECT_PYTHON", tmp_path / "other-python")
    issues = runtime_preflight.collect_runtime_issues(require_database=False)
    assert any(issue.startswith("wrong_python:") for issue in issues)


def test_model_activation_is_atomic_and_preserves_previous_pointer(monkeypatch, tmp_path):
    monkeypatch.setattr(cycle, "MODEL_ROOT", tmp_path)
    old = {"model_dir": "/models/old", "model_path": "/models/old/model.joblib"}
    (tmp_path / "active_model.json").write_text(json.dumps(old), encoding="utf-8")
    summary = {
        "output_dir": "/models/new",
        "model_path": "/models/new/model.joblib",
        "target": "hit_20_10pct",
        "model_kind": "random_forest",
        "risk_target": None,
        "feature_schema_hash": "abc",
    }

    active_path, previous = cycle._activate_model(summary)

    assert previous == old
    assert json.loads(active_path.read_text(encoding="utf-8"))["model_dir"] == "/models/new"
    assert not list(tmp_path.glob(".active_model.*.tmp"))
