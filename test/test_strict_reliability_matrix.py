from types import SimpleNamespace

import pandas as pd

from scripts.research.run_strict_reliability_matrix import cells, run


def test_matrix_has_all_real_parameter_cells(tmp_path):
    assert len(list(cells())) == 216
    result = run(SimpleNamespace(output_dir=tmp_path, corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=True))
    assert result["run_count"] == 72
    assert result["strategy_cell_count"] == 216
    assert result["reliability_pass"] is False
    assert result["promotion_enabled"] is False
    assert all(run["status"] == "DRY_RUN" for run in result["runs"])
    assert all("--strategies" in run["command"] and run["command"][run["command"].index("--strategies") + 1].count(",") == 2 for run in result["runs"])


def test_matrix_preflight_limits_runs_not_strategies(tmp_path):
    result = run(SimpleNamespace(output_dir=tmp_path, corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=True, max_runs=8))
    assert result["run_count"] == 8
    assert result["strategy_cell_count"] == 24


def test_preflight24_is_eight_grouped_runs_with_isolated_output_paths(tmp_path):
    calendar = pd.date_range("2025-03-01", "2026-06-18", freq="B")
    lifecycle = tmp_path / "lifecycle.csv"
    pd.DataFrame({"trade_date": calendar, "symbol": "000001", "is_listed": 1, "is_suspended": 0}).to_csv(lifecycle, index=False)
    result = run(SimpleNamespace(output_dir=tmp_path / "matrix", corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot=lifecycle, security_lifecycle_manifest="l.json", dry_run=True, profile="preflight24", preflight_sessions=60))
    assert result["profile"] == "preflight24"
    assert result["run_count"] == 8
    assert result["strategy_cell_count"] == 24
    assert {run["cap_profile"] for run in result["runs"]} == {"no_cap", "strict_cap"}
    assert {(run["trade_cost_rate"], run["additional_open_slippage_bps"]) for run in result["runs"]} == {(.00075, 0), (.0015, 25)}
    assert len({run["command"][-1] for run in result["runs"]}) == 8
    assert all(run["command"][-2] == "--output-dir" for run in result["runs"])
