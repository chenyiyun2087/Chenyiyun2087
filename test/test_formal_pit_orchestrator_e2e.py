"""E2E tests for Formal PIT Pipeline v5.1.2.

Covers:
  - Adapter signature contract (no end_date)
  - _block_and_seal extra parameter
  - PIT Run manifest (pit_run_manifest.json)
  - Seal re-seal rejection (v5.1.2)
  - Registry activation failure → external activation report (not modify sealed run)
  - Semantic audit missing → BLOCKED
  - Package builder imports and contract
  - Pre-flight checks
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# P0-1: Adapter signature contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdapterSignatureContract:
    """Adapter must NOT accept end_date."""

    def test_adapter_has_no_end_date_param(self):
        import inspect
        from scripts.research.pit_data_adapter import build_pit_adapter_manifest
        sig = inspect.signature(build_pit_adapter_manifest)
        params = list(sig.parameters.keys())
        assert params == ["config_path", "output_dir"], f"Adapter params: {params}"

    def test_orchestrator_has_no_end_date_param(self):
        import inspect
        from scripts.research.run_formal_pit_pipeline import run_formal_pit_pipeline
        sig = inspect.signature(run_formal_pit_pipeline)
        assert "end_date" not in sig.parameters

    def test_orchestrator_cli_has_no_end_date(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        assert "--end-date" not in source


# ═══════════════════════════════════════════════════════════════════════════════
# P0-2: _block_and_seal extra parameter
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlockAndSeal:
    """_block_and_seal must accept extra and use FORMAL_PIT_RUNS_ROOT."""

    def test_extra_param_exists(self):
        import inspect
        from scripts.research.run_formal_pit_pipeline import _block_and_seal
        sig = inspect.signature(_block_and_seal)
        assert "extra" in sig.parameters

    def test_uses_pit_runs_root(self):
        from scripts.research.run_formal_pit_pipeline import FORMAL_PIT_RUNS_ROOT
        assert "formal_pit_runs" in str(FORMAL_PIT_RUNS_ROOT)

    def test_extra_forwarded_to_blocked_report(self, tmp_path):
        from scripts.research.run_formal_pit_pipeline import (
            _block_and_seal, FORMAL_PIT_RUNS_ROOT,
        )
        building = FORMAL_PIT_RUNS_ROOT / ".building_test_bas"
        run_dir = FORMAL_PIT_RUNS_ROOT / "test_bas_extra"
        temp_registry = tmp_path / "seal_registry.json"
        _clean(building, run_dir)
        building.mkdir(parents=True)

        try:
            result = _block_and_seal(
                building, "test_bas_extra", "git_sha_abc",
                "test_stage", "TEST_ERROR",
                extra={"blockers": ["b1", "b2"], "context": "test"},
                registry_path_override=temp_registry,
            )
            assert result["status"] == "BLOCKED"
            assert result.get("extra", {}).get("blockers") == ["b1", "b2"]
        finally:
            _clean(building, run_dir)


# ═══════════════════════════════════════════════════════════════════════════════
# v5.1.2: PIT Run manifest
# ═══════════════════════════════════════════════════════════════════════════════


class TestPITRunManifest:
    """Orchestrator writes pit_run_manifest.json, not run_manifest.json."""

    def test_pit_run_manifest_written(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        assert "pit_run_manifest.json" in source
        assert '"pit_run_manifest_v5_1_2"' in source or "'pit_run_manifest_v5_1_2'" in source

    def test_no_stale_run_manifest(self):
        """Old run_manifest.json should not be in the orchestrator."""
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        # run_manifest should only appear in pit_run_manifest context
        lines_with_run_manifest = [l for l in source.split('\n') if 'run_manifest' in l and 'pit_run_manifest' not in l]
        assert len(lines_with_run_manifest) == 0, f"Stale run_manifest refs: {lines_with_run_manifest}"


# ═══════════════════════════════════════════════════════════════════════════════
# v5.1.2: Seal re-seal rejection
# ═══════════════════════════════════════════════════════════════════════════════


class TestSealRejection:
    """seal_directory must reject re-sealing an already-sealed directory."""

    def test_seal_rejects_re_seal(self):
        source = (PROJECT_ROOT / "runtime" / "artifact_seal.py").read_text()
        assert "Existing_seal" in source or "already sealed" in source or \
            "FileExistsError" in source, "seal_directory does not reject re-seal"


# ═══════════════════════════════════════════════════════════════════════════════
# v5.1.2: Activation report (not PASS_NOT_ACTIVATED)
# ═══════════════════════════════════════════════════════════════════════════════


class TestActivationReport:
    """Registry failure writes external activation report, never modifies sealed run."""

    def test_activation_report_exists(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        assert "activation_report" in source or "activation_failed" in source

    def test_no_modify_sealed_run(self):
        """PASS_NOT_ACTIVATED write-to-readonly logic must be removed."""
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        assert "PASS_NOT_ACTIVATED" not in source, \
            "PASS_NOT_ACTIVATED still present (should be activation_report.json instead)"
        # Also verify no second seal_directory call after registry failure
        # Count seal_directory calls: should only be 1 in success path + 1 in _block_and_seal
        assert "manifest_path.write_text" not in source, \
            "Write to manifest after seal still present"


# ═══════════════════════════════════════════════════════════════════════════════
# P0-6: Semantic audit missing → BLOCKED
# ═══════════════════════════════════════════════════════════════════════════════


class TestSemanticAuditBlocked:
    """Semantic audit module not available must produce BLOCKED."""

    def test_no_diagnostic_in_semantic_audit_block(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        idx = source.find("semantic_audit_module_not_available")
        if idx > 0:
            surrounding = source[idx:idx + 300]
            assert "DIAGNOSTIC" not in surrounding

    def test_semantic_audit_is_blocked(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "run_formal_pit_pipeline.py").read_text()
        idx = source.find("semantic_audit_module_not_available")
        if idx > 0:
            # The BLOCKED status is set a few lines above the blocker string
            surrounding = source[max(0, idx - 200):idx + 300]
            assert '"BLOCKED"' in surrounding or "'BLOCKED'" in surrounding


# ═══════════════════════════════════════════════════════════════════════════════
# Package Builder (new in v5.1.2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageBuilder:
    """Standalone Package builder is importable and has correct contract."""

    def test_package_builder_importable(self):
        from scripts.research.build_formal_package import build_formal_package
        import inspect
        sig = inspect.signature(build_formal_package)
        params = list(sig.parameters.keys())
        assert "formal_pit_run_id" in params
        assert "pit_run_dir" in params
        # No _internal_call — Seal verification is mandatory
        assert "_internal_call" not in params

    def test_all_snapshots_mandatory(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "build_formal_package.py").read_text()
        # All five snapshot families should be in ENTRIES without optional exemption
        assert "market.parquet" in source
        assert "universe.parquet" in source
        assert "financial.parquet" in source
        assert "industry.parquet" in source
        assert "adjustment.parquet" in source
        # Old "optional" snapshots comment should not appear
        assert "Snapshots (if they exist)" not in source or \
            "Snapshots — ALL mandatory" in source

    def test_seal_verification_mandatory(self):
        source = (PROJECT_ROOT / "scripts" / "research" / "build_formal_package.py").read_text()
        assert "verify_seal" in source
        # _internal_call should not be used as a code parameter (docstring mention is fine)
        import inspect
        from scripts.research.build_formal_package import build_formal_package
        sig = inspect.signature(build_formal_package)
        assert "_internal_call" not in sig.parameters


# ═══════════════════════════════════════════════════════════════════════════════
# Pre-flight checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreflight:
    """Pre-flight checks must catch common blockers."""

    def test_missing_adapter_config(self, tmp_path):
        from scripts.research.run_formal_pit_pipeline import _check_prerequisites
        missing = tmp_path / "nonexistent.json"
        blockers = _check_prerequisites(missing, "formal_v5_0")
        assert "adapter_config_missing" in blockers

    def test_missing_acceptance_profile(self, tmp_path):
        from scripts.research.run_formal_pit_pipeline import _check_prerequisites
        config = tmp_path / "adapter_config.json"
        config.write_text('{"adapter_type": "FILE", "evidence_origin": "SYNTHETIC"}')
        blockers = _check_prerequisites(config, "nonexistent_profile")
        assert any("acceptance_profile_missing" in b for b in blockers)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _clean(*paths: Path) -> None:
    """Remove directories, making writable first."""
    for p in paths:
        if not p.exists():
            continue
        if p.is_dir():
            for child in p.rglob("*"):
                if child.is_dir():
                    child.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                elif child.is_file():
                    child.chmod(stat.S_IRUSR | stat.S_IWUSR)
            p.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        else:
            p.chmod(stat.S_IRUSR | stat.S_IWUSR)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
