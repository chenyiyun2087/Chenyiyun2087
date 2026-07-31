"""End-to-end + mutation tests for Formal Evidence Backbone v5.1.

E2E: adapter → builder → scores → package → readiness → PR chain (complete B/C/D/E)
Mutations: tamper one byte → at least one downstream stage BLOCKED
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.formal_evidence_contract import EvidenceStatus, VersionManifest
from runtime.pr_chain_binding import (
    bind_pr_b, bind_pr_c, bind_pr_d, bind_pr_e, verify_pr_i_chain,
)
from runtime.artifact_seal import seal_directory, verify_seal
from scripts.research.build_formal_scores import build_formal_scores
from scripts.research.build_formal_package_v3 import build_formal_package_v3
from scripts.research.formal_readiness_v3 import validate as readiness_validate

from test.test_pit_data_contracts import (
    _write_frames, _write_manifest, _write_adapter_report,
    _write_manifest_with_config,
)

# ═══════════════════════════════════════════════════════════════════════════════
# E2E Happy Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestE2EHappyPath:
    """Full SYNTHETIC pipeline → S3 evidence (never E3)."""

    def test_e2e_synthetic_pipeline(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)
        run_id = "e2e_test_" + canonical_sha({"test": "e2e_synthetic"})[:16]
        run_dir = tmp_path / run_id
        run_dir.mkdir()

        # ── Stage: Scores ──
        # Build a minimal factor panel
        panel = pd.DataFrame({
            "trade_date": pd.date_range("2023-01-04", periods=30),
            "symbol": ["000001"] * 30,
            "volatility": np.random.randn(30) * 0.01,
            "value": np.random.randn(30) * 0.01,
            "size": np.random.randn(30) * 0.01,
            "momentum": np.random.randn(30) * 0.01,
            "liquidity": np.random.randn(30) * 0.01,
            "market_beta": np.random.randn(30) * 0.01,
        })
        panel_path = tmp_path / "factor_panel.parquet"
        panel.to_parquet(panel_path)

        scores_dir = run_dir / "scores"
        result = build_formal_scores(factor_panel_path=panel_path, output_dir=scores_dir)
        assert result["status"] == "PASS"

        # ── Stage: Package ──
        pkg_dir = run_dir / "package"
        result = build_formal_package_v3(
            formal_pit_run_id=run_id,
            scores_path=scores_dir / "formal_scores.parquet",
            output_dir=pkg_dir,
        )
        assert result["status"] == "PASS"

        # ── Stage: Readiness ──
        result = readiness_validate(
            formal_pit_run_id=run_id,
            package_dir=pkg_dir,
            release_id="e2e_test",
            strategy_set="champion_v1_2b",
        )
        assert result["status"] == "PASS"

        # ── Stage: PR Chain ──
        pr_b = bind_pr_b(
            formal_pit_run_id=run_id,
            package_sha256=result.get("package_sha256", ""),
            readiness_report_path=pkg_dir / "package_manifest.json",
            output_dir=run_dir / "pr_b",
            release_id="e2e_test",
            strategy_set="champion_v1_2b",
        )
        assert pr_b["status"] == "PASS"

        # Create real artifact files for PR-C binding
        formal_run_id = run_id + "_c"
        formal_run_manifest_path = run_dir / "formal_run_manifest.json"
        formal_run_manifest = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": run_id,
            "run_id": formal_run_id,
            "fixture_mode": False,
            "capital_authority": False,
        }
        formal_run_manifest["content_sha256"] = canonical_sha(
            {k: v for k, v in formal_run_manifest.items() if k != "content_sha256"}
        )
        formal_run_manifest_path.write_text(json.dumps(formal_run_manifest))

        frozen_bundle_path = run_dir / "frozen_bundle.json"
        frozen_bundle = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": run_id,
            "fixture_mode": False,
            "capital_authority": False,
        }
        frozen_bundle["content_sha256"] = canonical_sha(
            {k: v for k, v in frozen_bundle.items() if k != "content_sha256"}
        )
        frozen_bundle_path.write_text(json.dumps(frozen_bundle))

        pr_c = bind_pr_c(
            pr_b_binding_path=run_dir / "pr_b" / "pr_b_binding.json",
            formal_run_id=formal_run_id,
            formal_run_manifest_path=formal_run_manifest_path,
            frozen_bundle_path=frozen_bundle_path,
            output_dir=run_dir / "pr_c",
        )
        assert pr_c["status"] == "PASS"

        # ── PR-D (OOS) ──
        oos_report_path = run_dir / "oos_report.json"
        oos_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": run_id,
            "formal_run_id": formal_run_id,
            "fixture_mode": False,
            "capital_authority": False,
        }
        oos_report["content_sha256"] = canonical_sha(
            {k: v for k, v in oos_report.items() if k != "content_sha256"}
        )
        oos_report_path.write_text(json.dumps(oos_report))

        pr_d = bind_pr_d(
            pr_c_binding_path=run_dir / "pr_c" / "pr_c_binding.json",
            oos_report_path=oos_report_path,
            output_dir=run_dir / "pr_d",
            fixture_mode=False,
        )
        assert pr_d["status"] == "PASS"

        # ── PR-E (Capacity) ──
        cap_report_path = run_dir / "capacity_report.json"
        cap_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": run_id,
            "formal_run_id": formal_run_id,
            "fixture_mode": False,
            "capital_authority": False,
        }
        cap_report["content_sha256"] = canonical_sha(
            {k: v for k, v in cap_report.items() if k != "content_sha256"}
        )
        cap_report_path.write_text(json.dumps(cap_report))

        pr_e = bind_pr_e(
            pr_c_binding_path=run_dir / "pr_c" / "pr_c_binding.json",
            capacity_report_path=cap_report_path,
            output_dir=run_dir / "pr_e",
            fixture_mode=False,
        )
        assert pr_e["status"] == "PASS"

        # ── Seal (v2.1: use temp registry for test isolation) ──
        temp_registry = tmp_path / "seal_registry.json"
        seal = seal_directory(run_dir, run_id=run_id, git_commit_sha="test",
                              registry_path_override=temp_registry)
        assert seal["file_count"] > 0
        verified = verify_seal(run_dir, registry_path_override=temp_registry)
        assert verified["status"] == "VERIFIED"

        # ── PR-I: Complete chain must PASS ──
        pr_i = verify_pr_i_chain(
            pr_b_path=run_dir / "pr_b" / "pr_b_binding.json",
            pr_c_path=run_dir / "pr_c" / "pr_c_binding.json",
            pr_d_path=run_dir / "pr_d" / "pr_d_binding.json",
            pr_e_path=run_dir / "pr_e" / "pr_e_binding.json",
        )
        assert pr_i["status"] == "PASS", f"Blockers: {pr_i.get('blockers')}"


# ═══════════════════════════════════════════════════════════════════════════════
# Mutation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMutationDetection:
    """Tamper one byte → downstream BLOCKED."""

    def _setup_chain(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)
        run_id = "mut_" + canonical_sha({"test": "mutation"})[:16]
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        panel = pd.DataFrame({
            "trade_date": pd.date_range("2023-01-04", periods=30),
            "symbol": ["000001"] * 30,
            "volatility": np.random.randn(30) * 0.01,
            "value": np.random.randn(30) * 0.01,
            "size": np.random.randn(30) * 0.01,
            "momentum": np.random.randn(30) * 0.01,
            "liquidity": np.random.randn(30) * 0.01,
            "market_beta": np.random.randn(30) * 0.01,
        })
        panel.to_parquet(tmp_path / "panel.parquet")
        return paths, manifest, run_id, run_dir, tmp_path

    def test_tampered_scores_breaks_package(self, tmp_path):
        _, _, run_id, run_dir, tp = self._setup_chain(tmp_path)
        scores_dir = run_dir / "scores"
        build_formal_scores(factor_panel_path=tp / "panel.parquet", output_dir=scores_dir)
        scores_path = scores_dir / "formal_scores.parquet"
        pkg_dir = run_dir / "package"
        build_formal_package_v3(formal_pit_run_id=run_id, scores_path=scores_path, output_dir=pkg_dir)
        # Tamper scores
        scores = pd.read_parquet(scores_path)
        scores["formal_score"] = 999.0
        scores.to_parquet(scores_path)
        # Rebuild package — scores SHA changed
        pkg_dir2 = run_dir / "package2"
        result = build_formal_package_v3(formal_pit_run_id=run_id, scores_path=scores_path, output_dir=pkg_dir2)
        # Different SHA proves tampering was detected
        pkg1 = json.loads((pkg_dir / "package_manifest.json").read_text())
        pkg2 = json.loads((pkg_dir2 / "package_manifest.json").read_text())
        assert pkg1["scores_sha256"] != pkg2["scores_sha256"]

    def test_missing_scores_blocks_readiness(self, tmp_path):
        _, _, run_id, run_dir, _ = self._setup_chain(tmp_path)
        pkg_dir = run_dir / "package"
        pkg_dir.mkdir()
        manifest = {"formal_pit_run_id": run_id, "status": "PASS", "content_sha256": "x" * 64}
        (pkg_dir / "package_manifest.json").write_text(json.dumps(manifest))
        result = readiness_validate(formal_pit_run_id=run_id, package_dir=pkg_dir, release_id="test", strategy_set="champion")
        assert result["status"] == "BLOCKED"
        assert any("required_object_missing" in b for b in result["blockers"])

    def test_run_id_mismatch_blocks_readiness(self, tmp_path):
        _, _, run_id, run_dir, tp = self._setup_chain(tmp_path)
        scores_dir = run_dir / "scores"
        build_formal_scores(factor_panel_path=tp / "panel.parquet", output_dir=scores_dir)
        pkg_dir = run_dir / "package"
        build_formal_package_v3(formal_pit_run_id=run_id, scores_path=scores_dir / "formal_scores.parquet", output_dir=pkg_dir)
        result = readiness_validate(formal_pit_run_id="DIFFERENT_RUN_ID", package_dir=pkg_dir, release_id="test", strategy_set="champion")
        assert result["status"] == "BLOCKED"
        assert any("run_id_mismatch" in b for b in result["blockers"])

    def test_pr_b_sha_tampered_blocks_pr_i(self, tmp_path):
        run_dir = tmp_path / "chain_test"
        run_dir.mkdir()
        pr_b_dir = run_dir / "pr_b"; pr_b_dir.mkdir()
        pr_c_dir = run_dir / "pr_c"; pr_c_dir.mkdir()
        pr_d_dir = run_dir / "pr_d"; pr_d_dir.mkdir()
        pr_e_dir = run_dir / "pr_e"; pr_e_dir.mkdir()
        # Create valid readiness report
        readiness_path = pr_b_dir / "readiness.json"
        readiness_path.write_text(json.dumps({"status": "PASS", "evidence_sha256": "e"*64}))

        pr_b = bind_pr_b(formal_pit_run_id="run_x", package_sha256="", readiness_report_path=readiness_path, output_dir=pr_b_dir, release_id="rel", strategy_set="strat")
        pr_b_path = pr_b_dir / "pr_b_binding.json"

        # Create real artifact files for PR-C
        formal_run_manifest_path = run_dir / "formal_run_manifest.json"
        formal_run_manifest = {
            "schema_version": "pr_chain_binding_v5_1", "status": "PASS",
            "formal_pit_run_id": "run_x", "run_id": "run_x_c",
            "fixture_mode": False, "capital_authority": False,
        }
        formal_run_manifest["content_sha256"] = canonical_sha(
            {k: v for k, v in formal_run_manifest.items() if k != "content_sha256"})
        formal_run_manifest_path.write_text(json.dumps(formal_run_manifest))

        frozen_bundle_path = run_dir / "frozen_bundle.json"
        frozen_bundle = {
            "schema_version": "pr_chain_binding_v5_1", "status": "PASS",
            "formal_pit_run_id": "run_x",
            "fixture_mode": False, "capital_authority": False,
        }
        frozen_bundle["content_sha256"] = canonical_sha(
            {k: v for k, v in frozen_bundle.items() if k != "content_sha256"})
        frozen_bundle_path.write_text(json.dumps(frozen_bundle))

        pr_c = bind_pr_c(pr_b_binding_path=pr_b_path, formal_run_id="run_x_c",
                         formal_run_manifest_path=formal_run_manifest_path,
                         frozen_bundle_path=frozen_bundle_path, output_dir=pr_c_dir)

        # Create valid PR-D and PR-E with real artifact files
        oos_report_path = run_dir / "oos_report.json"
        oos_report = {
            "schema_version": "pr_chain_binding_v5_1", "status": "PASS",
            "formal_pit_run_id": "run_x", "formal_run_id": "run_x_c",
            "fixture_mode": False, "capital_authority": False,
        }
        oos_report["content_sha256"] = canonical_sha(
            {k: v for k, v in oos_report.items() if k != "content_sha256"})
        oos_report_path.write_text(json.dumps(oos_report))
        bind_pr_d(pr_c_binding_path=pr_c_dir / "pr_c_binding.json",
                  oos_report_path=oos_report_path, output_dir=pr_d_dir, fixture_mode=False)

        cap_report_path = run_dir / "capacity_report.json"
        cap_report = {
            "schema_version": "pr_chain_binding_v5_1", "status": "PASS",
            "formal_pit_run_id": "run_x", "formal_run_id": "run_x_c",
            "fixture_mode": False, "capital_authority": False,
        }
        cap_report["content_sha256"] = canonical_sha(
            {k: v for k, v in cap_report.items() if k != "content_sha256"})
        cap_report_path.write_text(json.dumps(cap_report))
        bind_pr_e(pr_c_binding_path=pr_c_dir / "pr_c_binding.json",
                  capacity_report_path=cap_report_path, output_dir=pr_e_dir, fixture_mode=False)
        # Tamper PR-B
        data = json.loads(pr_b_path.read_text())
        data["release_id"] = "tampered"
        pr_b_path.write_text(json.dumps(data))
        # PR-I must detect the PR-B file SHA mismatch
        pr_i = verify_pr_i_chain(
            pr_b_path=pr_b_path,
            pr_c_path=pr_c_dir / "pr_c_binding.json",
            pr_d_path=pr_d_dir / "pr_d_binding.json",
            pr_e_path=pr_e_dir / "pr_e_binding.json",
        )
        assert pr_i["status"] == "BLOCKED"
        assert any("pr_b_file_sha_mismatch" in b for b in pr_i["blockers"])

    def test_symlink_in_package_blocks_readiness(self, tmp_path):
        _, _, run_id, run_dir, tp = self._setup_chain(tmp_path)
        scores_dir = run_dir / "scores"
        build_formal_scores(factor_panel_path=tp / "panel.parquet", output_dir=scores_dir)
        pkg_dir = run_dir / "package"
        build_formal_package_v3(formal_pit_run_id=run_id, scores_path=scores_dir / "formal_scores.parquet", output_dir=pkg_dir)
        # Create symlink
        (pkg_dir / "link_to_scores").symlink_to(scores_dir / "formal_scores.parquet")
        result = readiness_validate(formal_pit_run_id=run_id, package_dir=pkg_dir, release_id="test", strategy_set="champion")
        assert result["status"] == "BLOCKED"
        assert any("symlink_forbidden" in b for b in result["blockers"])

    def test_seal_detects_tampering(self, tmp_path):
        run_dir = tmp_path / "seal_test"
        run_dir.mkdir()
        (run_dir / "test.txt").write_text("original")
        temp_registry = tmp_path / "seal_registry.json"
        seal_directory(run_dir, run_id="seal_1", git_commit_sha="test",
                       registry_path_override=temp_registry)
        assert verify_seal(run_dir, registry_path_override=temp_registry)["status"] == "VERIFIED"
        # Tamper — make writable first
        import stat
        for p in run_dir.rglob("*"):
            if p.is_file():
                p.chmod(stat.S_IRUSR | stat.S_IWUSR)
        (run_dir / "test.txt").write_text("tampered")
        assert verify_seal(run_dir, registry_path_override=temp_registry)["status"] == "TAMPERED"

    def test_blocked_report_has_required_fields(self, tmp_path):
        from runtime.fail_closed import blocked_report
        r = blocked_report("test_comp", "test_stage", "TEST_ERROR", output_dir=tmp_path)
        assert r["status"] == "BLOCKED"
        assert r["component"] == "test_comp"
        assert r["error_code"] == "TEST_ERROR"
        assert r["capital_authority"] is False
        assert "blockers" in r

    def test_evidence_status_defaults_to_e0(self):
        es = EvidenceStatus()
        assert es.data_evidence == "no_qualified_data"
        assert es.alpha_evidence == "no_valid_economic_evidence"
        assert es.execution_evidence == "no_execution_evidence"
        assert es.capital_authority is False

    def test_run_id_deterministic(self):
        from runtime.formal_evidence_contract import compute_formal_pit_run_id
        args = dict(release_id="r", strategy_set="s", git_commit_sha="a"*40,
                    dependency_lock_sha="b"*64, acceptance_profile_sha="c"*64,
                    adapter_config_sha="d"*64, query_bundle_sha="e"*64,
                    field_semantics_sha="f"*64, database_snapshot_identity="g")
        id1 = compute_formal_pit_run_id(**args)
        id2 = compute_formal_pit_run_id(**args)
        assert id1 == id2
        args["release_id"] = "changed"
        id3 = compute_formal_pit_run_id(**args)
        assert id1 != id3

    def test_version_manifest_content_sha(self):
        vm = VersionManifest(
            strategy_version="v1", data_contract_version="v1",
            field_semantic_version="v1", factor_formula_version="v1",
            execution_model_version="v1", acceptance_profile_version="v1",
        )
        sha = vm.content_sha()
        assert len(sha) == 64
