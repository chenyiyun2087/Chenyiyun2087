from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_derived_mysql_metrics():
    audit = _load("audit_mysql_performance", "scripts/maintenance/audit_mysql_performance.py")
    status = [
        {"variable_name": "Innodb_buffer_pool_reads", "value": "10"},
        {"variable_name": "Innodb_buffer_pool_read_requests", "value": "1000"},
        {"variable_name": "Created_tmp_tables", "value": "100"},
        {"variable_name": "Created_tmp_disk_tables", "value": "5"},
    ]
    metrics = audit.derived_metrics(status)
    assert metrics["buffer_pool_hit_ratio"] == 0.99
    assert metrics["disk_tmp_table_ratio"] == 0.05


def test_index_candidates_are_invisible_first_and_bounded():
    indexes = _load("manage_mysql_indexes", "scripts/maintenance/manage_mysql_indexes.py")
    assert indexes.CANDIDATES["bs_batch_stock"].columns == "`batch_date`, `stock_code`"
    assert "4.3M-row" in indexes.CANDIDATES["score_candidate_default"].risk


def test_runtime_tuning_allowlist_has_safe_bounds():
    tuning = _load("tune_mysql_runtime", "scripts/maintenance/tune_mysql_runtime.py")
    assert tuning.LIMITS["innodb_redo_log_capacity"] == (1024**3, 4 * 1024**3)
    assert tuning.LIMITS["innodb_buffer_pool_size"][1] == 8 * 1024**3


def test_web_benchmark_percentile_is_deterministic():
    benchmark = _load("benchmark_web_endpoints", "scripts/maintenance/benchmark_web_endpoints.py")
    assert benchmark.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50) == 3.0
    assert benchmark.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


def test_web_benchmark_comparison_enforces_p95_budget():
    benchmark = _load("benchmark_web_endpoints_compare", "scripts/maintenance/benchmark_web_endpoints.py")
    current = [{"endpoint": "/scores", "p95_ms": 111.0}]
    baseline = [{"endpoint": "/scores", "p95_ms": 100.0}]

    result = benchmark.compare_with_baseline(
        current,
        baseline,
        max_regression_pct=10.0,
    )

    assert result["passed"] is False
    assert result["comparisons"][0]["delta_pct"] == 11.0
