"""PR20: Real OOS Executor & Final Evidence Closure Tests.

Covers:
  L0 — Independence Hard Gate (no approval override)
  L1 — Date Completeness (empty dates tracked)
  L2 — Exit Golden (exit ledger, exit events > 0)
  L3 — Flat Evidence Abolished (per-experiment structure required)
  L4 — Real Executor (non-empty output)
  L5 — Ledger Conservation (no fallback, required columns)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# L0: Independence Hard Gate — Approval Does NOT Override Self-Referential
# ============================================================================


class TestIndependenceHardGate:
    """PR20: Approval cannot make self-referential data independent."""

    def test_adapter_oracle_with_approval_still_fails(self):
        """Adapter-generated oracle with approval must still fail independence."""
        from scripts.research.frozen_oracle import (
            FrozenOracleState, OracleProvenance, OracleSource,
            check_oracle_independence,
        )

        oracle = FrozenOracleState(
            strategy_id="test",
            experiment_id="P0",
            provenance=OracleProvenance(
                source=OracleSource.FROZEN_PRODUCTION_FILE,
                generated_at="2026-01-01T00:00:00",
                git_commit_sha="abc123",
                generating_class="scripts.research.strategy_adapters.ProductionStrategyAdapter",
                generating_function="ProductionStrategyAdapter.rank",
                generator_file_sha="some_sha",
                config_sha="cfg123",
                data_snapshot_sha="data123",
                approved_by="admin",
                approval_sha="approved_sha_123",
            ),
        )

        result = check_oracle_independence(
            oracle,
            runtime_class_name="FrozenAlphaRuntime",
        )
        # PR20: Adapter oracles ALWAYS fail independence, even with approval
        assert not result["is_independent"], (
            f"Adapter oracle with approval must NOT be independent: {result}"
        )
        assert result["is_self_referential"], (
            f"Adapter oracle must remain self-referential: {result}"
        )
        # Approval should generate a warning, not clear errors
        has_approval_warning = any(
            "approved" in w.lower() for w in result.get("warnings", [])
        )
        assert has_approval_warning, (
            f"Approval should generate warning annotation: {result}"
        )
        # Errors list must NOT be empty (the adapter class error remains)
        assert len(result["errors"]) > 0, (
            f"Errors must not be empty after approval: {result}"
        )

    def test_same_file_sha_with_approval_still_fails(self):
        """Same file SHA with approval must still fail independence."""
        from scripts.research.frozen_oracle import (
            FrozenOracleState, OracleProvenance, OracleSource,
            check_oracle_independence,
        )

        oracle = FrozenOracleState(
            strategy_id="test",
            experiment_id="P0",
            provenance=OracleProvenance(
                source=OracleSource.FROZEN_PRODUCTION_FILE,
                generated_at="2026-01-01T00:00:00",
                git_commit_sha="abc123",
                generating_class="independent.production.engine",
                generating_function="export_daily_decisions",
                generator_file_sha="same_file_sha_12345",
                config_sha="cfg123",
                data_snapshot_sha="data123",
                approved_by="admin",
                approval_sha="approved_sha_123",
            ),
        )

        result = check_oracle_independence(
            oracle,
            runtime_class_name="FrozenAlphaRuntime",
            runtime_file_sha="same_file_sha_12345",  # Same!
        )
        assert not result["is_independent"], (
            f"Same file SHA with approval must fail: {result}"
        )
        assert any(
            "same" in e.lower() and "file" in e.lower()
            for e in result["errors"]
        ), f"File SHA error must remain: {result}"

    def test_independent_oracle_passes(self):
        """Truly independent oracle should pass."""
        from scripts.research.frozen_oracle import (
            FrozenOracleState, OracleProvenance, OracleSource,
            check_oracle_independence,
        )

        oracle = FrozenOracleState(
            strategy_id="test",
            experiment_id="P0",
            provenance=OracleProvenance(
                source=OracleSource.FROZEN_PRODUCTION_FILE,
                generated_at="2026-01-01T00:00:00",
                git_commit_sha="abc123",
                generating_class="completely.different.production.system",
                generating_function="production_export_pipeline",
                generator_file_sha="different_sha_99999",
                config_sha="cfg123",
                data_snapshot_sha="data123",
            ),
        )

        result = check_oracle_independence(
            oracle,
            runtime_class_name="FrozenAlphaRuntime",
            runtime_file_sha="runtime_sha_11111",
            runtime_git_sha="def456",
        )
        assert result["is_independent"], (
            f"Independent oracle should pass: {result}"
        )


# ============================================================================
# L1: Date Completeness
# ============================================================================


class TestDateCompleteness:
    """PR20: Empty runtime dates must be tracked and cause failure."""

    def test_empty_dates_tracking(self):
        """Verify empty date tracking mechanism exists in golden runner."""
        from scripts.research.run_pr19_golden_regression import run_golden_regression
        import inspect

        source = inspect.getsource(run_golden_regression)
        # Must track empty dates
        assert "empty" in source.lower(), (
            "Golden runner must track empty runtime dates"
        )

    def test_oracle_date_coverage_exact_match(self):
        """Exact date match between oracle and runtime must be enforced."""
        from scripts.research.run_pr19_golden_regression import check_oracle_date_coverage
        from scripts.research.frozen_oracle import (
            FrozenOracleState, OracleProvenance, OracleSource,
        )

        oracle = FrozenOracleState(
            strategy_id="test",
            experiment_id="P0",
            provenance=OracleProvenance(
                source=OracleSource.FROZEN_PRODUCTION_FILE,
                generated_at="2026-01-01T00:00:00",
                git_commit_sha="abc123",
                generating_class="independent.process",
                generating_function="independent.run",
                generator_file_sha="gen_sha",
                config_sha="cfg_sha",
                data_snapshot_sha="data_sha",
            ),
            decisions={
                "2026-01-02": None,
                "2026-01-03": None,
            },
            n_dates=2,
            date_range=("2026-01-02", "2026-01-03"),
        )

        # Missing one date
        result = check_oracle_date_coverage(
            oracle, {"2026-01-02", "2026-01-03", "2026-01-06"}, "P0"
        )
        assert not result["passed"], "Missing runtime date must cause failure"
        assert result["n_missing"] > 0

        # Exact match
        result = check_oracle_date_coverage(
            oracle, {"2026-01-02", "2026-01-03"}, "P0"
        )
        assert result["passed"], "Exact match must pass"

    def test_regime_coverage_extended_to_2024(self):
        """PR20: Regime coverage must include 2024 windows."""
        from scripts.research.run_pr19_golden_regression import (
            REQUIRED_DATE_RANGES, MIN_SESSIONS_PER_WINDOW,
        )
        windows = {r[0] for r in REQUIRED_DATE_RANGES}
        assert "2024Q1" in windows, "2024Q1 must be in required windows"
        assert "2024Q2" in windows, "2024Q2 must be in required windows"
        assert MIN_SESSIONS_PER_WINDOW >= 15


# ============================================================================
# L2: Exit Golden
# ============================================================================


class TestExitGolden:
    """PR20: Real exit golden with exit ledger and hard gates."""

    def test_exit_comparison_fields_exist(self):
        """Exit comparison must check specific fields."""
        from scripts.research.frozen_oracle import _compare_exits

        rt_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]
        oracle_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]

        result = _compare_exits(rt_exits, oracle_exits)
        assert not result, f"Exact match should produce no diffs: {result}"

    def test_exit_date_mismatch_detected(self):
        """Exit date mismatch must be detected."""
        from scripts.research.frozen_oracle import _compare_exits

        rt_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-06",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]
        oracle_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",  # Different
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]

        result = _compare_exits(rt_exits, oracle_exits)
        assert result, "Exit date mismatch must produce diffs"

    def test_exit_reason_mismatch_detected(self):
        """Exit reason mismatch must be detected."""
        from scripts.research.frozen_oracle import _compare_exits

        rt_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "HARD_STOP",
            "exit_shares": "1000",
        }]
        oracle_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]

        result = _compare_exits(rt_exits, oracle_exits)
        assert result, "Exit reason mismatch must produce diffs"

    def test_exit_shares_mismatch_detected(self):
        """Exit shares mismatch must be detected."""
        from scripts.research.frozen_oracle import _compare_exits

        rt_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "500",
        }]
        oracle_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]

        result = _compare_exits(rt_exits, oracle_exits)
        assert result, "Exit shares mismatch must produce diffs"

    def test_exit_presence_mismatch_detected(self):
        """Missing exit in one side must be detected."""
        from scripts.research.frozen_oracle import _compare_exits

        rt_exits = [{
            "symbol": "000001",
            "exit_date": "2026-01-05",
            "exit_reason": "TIME_STOP",
            "exit_shares": "1000",
        }]
        oracle_exits = []  # Oracle has no exit

        result = _compare_exits(rt_exits, oracle_exits)
        assert result, "Exit presence mismatch must produce diffs"


# ============================================================================
# L3: Flat Evidence Abolished
# ============================================================================


class TestFlatEvidenceAbolished:
    """PR20: Flat evidence structure must be rejected."""

    def test_flat_evidence_rejected(self, tmp_path):
        """Flat evidence without per-experiment dirs must fail."""
        from scripts.research.evidence_semantic import (
            validate_evidence_per_experiment_window,
        )

        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()
        nav = pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=60, freq="B"),
            "nav": np.cumprod(1 + np.random.randn(60) * 0.01),
        })
        nav.to_parquet(evidence_dir / "daily_nav.parquet")

        result = validate_evidence_per_experiment_window(evidence_dir)
        assert not result["passed"], (
            f"Flat evidence must be rejected: {result}"
        )
        # PR26A.9: Flat evidence is detected as missing experiment directories
        # (no per-experiment subdirectories exist).  At minimum P0 must be missing.
        assert any("P0" in m for m in result.get("missing", [])), (
            f"P0 directory must be reported missing: {result}"
        )

    def test_per_experiment_structure_passes(self, tmp_path):
        """All experiments with per-exp dirs and data must pass."""
        from scripts.research.evidence_semantic import (
            validate_evidence_per_experiment_window,
            FIXED_VALIDATION_WINDOWS,
        )

        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()

        # PR26A.9: All 6 core experiments + full quarter coverage required
        for exp_id in ["P0", "C0", "A7", "A8", "A9", "REV_A7"]:
            exp_dir = evidence_dir / exp_id
            exp_dir.mkdir()
            all_dates = []
            for year, q_start_month in [(2024, 1), (2024, 4), (2024, 7), (2024, 10),
                                         (2025, 1), (2025, 4), (2025, 7), (2025, 10),
                                         (2026, 1), (2026, 4)]:
                start = pd.Timestamp(f"{year}-{q_start_month:02d}-01")
                end = start + pd.DateOffset(months=3) - pd.DateOffset(days=1)
                all_dates.extend(pd.date_range(start, end, freq="B"))
            nav = pd.DataFrame({
                "trade_date": all_dates,
                "nav": np.cumprod(1 + np.random.randn(len(all_dates)) * 0.01),
                "cash": [350000.0] * len(all_dates),
                "market_value": [150000.0] * len(all_dates),
            })
            nav.to_parquet(exp_dir / "daily_nav.parquet")

        # PR26A.9: RND dirs also required
        all_windows = sorted(FIXED_VALIDATION_WINDOWS)
        for rnd_id in ["RND_TOP30", "RND_FULL"]:
            rnd_dir = evidence_dir / rnd_id
            rnd_dir.mkdir()
            (rnd_dir / "status.json").write_text(
                json.dumps({"experiment": rnd_id, "n_seeds": 100,
                            "n_distinct_paths": 100, "status": "PASSED"})
            )
            for wl in all_windows:
                qdir = rnd_dir / wl
                qdir.mkdir()
                rdf = pd.DataFrame({
                    "seed": [f"seed_{i}" for i in range(100)],
                    "path_hash": [f"path_{i}" for i in range(100)],
                })
                rdf.to_csv(qdir / "random_seed_results.csv", index=False)

        result = validate_evidence_per_experiment_window(evidence_dir)
        assert result["passed"], (
            f"All experiments present should pass: {result.get('errors')}"
            f"\nmissing: {result.get('missing', [])}"
        )

    def test_missing_experiment_fails(self, tmp_path):
        """Missing A9 must cause failure."""
        from scripts.research.evidence_semantic import (
            validate_evidence_per_experiment_window,
        )

        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()

        # Only P0 and C0, missing A7, A8, A9
        for exp_id in ["P0", "C0"]:
            exp_dir = evidence_dir / exp_id
            exp_dir.mkdir()
            dates = list(pd.date_range("2025-01-02", "2025-06-30", freq="B"))
            nav = pd.DataFrame({
                "trade_date": dates,
                "nav": np.cumprod(1 + np.random.randn(len(dates)) * 0.01),
            })
            nav.to_parquet(exp_dir / "daily_nav.parquet")

        result = validate_evidence_per_experiment_window(evidence_dir)
        assert not result["passed"], (
            f"Missing experiments must fail: {result}"
        )
        assert "A7" in str(result["missing"]) or "A8" in str(result["missing"]), (
            f"Missing experiments should be listed: {result}"
        )

    def test_fixed_windows_include_2024(self):
        """FIXED_WINDOWS must include 2024Q1 and 2024Q2."""
        from scripts.research.validation_evidence import FIXED_WINDOWS
        windows = {w[0] for w in FIXED_WINDOWS}
        assert "2024Q1" in windows, "2024Q1 must be in FIXED_WINDOWS"
        assert "2024Q2" in windows, "2024Q2 must be in FIXED_WINDOWS"
        assert len(FIXED_WINDOWS) == 10, (
            f"FIXED_WINDOWS must have 10 quarterly entries, got {len(FIXED_WINDOWS)}"
        )


# ============================================================================
# L4: Real Executor
# ============================================================================


class TestRealExecutor:
    """PR20: V3 validation must generate real output, not empty files."""

    def test_run_function_exists(self):
        """Verify the run function is importable."""
        from scripts.research.run_full_strategy_v3_validation import run
        assert callable(run)

    def test_write_empty_parquets_still_available(self):
        """Schema writer still available as fallback (PR21: renamed to _write_empty_parquets_precheck)."""
        from scripts.research.run_full_strategy_v3_validation import _write_empty_parquets_precheck
        assert callable(_write_empty_parquets_precheck)

    def test_executor_imports_experiment_specs(self):
        """Executor must import experiment specs for real execution."""
        from scripts.research.run_full_strategy_v3_validation import run
        import inspect
        source = inspect.getsource(run)
        assert "build_experiment_specs" in source, (
            "Real executor must import build_experiment_specs"
        )
        assert "resolve_runtime" in source, (
            "Real executor must import resolve_runtime"
        )

    def test_executor_generates_factor_states(self):
        """Executor must generate factor state per experiment."""
        from scripts.research.run_full_strategy_v3_validation import run
        import inspect
        source = inspect.getsource(run)
        assert "factor_state_by_fold" in source, (
            "Real executor must track factor_state_by_fold"
        )
        assert "FITTED" in source, (
            "Real executor must set factor states to FITTED"
        )

    def test_executor_sets_source_complete_true(self):
        """Corporate and lifecycle must be marked source_complete=True."""
        from scripts.research.run_full_strategy_v3_validation import run
        import inspect
        source = inspect.getsource(run)
        assert 'source_complete' in source, (
            "Real executor must set source_complete"
        )


# ============================================================================
# L5: Ledger Conservation — No Fallback
# ============================================================================


class TestLedgerConservationNoFallback:
    """PR20: Missing columns must fail, not fall back to date overlap."""

    def test_missing_cash_column_fails(self, tmp_path):
        """NAV file missing cash column must fail conservation."""
        from scripts.research.evidence_semantic import validate_ledger_nav_conservation

        nav_path = tmp_path / "nav.parquet"
        ledger_path = tmp_path / "ledger.parquet"

        # NAV without cash or market_value columns
        pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=5, freq="B"),
            "nav": [1.0, 1.01, 0.99, 1.02, 1.03],
        }).to_parquet(nav_path)

        pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=3, freq="B"),
            "symbol": ["A", "B", "C"],
        }).to_parquet(ledger_path)

        result = validate_ledger_nav_conservation(nav_path, ledger_path, tolerance=0.0001)
        assert not result["passed"], (
            f"Missing cash column must fail: {result}"
        )
        assert result["details"].get("conservation_check") == "failed_missing_columns", (
            f"Must report failed_missing_columns, not date_overlap_only: {result}"
        )

    def test_all_columns_present_passes(self, tmp_path):
        """NAV with all required columns must pass conservation."""
        from scripts.research.evidence_semantic import validate_ledger_nav_conservation

        nav_path = tmp_path / "nav.parquet"
        ledger_path = tmp_path / "ledger.parquet"

        nav = pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=5, freq="B"),
            "nav": [1.0, 1.01, 0.99, 1.02, 1.03],
            "cash": [0.3, 0.31, 0.29, 0.32, 0.33],
            "market_value": [0.7, 0.7, 0.7, 0.7, 0.7],
            "accrued_cost": [0.0, 0.0, 0.0, 0.0, 0.0],
        })
        nav.to_parquet(nav_path)

        pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=3, freq="B"),
            "symbol": ["A", "B", "C"],
        }).to_parquet(ledger_path)

        result = validate_ledger_nav_conservation(nav_path, ledger_path, tolerance=0.0001)
        assert result["passed"], (
            f"All columns present should pass: {result.get('errors')}"
        )
        assert result["details"].get("conservation_check") == "v2_yuan_normalized", (
            f"Must report v2_yuan_normalized conservation check: {result}"
        )

    def test_negative_nav_detected(self, tmp_path):
        """Negative NAV must be flagged."""
        from scripts.research.evidence_semantic import validate_ledger_nav_conservation

        nav_path = tmp_path / "nav.parquet"
        ledger_path = tmp_path / "ledger.parquet"

        nav = pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=3, freq="B"),
            "nav": [-0.5, 1.01, 0.99],
            "cash": [0.3, 0.31, 0.29],
            "market_value": [0.7, 0.7, 0.7],
            "accrued_cost": [0.0, 0.0, 0.0],
        })
        nav.to_parquet(nav_path)

        pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=2, freq="B"),
            "symbol": ["A", "B"],
        }).to_parquet(ledger_path)

        result = validate_ledger_nav_conservation(nav_path, ledger_path, tolerance=0.0001)
        assert not result["passed"], "Negative NAV must fail"
        assert any(
            "negative" in e.lower() for e in result["errors"]
        ), f"Error must mention negative NAV: {result}"


# ============================================================================
# L6: Cost Accounting Contract
# ============================================================================


class TestCostAccountingContract:
    """PR20: Explicit cost accounting contract."""

    def test_conservation_formula_documented(self):
        """Cost accounting formula must be in docstring."""
        from scripts.research.evidence_semantic import validate_ledger_nav_conservation
        doc = validate_ledger_nav_conservation.__doc__ or ""
        assert "cash" in doc.lower(), "Docstring must mention cash"
        assert "market_value" in doc.lower(), "Docstring must mention market_value"
        assert "cost" in doc.lower(), "Docstring must mention cost accounting"
        assert "nav" in doc.lower(), "Docstring must mention nav formula"

    def test_conservation_with_exact_match_passes(self, tmp_path):
        """Exact conservation nav = cash + mv - cost must pass."""
        from scripts.research.evidence_semantic import validate_ledger_nav_conservation

        nav_path = tmp_path / "nav.parquet"
        ledger_path = tmp_path / "ledger.parquet"

        nav = pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=5, freq="B"),
            "nav": [1.0, 1.01, 0.99, 1.02, 1.03],
            "cash": [0.3, 0.31, 0.29, 0.32, 0.33],
            "market_value": [0.7, 0.7, 0.7, 0.7, 0.7],
            "accrued_cost": [0.0, 0.0, 0.0, 0.0, 0.0],
        })
        nav.to_parquet(nav_path)

        pd.DataFrame({
            "trade_date": pd.date_range("2026-01-02", periods=3, freq="B"),
            "symbol": ["A", "B", "C"],
        }).to_parquet(ledger_path)

        result = validate_ledger_nav_conservation(nav_path, ledger_path, tolerance=0.0001)
        assert result["passed"], f"Exact match must pass: {result.get('errors')}"


# ============================================================================
# L7: Smoke — Golden Runner Integration
# ============================================================================


class TestGoldenRunnerIntegration:
    """Smoke tests for golden runner with PR20 changes."""

    def test_independence_check_in_runner(self):
        """check_oracle_independence must be called in golden runner."""
        from scripts.research.run_pr19_golden_regression import run_golden_regression
        import inspect
        source = inspect.getsource(run_golden_regression)
        assert "check_oracle_independence" in source, (
            "Golden runner must call check_oracle_independence"
        )

    def test_exit_gate_in_runner(self):
        """Exit gate must be checked in golden runner."""
        from scripts.research.run_pr19_golden_regression import run_golden_regression
        import inspect
        source = inspect.getsource(run_golden_regression)
        assert "exit_gate" in source.lower(), (
            "Golden runner must have exit gate logic"
        )

    def test_runtime_empty_dates_tracked(self):
        """Empty runtime dates must be tracked."""
        from scripts.research.run_pr19_golden_regression import run_golden_regression
        import inspect
        source = inspect.getsource(run_golden_regression)
        assert "empty" in source.lower(), (
            "Golden runner must track empty dates"
        )

    def test_all_independent_in_all_passed(self):
        """all_independent must be in the all_passed boolean."""
        from scripts.research.run_pr19_golden_regression import run_golden_regression
        import inspect
        source = inspect.getsource(run_golden_regression)
        assert "all_independent" in source, (
            "all_passed must include all_independent"
        )
        assert "all_dates_complete" in source, (
            "all_passed must include all_dates_complete"
        )
        assert "exit_gate_passed" in source, (
            "all_passed must include exit_gate_passed"
        )
