from types import SimpleNamespace

from scripts.research.run_strict_reliability_matrix import cells, run


def test_matrix_has_all_real_parameter_cells(tmp_path):
    assert len(list(cells())) == 216
    result = run(SimpleNamespace(output_dir=tmp_path, corporate_action_snapshot="a.csv", corporate_action_manifest="a.json", security_lifecycle_snapshot="l.csv", security_lifecycle_manifest="l.json", dry_run=True))
    assert result["cell_count"] == 216
    assert result["reliability_pass"] is False
    assert all(cell["status"] == "DRY_RUN" for cell in result["cells"])
