from types import SimpleNamespace

from scripts.research.run_strict_reliability_matrix import cells, run


def test_matrix_has_all_real_parameter_cells(tmp_path):
    assert len(list(cells())) == 216
    result = run(SimpleNamespace(output_dir=tmp_path, corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=True))
    assert result["run_count"] == 72
    assert result["strategy_cell_count"] == 216
    assert result["reliability_pass"] is False
    assert all(run["status"] == "DRY_RUN" for run in result["runs"])
    assert all("--strategies" in run["command"] and run["command"][run["command"].index("--strategies") + 1].count(",") == 2 for run in result["runs"])


def test_matrix_preflight_limits_runs_not_strategies(tmp_path):
    result = run(SimpleNamespace(output_dir=tmp_path, corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=True, max_runs=8))
    assert result["run_count"] == 8
    assert result["strategy_cell_count"] == 24
