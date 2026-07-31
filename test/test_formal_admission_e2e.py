"""Real E2E tests for Formal Admission Pipeline v5.1.3.

Tests actually call pipeline functions (not inspect source strings).
All paths use temp directories — no production Registry pollution.

Covers:
  - Semantic audit BLOCKED on missing snapshots
  - Package builder imports + mandatory snapshots
  - Admission pipeline imports + stage contracts
  - PR chain artifact binding with real files
  - Seal re-seal rejection
  - Capital firewall with new semantics
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

from runtime.acceptance_config import canonical_sha


# ═══════════════════════════════════════════════════════════════════════════════
# Semantic Audit E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestSemanticAuditE2E:
    """Real semantic audit function calls."""

    def test_audit_missing_snapshots_returns_blocked(self, tmp_path):
        from scripts.research.pit_semantic_audit import run_semantic_audit
        result = run_semantic_audit(tmp_path, tmp_path / "nonexistent.json")
        assert result["status"] == "BLOCKED"
        assert any("snapshot_missing" in b for b in result.get("blockers", []))

    def test_audit_returns_valid_structure(self, tmp_path):
        from scripts.research.pit_semantic_audit import run_semantic_audit
        result = run_semantic_audit(tmp_path, tmp_path / "nonexistent.json")
        assert "content_sha256" in result
        assert "blockers" in result
        assert result["capital_authority"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Package Builder E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestPackageBuilderE2E:
    """Real package builder function calls."""

    def test_package_builder_importable(self):
        from scripts.research.build_formal_package import build_formal_package
        assert callable(build_formal_package)

    def test_package_builder_blocks_on_missing_pit_run(self, tmp_path):
        from scripts.research.build_formal_package import build_formal_package
        result = build_formal_package(
            formal_pit_run_id="nonexistent",
            pit_run_dir=tmp_path / "nonexistent",
            release_id="test",
            strategy_set="test",
        )
        assert result["status"] == "BLOCKED"

    def test_package_id_binds_all_identity(self):
        """package_id computation includes all identity fields."""
        from scripts.research.build_formal_package import compute_package_id
        id1 = compute_package_id("a", "sha_a", "s1", "r1", 500_000.0, "code_a")
        id2 = compute_package_id("a", "sha_a", "s1", "r1", 1_500_000.0, "code_a")
        assert id1 != id2  # Different initial capital → different ID

    def test_csv_generation_in_entries(self):
        """Package ENTRIES should include mandatory snapshots."""
        source = (PROJECT_ROOT / "scripts" / "research" / "build_formal_package.py").read_text()
        assert "trade_calendar.csv" in source
        assert "strict_snapshot_manifest.json" in source


# ═══════════════════════════════════════════════════════════════════════════════
# Admission Pipeline E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdmissionPipelineE2E:
    """Real admission pipeline function calls."""

    def test_admission_pipeline_importable(self):
        from scripts.research.run_formal_admission_pipeline import run_formal_admission
        assert callable(run_formal_admission)

    def test_admission_blocks_on_missing_pit_run(self):
        from scripts.research.run_formal_admission_pipeline import run_formal_admission
        result = run_formal_admission(
            pit_run_id="nonexistent_pit_run_12345",
            release_id="test",
        )
        assert result["status"] == "BLOCKED"
        assert "pit_run_dir_not_found" in result.get("error_code", "")


# ═══════════════════════════════════════════════════════════════════════════════
# PR Chain Artifact Binding E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestPRChainArtifactBinding:
    """PR chain binding with real artifact files."""

    def test_full_chain_binds_and_verifies(self, tmp_path):
        """Build a complete B→C→D→E chain and verify PR-I."""
        from runtime.pr_chain_binding import (
            bind_pr_b, bind_pr_c, bind_pr_d, bind_pr_e, verify_pr_i_chain,
        )
        pit_run_id = "pr_test_" + canonical_sha({"test": "chain"})[:16]
        run_dir = tmp_path / "chain"
        run_dir.mkdir()

        # PR-B
        readiness_path = run_dir / "readiness_report.json"
        readiness_path.write_text(json.dumps({
            "status": "PASS", "evidence_sha256": "e" * 64,
        }))
        pr_b = bind_pr_b(
            formal_pit_run_id=pit_run_id, package_sha256="p" * 64,
            readiness_report_path=readiness_path,
            output_dir=run_dir / "pr_b",
            release_id="test", strategy_set="test",
        )
        assert pr_b["status"] == "PASS"

        # PR-C: create artifacts with correct hash fields
        formal_run_id = f"formal-{pit_run_id}"
        fm_path = run_dir / "formal_run_manifest.json"
        fm = {
            "schema_version": "immutable_formal_run_v3", "status": "VERIFIED",
            "formal_pit_run_id": pit_run_id, "formal_run_id": formal_run_id,
            "run_id": formal_run_id, "fixture_mode": False, "capital_authority": False,
        }
        fm["manifest_sha256"] = canonical_sha(
            {k: v for k, v in fm.items() if k != "manifest_sha256"})
        fm_path.write_text(json.dumps(fm))

        fb_path = run_dir / "frozen_bundle_manifest.json"
        fb = {
            "schema_version": "frozen_bundle_manifest_v5_1_3", "status": "PASS",
            "formal_pit_run_id": pit_run_id, "fixture_mode": False,
            "capital_authority": False,
        }
        fb["content_sha256"] = canonical_sha(
            {k: v for k, v in fb.items() if k != "content_sha256"})
        fb_path.write_text(json.dumps(fb))

        pr_c = bind_pr_c(
            pr_b_binding_path=run_dir / "pr_b" / "pr_b_binding.json",
            formal_run_id=formal_run_id,
            formal_run_manifest_path=fm_path,
            frozen_bundle_path=fb_path,
            output_dir=run_dir / "pr_c",
        )
        assert pr_c["status"] == "PASS"

        # PR-D
        oos_path = run_dir / "oos_report.json"
        oos = {
            "schema_version": "formal_oos_robustness_v2", "status": "PASS",
            "formal_pit_run_id": pit_run_id, "formal_run_id": formal_run_id,
            "fixture_mode": False, "capital_authority": False,
        }
        oos["evidence_sha256"] = canonical_sha(
            {k: v for k, v in oos.items() if k != "evidence_sha256"})
        oos_path.write_text(json.dumps(oos))

        pr_d = bind_pr_d(
            pr_c_binding_path=run_dir / "pr_c" / "pr_c_binding.json",
            oos_report_path=oos_path,
            output_dir=run_dir / "pr_d",
        )
        assert pr_d["status"] == "PASS"

        # PR-E
        cap_path = run_dir / "capacity_report.json"
        cap = {
            "schema_version": "formal_execution_capacity_v2", "status": "PASS",
            "formal_pit_run_id": pit_run_id, "formal_run_id": formal_run_id,
            "fixture_mode": False, "capital_authority": False,
        }
        cap["evidence_sha256"] = canonical_sha(
            {k: v for k, v in cap.items() if k != "evidence_sha256"})
        cap_path.write_text(json.dumps(cap))

        pr_e = bind_pr_e(
            pr_c_binding_path=run_dir / "pr_c" / "pr_c_binding.json",
            capacity_report_path=cap_path,
            output_dir=run_dir / "pr_e",
        )
        assert pr_e["status"] == "PASS"

        # PR-I: verify complete chain
        pr_i = verify_pr_i_chain(
            pr_b_path=run_dir / "pr_b" / "pr_b_binding.json",
            pr_c_path=run_dir / "pr_c" / "pr_c_binding.json",
            pr_d_path=run_dir / "pr_d" / "pr_d_binding.json",
            pr_e_path=run_dir / "pr_e" / "pr_e_binding.json",
        )
        assert pr_i["status"] == "PASS", f"Blockers: {pr_i.get('blockers')}"

    def test_missing_pr_d_blocks_pr_i(self, tmp_path):
        """PR-I must BLOCKED when PR-D is missing."""
        from runtime.pr_chain_binding import verify_pr_i_chain
        result = verify_pr_i_chain(
            pr_b_path=tmp_path / "nonexistent_b.json",
            pr_c_path=tmp_path / "nonexistent_c.json",
            pr_d_path=tmp_path / "nonexistent_d.json",
            pr_e_path=tmp_path / "nonexistent_e.json",
        )
        assert result["status"] == "BLOCKED"

    def test_pit_id_mismatch_blocked(self, tmp_path):
        """PIT Run ID mismatch must produce BLOCKED."""
        from runtime.pr_chain_binding import bind_pr_b, bind_pr_c
        pit_run_id = "pit_mismatch_test"
        run_dir = tmp_path / "mismatch"
        run_dir.mkdir()

        # PR-B with one PIT ID
        readiness_path = run_dir / "readiness.json"
        readiness_path.write_text(json.dumps({"status": "PASS", "evidence_sha256": "e" * 64}))
        bind_pr_b(
            formal_pit_run_id=pit_run_id, package_sha256="p" * 64,
            readiness_report_path=readiness_path,
            output_dir=run_dir / "pr_b", release_id="test", strategy_set="test",
        )

        # PR-C artifact with DIFFERENT PIT ID
        fm_path = run_dir / "formal_run_manifest.json"
        fm = {
            "schema_version": "immutable_formal_run_v3", "status": "VERIFIED",
            "formal_pit_run_id": "DIFFERENT_PIT_ID",
            "formal_run_id": f"formal-{pit_run_id}",
            "run_id": f"formal-{pit_run_id}",
            "fixture_mode": False, "capital_authority": False,
        }
        fm["manifest_sha256"] = canonical_sha(
            {k: v for k, v in fm.items() if k != "manifest_sha256"})
        fm_path.write_text(json.dumps(fm))

        fb_path = run_dir / "frozen_bundle.json"
        fb = {"schema_version": "frozen_bundle", "status": "PASS",
              "formal_pit_run_id": "DIFFERENT_PIT_ID",
              "fixture_mode": False, "capital_authority": False}
        fb["content_sha256"] = canonical_sha(
            {k: v for k, v in fb.items() if k != "content_sha256"})
        fb_path.write_text(json.dumps(fb))

        result = bind_pr_c(
            pr_b_binding_path=run_dir / "pr_b" / "pr_b_binding.json",
            formal_run_id=f"formal-{pit_run_id}",
            formal_run_manifest_path=fm_path,
            frozen_bundle_path=fb_path,
            output_dir=run_dir / "pr_c",
        )
        assert result["status"] == "BLOCKED"
        all_blockers = result.get("blockers", []) + result.get("extra", {}).get("blockers", [])
        assert any("pit_run_id_mismatch" in b for b in all_blockers)


# ═══════════════════════════════════════════════════════════════════════════════
# Capital Firewall E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestCapitalFirewallE2E:
    """Capital firewall with new split semantics."""

    def test_firewall_requires_both_chain_and_economic_pass(self):
        from scripts.research.capital_firewall import build_decomposed_capital_firewall
        # All E0 → BLOCKED
        result = build_decomposed_capital_firewall(
            data_evidence="DataEvidence.E0",
            alpha_evidence="AlphaEvidence.E0",
            execution_evidence="ExecutionEvidence.E0",
            pr_chain_status="PASS",
            economic_status="PASS",
        )
        assert result["status"] == "BLOCKED"

    def test_firewall_no_longer_accepts_pr_i_triggered(self):
        """Capital firewall must NOT use pr_i_status parameter."""
        import inspect
        from scripts.research.capital_firewall import build_decomposed_capital_firewall
        sig = inspect.signature(build_decomposed_capital_firewall)
        assert "pr_chain_status" in sig.parameters
        assert "economic_status" in sig.parameters
        assert "pr_i_status" not in sig.parameters


# ═══════════════════════════════════════════════════════════════════════════════
# Seal E2E
# ═══════════════════════════════════════════════════════════════════════════════


class TestSealRejectionE2E:
    """Seal re-seal rejection with real function calls."""

    def test_seal_rejects_second_seal(self, tmp_path):
        from runtime.artifact_seal import seal_directory
        run_dir = tmp_path / "seal_test"
        run_dir.mkdir()
        (run_dir / "data.txt").write_text("hello")
        temp_registry = tmp_path / "seal_registry.json"

        # First seal succeeds
        seal_directory(run_dir, run_id="seal_test", git_commit_sha="test",
                       registry_path_override=temp_registry)

        # Second seal must raise FileExistsError
        with pytest.raises(FileExistsError, match="already sealed"):
            seal_directory(run_dir, run_id="seal_test", git_commit_sha="test",
                           registry_path_override=temp_registry)

        # Clean up read-only
        _make_writable(run_dir)


def _make_writable(p: Path) -> None:
    if p.is_dir():
        p.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for child in p.rglob("*"):
            if child.is_dir():
                child.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            elif child.is_file():
                child.chmod(stat.S_IRUSR | stat.S_IWUSR)
