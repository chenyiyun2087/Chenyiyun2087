"""PR-I Complete Chain Enforcement Tests.

Verify that PR-I requires ALL of PR-B, PR-C, PR-D, PR-E.
Every missing layer, invalid status, SHA mismatch, or fixture_mode=True
must produce BLOCKED.  No "chain intact where defined" escape hatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.acceptance_config import canonical_sha
from runtime.pr_chain_binding import (
    bind_pr_b,
    bind_pr_c,
    bind_pr_d,
    bind_pr_e,
    verify_pr_i_chain,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _build_complete_chain(tmp_path: Path, pit_run_id: str = "test_pit_run_001"):
    """Build a complete PASS B/C/D/E chain and return (paths_dict, run_dir)."""
    run_dir = tmp_path / "chain"
    run_dir.mkdir()

    # PR-B
    readiness_path = run_dir / "readiness_report.json"
    readiness_path.write_text(json.dumps({
        "status": "PASS",
        "evidence_sha256": "e" * 64,
    }))

    pr_b_dir = run_dir / "pr_b"
    bind_pr_b(
        formal_pit_run_id=pit_run_id,
        package_sha256="p" * 64,
        readiness_report_path=readiness_path,
        output_dir=pr_b_dir,
        release_id="test_release",
        strategy_set="champion_v1_2b",
    )

    # PR-C: create real artifact files with correct hash conventions
    formal_run_id = f"formal-{pit_run_id}"
    formal_run_manifest_path = run_dir / "formal_run_manifest.json"
    formal_run_manifest = {
        "schema_version": "pr_chain_binding_v5_1",
        "status": "PASS",  # v5.1.2: also accepts VERIFIED
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": formal_run_id,
        "run_id": formal_run_id,
        "fixture_mode": False,
        "capital_authority": False,
    }
    # Use manifest_sha256 as the self-hash (no content_sha256 copy — breaks verification)
    formal_run_manifest["manifest_sha256"] = canonical_sha(
        {k: v for k, v in formal_run_manifest.items() if k != "manifest_sha256"}
    )
    formal_run_manifest_path.write_text(json.dumps(formal_run_manifest))

    frozen_bundle_path = run_dir / "frozen_bundle.json"
    frozen_bundle = {
        "schema_version": "pr_chain_binding_v5_1",
        "status": "PASS",
        "formal_pit_run_id": pit_run_id,
        "fixture_mode": False,
        "capital_authority": False,
    }
    frozen_bundle["content_sha256"] = canonical_sha(
        {k: v for k, v in frozen_bundle.items() if k != "content_sha256"}
    )
    frozen_bundle_path.write_text(json.dumps(frozen_bundle))

    pr_c_dir = run_dir / "pr_c"
    bind_pr_c(
        pr_b_binding_path=pr_b_dir / "pr_b_binding.json",
        formal_run_id=formal_run_id,
        formal_run_manifest_path=formal_run_manifest_path,
        frozen_bundle_path=frozen_bundle_path,
        output_dir=pr_c_dir,
    )

    # PR-D: create real OOS report (uses evidence_sha256)
    oos_report_path = run_dir / "oos_report.json"
    oos_report = {
        "schema_version": "pr_chain_binding_v5_1",
        "status": "PASS",
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": formal_run_id,
        "fixture_mode": False,
        "capital_authority": False,
    }
    oos_report["evidence_sha256"] = canonical_sha(
        {k: v for k, v in oos_report.items() if k != "evidence_sha256"}
    )
    oos_report_path.write_text(json.dumps(oos_report))

    pr_d_dir = run_dir / "pr_d"
    bind_pr_d(
        pr_c_binding_path=pr_c_dir / "pr_c_binding.json",
        oos_report_path=oos_report_path,
        output_dir=pr_d_dir,
        fixture_mode=False,
    )

    # PR-E: create real capacity report (uses evidence_sha256)
    capacity_report_path = run_dir / "capacity_report.json"
    capacity_report = {
        "schema_version": "pr_chain_binding_v5_1",
        "status": "PASS",
        "formal_pit_run_id": pit_run_id,
        "formal_run_id": formal_run_id,
        "fixture_mode": False,
        "capital_authority": False,
    }
    capacity_report["evidence_sha256"] = canonical_sha(
        {k: v for k, v in capacity_report.items() if k != "evidence_sha256"}
    )
    capacity_report_path.write_text(json.dumps(capacity_report))

    pr_e_dir = run_dir / "pr_e"
    bind_pr_e(
        pr_c_binding_path=pr_c_dir / "pr_c_binding.json",
        capacity_report_path=capacity_report_path,
        output_dir=pr_e_dir,
        fixture_mode=False,
    )

    paths = {
        "pr_b": pr_b_dir / "pr_b_binding.json",
        "pr_c": pr_c_dir / "pr_c_binding.json",
        "pr_d": pr_d_dir / "pr_d_binding.json",
        "pr_e": pr_e_dir / "pr_e_binding.json",
    }
    return paths, run_dir


def _write_file(path: Path, data: dict):
    """Write JSON to path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# Happy Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompleteChainHappyPath:
    """PR-I PASS when all layers are present and valid."""

    def test_complete_chain_all_pass(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "PASS", f"Blockers: {result.get('blockers')}"
        assert result["blockers"] == []
        assert result["capital_authority"] is False
        assert result["stage"] == "PR_I"
        assert len(result["content_sha256"]) == 64

    def test_complete_chain_economic_failed_still_pass(self, tmp_path):
        """PR-D and PR-E with ECONOMIC_FAILED status should still PASS PR-I."""
        paths, run_dir = _build_complete_chain(tmp_path)

        # Rebuild PR-D with ECONOMIC_FAILED
        oos_report_path = run_dir / "oos_report_econ.json"
        oos_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "ECONOMIC_FAILED",
            "formal_pit_run_id": "test_pit_run_001",
            "formal_run_id": "formal-test_pit_run_001",
            "fixture_mode": False,
            "capital_authority": False,
        }
        oos_report["evidence_sha256"] = canonical_sha(
            {k: v for k, v in oos_report.items() if k != "evidence_sha256"}
        )
        oos_report_path.write_text(json.dumps(oos_report))

        pr_d_dir = run_dir / "pr_d"
        bind_pr_d(
            pr_c_binding_path=paths["pr_c"],
            oos_report_path=oos_report_path,
            output_dir=pr_d_dir,
            fixture_mode=False,
        )

        # Rebuild PR-E with ECONOMIC_FAILED
        capacity_report_path = run_dir / "capacity_report_econ.json"
        capacity_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "ECONOMIC_FAILED",
            "formal_pit_run_id": "test_pit_run_001",
            "formal_run_id": "formal-test_pit_run_001",
            "fixture_mode": False,
            "capital_authority": False,
        }
        capacity_report["evidence_sha256"] = canonical_sha(
            {k: v for k, v in capacity_report.items() if k != "evidence_sha256"}
        )
        capacity_report_path.write_text(json.dumps(capacity_report))

        pr_e_dir = run_dir / "pr_e"
        bind_pr_e(
            pr_c_binding_path=paths["pr_c"],
            capacity_report_path=capacity_report_path,
            output_dir=pr_e_dir,
            fixture_mode=False,
        )

        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=pr_d_dir / "pr_d_binding.json",
            pr_e_path=pr_e_dir / "pr_e_binding.json",
        )
        assert result["status"] == "PASS", f"Blockers: {result.get('blockers')}"


# ═══════════════════════════════════════════════════════════════════════════════
# Missing Layer Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingLayers:
    """Every missing PR layer must produce BLOCKED."""

    def test_pr_d_missing(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=tmp_path / "nonexistent" / "pr_d_binding.json",
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_missing_or_invalid_json" in result["blockers"]

    def test_pr_e_missing(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=tmp_path / "nonexistent" / "pr_e_binding.json",
        )
        assert result["status"] == "BLOCKED"
        assert "pr_e_missing_or_invalid_json" in result["blockers"]

    def test_pr_b_missing(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=tmp_path / "nonexistent" / "pr_b_binding.json",
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_missing_or_invalid_json" in result["blockers"]

    def test_pr_c_missing(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=tmp_path / "nonexistent" / "pr_c_binding.json",
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_missing_or_invalid_json" in result["blockers"]

    def test_pr_d_and_pr_e_both_missing(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=tmp_path / "nonexistent" / "pr_d.json",
            pr_e_path=tmp_path / "nonexistent" / "pr_e.json",
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_missing_or_invalid_json" in result["blockers"]
        assert "pr_e_missing_or_invalid_json" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Invalid JSON Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvalidJson:
    """Non-JSON or non-dict content must produce BLOCKED."""

    def test_pr_d_invalid_json(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        bad_path = tmp_path / "pr_d_bad" / "pr_d_binding.json"
        bad_path.parent.mkdir()
        bad_path.write_text("not valid json {{{")
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=bad_path,
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_missing_or_invalid_json" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Status Semantics Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusSemantics:
    """Wrong status values must produce BLOCKED."""

    def test_pr_b_status_not_pass(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        # Tamper PR-B status
        pr_b_data = _read_json(paths["pr_b"])
        pr_b_data["status"] = "BLOCKED"
        _write_file(paths["pr_b"], pr_b_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_status_not_pass" in result["blockers"]

    def test_pr_c_status_not_pass(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        pr_c_data["status"] = "BLOCKED"
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_status_not_pass" in result["blockers"]

    def test_pr_d_status_blocked(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_d_data = _read_json(paths["pr_d"])
        pr_d_data["status"] = "BLOCKED"
        _write_file(paths["pr_d"], pr_d_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert any(b for b in result["blockers"] if "pr_d_status" in b)

    def test_pr_e_status_blocked(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_e_data = _read_json(paths["pr_e"])
        pr_e_data["status"] = "BLOCKED"
        _write_file(paths["pr_e"], pr_e_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert any(b for b in result["blockers"] if "pr_e_status" in b)

    def test_pr_d_status_invalid_random(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_d_data = _read_json(paths["pr_d"])
        pr_d_data["status"] = "RANDOM_GARBAGE"
        _write_file(paths["pr_d"], pr_d_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_status_invalid" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# SHA Mismatch Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSHAMismatches:
    """Any SHA inconsistency must produce BLOCKED."""

    def test_pr_b_sha_tampered(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        # Modify PR-B release_id (changes file content, breaks PR-C binding)
        pr_b_data = _read_json(paths["pr_b"])
        pr_b_data["release_id"] = "tampered_release"
        _write_file(paths["pr_b"], pr_b_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_file_sha_mismatch" in result["blockers"]

    def test_pr_b_content_sha_tampered(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_b_data = _read_json(paths["pr_b"])
        # Modify a field without updating content_sha256
        pr_b_data["release_id"] = "tampered"
        pr_b_data["content_sha256"] = "a" * 64  # fake hash
        _write_file(paths["pr_b"], pr_b_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_content_sha_invalid" in result["blockers"]

    def test_pr_c_content_sha_tampered(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        pr_c_data["formal_run_id"] = "tampered_run"
        pr_c_data["content_sha256"] = "b" * 64
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_content_sha_invalid" in result["blockers"]

    def test_empty_sha_in_pr_c_manifest(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        pr_c_data["formal_run_manifest_sha256"] = ""
        pr_c_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_c_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_manifest_sha_invalid_for_pr_d_binding" in result["blockers"]

    def test_missing_content_sha256(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_d_data = _read_json(paths["pr_d"])
        del pr_d_data["content_sha256"]
        _write_file(paths["pr_d"], pr_d_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_content_sha_invalid" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Run ID Mismatch Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunIDMismatches:
    """Inconsistent run IDs must produce BLOCKED."""

    def test_pr_c_pr_d_run_id_mismatch(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_d_data = _read_json(paths["pr_d"])
        pr_d_data["formal_run_id"] = "different_run_id"
        pr_d_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_d_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_d"], pr_d_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "run_id_pr_c_pr_d_mismatch" in result["blockers"]

    def test_pr_c_pr_e_run_id_mismatch(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_e_data = _read_json(paths["pr_e"])
        pr_e_data["formal_run_id"] = "different_run_id"
        pr_e_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_e_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_e"], pr_e_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "run_id_pr_c_pr_e_mismatch" in result["blockers"]

    def test_pr_b_pr_c_pit_run_id_mismatch(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        pr_c_data["formal_pit_run_id"] = "different_pit_run"
        pr_c_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_c_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_pit_run_id_mismatch" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Fixture Mode Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureMode:
    """fixture_mode=True on any layer must produce BLOCKED."""

    def test_pr_d_fixture_mode_true(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        # Create OOS report with fixture_mode=True
        oos_report_path = run_dir / "oos_fixture.json"
        oos_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": "test_pit_run_001",
            "formal_run_id": "formal-test_pit_run_001",
            "fixture_mode": True,
            "capital_authority": False,
        }
        oos_report["evidence_sha256"] = canonical_sha(
            {k: v for k, v in oos_report.items() if k != "evidence_sha256"}
        )
        oos_report_path.write_text(json.dumps(oos_report))

        pr_d_dir = run_dir / "pr_d_fixture"
        bind_pr_d(
            pr_c_binding_path=paths["pr_c"],
            oos_report_path=oos_report_path,
            output_dir=pr_d_dir,
            fixture_mode=True,
        )
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=pr_d_dir / "pr_d_binding.json",
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_fixture_mode_not_false" in result["blockers"]

    def test_pr_e_fixture_mode_true(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        # Create capacity report with fixture_mode=True
        cap_report_path = run_dir / "cap_fixture.json"
        cap_report = {
            "schema_version": "pr_chain_binding_v5_1",
            "status": "PASS",
            "formal_pit_run_id": "test_pit_run_001",
            "formal_run_id": "formal-test_pit_run_001",
            "fixture_mode": True,
            "capital_authority": False,
        }
        cap_report["evidence_sha256"] = canonical_sha(
            {k: v for k, v in cap_report.items() if k != "evidence_sha256"}
        )
        cap_report_path.write_text(json.dumps(cap_report))

        pr_e_dir = run_dir / "pr_e_fixture"
        bind_pr_e(
            pr_c_binding_path=paths["pr_c"],
            capacity_report_path=cap_report_path,
            output_dir=pr_e_dir,
            fixture_mode=True,
        )
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=pr_e_dir / "pr_e_binding.json",
        )
        assert result["status"] == "BLOCKED"
        assert "pr_e_fixture_mode_not_false" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Capital Authority Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapitalAuthority:
    """capital_authority must always be False."""

    def test_pr_b_capital_authority_true(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_b_data = _read_json(paths["pr_b"])
        pr_b_data["capital_authority"] = True
        pr_b_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_b_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_b"], pr_b_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_capital_authority_not_false" in result["blockers"]

    def test_pr_i_report_has_capital_authority_false(self, tmp_path):
        paths, _ = _build_complete_chain(tmp_path)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["capital_authority"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Schema Version Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    """Wrong schema_version must produce BLOCKED."""

    def test_pr_b_wrong_schema_version(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_b_data = _read_json(paths["pr_b"])
        pr_b_data["schema_version"] = "pr_chain_binding_v3_0"
        pr_b_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_b_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_b"], pr_b_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_b_schema_version_wrong" in result["blockers"]

    def test_pr_d_wrong_schema_version(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_d_data = _read_json(paths["pr_d"])
        pr_d_data["schema_version"] = "wrong_version"
        pr_d_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_d_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_d"], pr_d_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_d_schema_version_wrong" in result["blockers"]


# ═══════════════════════════════════════════════════════════════════════════════
# Empty formal_run_id Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyFormalRunID:
    """Empty or missing formal_run_id in PR-C must produce BLOCKED."""

    def test_pr_c_empty_formal_run_id(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        pr_c_data["formal_run_id"] = ""
        pr_c_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_c_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_formal_run_id_empty_or_invalid" in result["blockers"]

    def test_pr_c_missing_formal_run_id_key(self, tmp_path):
        paths, run_dir = _build_complete_chain(tmp_path)
        pr_c_data = _read_json(paths["pr_c"])
        del pr_c_data["formal_run_id"]
        pr_c_data["content_sha256"] = canonical_sha(
            {k: v for k, v in pr_c_data.items() if k != "content_sha256"}
        )
        _write_file(paths["pr_c"], pr_c_data)
        result = verify_pr_i_chain(
            pr_b_path=paths["pr_b"],
            pr_c_path=paths["pr_c"],
            pr_d_path=paths["pr_d"],
            pr_e_path=paths["pr_e"],
        )
        assert result["status"] == "BLOCKED"
        assert "pr_c_formal_run_id_empty_or_invalid" in result["blockers"]
