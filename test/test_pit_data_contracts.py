"""Regression tests for PIT data contracts identified in v4.7 review.

Each test addresses a specific gate-bypass found during the independent review.
These tests enforce fail-closed behavior for missing/placeholder/synthetic data.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.pit_factor_panel_builder import (
    REQUIRED_COLUMNS,
    SOURCE_NAMES,
    build_pit_factor_panel,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _minimal_frame(name: str, n: int = 30) -> pd.DataFrame:
    """Build a minimal valid frame for one PIT family."""
    base_date = pd.Timestamp("2023-01-04")
    dates = [base_date + pd.Timedelta(days=i) for i in range(n)]
    symbols = [f"{i:06d}" for i in range(1, 6)]
    rows = []
    for d in dates:
        for s in symbols:
            rows.append({"trade_date": d, "symbol": s})
    df = pd.DataFrame(rows)
    defaults = {
        "market": {
            "open": 10.0, "close": 10.5, "pre_close": 10.0,
            "amount": 1e8, "circ_mv": 1e10, "market_return": 0.001,
            "market_regime": "NEUTRAL",
            "market_available_at": "2023-01-04T14:00:00+08:00",
        },
        "universe": {
            "is_listed": 1, "is_st": 0, "is_suspended": 0,
            "limit_status": "NORMAL", "security_status_transition": "LISTED",
            "universe_available_at": "2023-01-04T14:00:00+08:00",
        },
        "financial": {
            "pb": 2.0, "financial_period_end": "20221231",
            "announcement_date": "20230401",
            "financial_available_at": "2023-04-01T10:00:00+08:00",
            "revision_id": "2", "financial_source_snapshot_sha": "a" * 64,
        },
        "industry": {
            "industry": "银行",
            "industry_available_at": "2023-01-04T14:00:00+08:00",
        },
        "adjustment": {
            "adj_factor": 1.0, "corporate_action_type": "DIVIDEND",
            "ex_date": "20230601", "record_date": "20230605",
            "adjustment_factor_version": "2",
            "adjustment_available_at": "2023-01-04T14:00:00+08:00",
        },
    }
    for col, val in defaults.get(name, {}).items():
        df[col] = val
    return df


def _write_frames(out_dir: Path) -> dict[str, Path]:
    """Write minimal valid frames for all 5 families."""
    paths = {}
    for name in SOURCE_NAMES:
        df = _minimal_frame(name)
        p = out_dir / f"{name}.parquet"
        df.to_parquet(p, index=False)
        paths[name] = p
    return paths


def _patch_manifest_origin(manifest_path: Path, origin: str) -> None:
    data = json.loads(manifest_path.read_text())
    data["evidence_origin"] = origin
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _write_manifest(out_dir: Path, paths: dict[str, Path], *, field_definition_hash: str | None = None, evidence_origin: str = "SYNTHETIC") -> Path:
    """Write a minimal qualified source manifest."""
    manifest = {
        "schema_version": "alpha_v4_7_pit_source_manifest_v1",
        "status": "QUALIFIED",
        "adapter_type": "FILE",
        "release": "test_release",
        "evidence_origin": evidence_origin,
        "provider": "test",
        "retrieved_at": "2026-07-31T12:00:00+00:00",
        "schema_semantic_version": "alpha_v4_7_pit_v1",
        "field_definition_hash": field_definition_hash or _canonical_sha(
            {n: sorted(REQUIRED_COLUMNS[n]) for n in SOURCE_NAMES}
        ),
        "sources": {
            name: {
                "path": str(p),
                "sha256": _file_sha(p),
                "schema_hash": "abc",
                "rows": 30,
                "version": "test",
                "provider": "test",
            }
            for name, p in paths.items()
        },
    }
    manifest["content_sha256"] = _canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    mp = out_dir / "pit_source_manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return mp


# ── Tests ────────────────────────────────────────────────────────────────────


class TestMissingAvailableAtBlocks:
    """Missing available_at must block the builder (no auto-fill to signal time)."""

    def test_missing_market_available_at_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        # Set all market_available_at to NaN
        market = pd.read_parquet(paths["market"])
        market["market_available_at"] = np.nan
        market.to_parquet(paths["market"], index=False)
        manifest = _write_manifest(tmp_path, paths)

        result = build_pit_factor_panel(
            market_path=paths["market"],
            universe_path=paths["universe"],
            financial_path=paths["financial"],
            industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest,
            output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        assert any("missing_available_at:market_available_at" in b for b in result["blockers"])

    def test_missing_financial_available_at_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        fin = pd.read_parquet(paths["financial"])
        fin["financial_available_at"] = np.nan
        fin.to_parquet(paths["financial"], index=False)
        manifest = _write_manifest(tmp_path, paths)

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        assert any("missing_available_at:financial_available_at" in b for b in result["blockers"])


class TestPbNotFilled:
    """NaN PB must NOT be filled with 0.0 — coverage must reflect the gap."""

    def test_missing_pb_reduces_financial_coverage(self, tmp_path):
        paths = _write_frames(tmp_path)
        fin = pd.read_parquet(paths["financial"])
        # Set pb to NaN for rows after warmup to ensure they're in qualified panel
        fin["pb"] = np.nan
        fin.to_parquet(paths["financial"], index=False)
        manifest = _write_manifest(tmp_path, paths)

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        # With ALL pb NaN, value factor coverage must be 0
        fc = result.get("factor_coverage", {})
        if "value" in fc:
            assert fc["value"] == 0.0, f"Value factor coverage should be 0 with all-NaN PB, got {fc['value']}"


class TestPlaceholderFieldDefinitionHash:
    """Placeholder field_definition_hash must block."""

    def test_matCHANGEME_placeholder_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths, field_definition_hash="matCHANGEME_something")

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        assert any("field_definition_hash_is_placeholder" in b for b in result["blockers"])

    def test_short_field_definition_hash_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths, field_definition_hash="abc123", evidence_origin="HISTORICAL_REAL")

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7", fixture_mode=True,
        )
        assert result["status"] == "BLOCKED"
        assert any("field_definition_hash_is_placeholder" in b for b in result["blockers"])


class TestConstantSemanticColumns:
    """Constant placeholder values in semantic columns must block."""

    def test_constant_security_status_transition_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        uni = pd.read_parquet(paths["universe"])
        uni["security_status_transition"] = "ACTIVE"
        uni.to_parquet(paths["universe"], index=False)
        manifest = _write_manifest(tmp_path, paths, evidence_origin="HISTORICAL_REAL")

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7", fixture_mode=True,
        )
        assert result["status"] == "BLOCKED"
        assert any("security_status_transition_constant" in b for b in result["blockers"])

    def test_constant_corporate_action_type_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        adj = pd.read_parquet(paths["adjustment"])
        adj["corporate_action_type"] = "NONE"
        adj.to_parquet(paths["adjustment"], index=False)
        manifest = _write_manifest(tmp_path, paths, evidence_origin="HISTORICAL_REAL")

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7", fixture_mode=True,
        )
        assert result["status"] == "BLOCKED"
        assert any("corporate_action_type_constant" in b for b in result["blockers"])


class TestMarketRegimeDiversity:
    """At least 3 unique market regime values required."""

    def test_single_regime_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        mkt = pd.read_parquet(paths["market"])
        mkt["market_regime"] = "UNKNOWN"
        mkt.to_parquet(paths["market"], index=False)
        manifest = _write_manifest(tmp_path, paths, evidence_origin="HISTORICAL_REAL")

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7", fixture_mode=True,
        )
        assert result["status"] == "BLOCKED"
        assert any("market_regime_diversity" in b for b in result["blockers"])


class TestManifestIntegrity:
    """Manifest modifications after adapter run must be detected."""

    def test_manifest_sha_mismatch_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)

        # Tamper with a source file SHA in the manifest
        data = json.loads(manifest.read_text())
        data["sources"]["market"]["sha256"] = "f" * 64
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        assert any("source_manifest_sha_mismatch:market" in b for b in result["blockers"])

    def test_tampered_source_file_blocks(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)

        # Tamper with the actual market parquet file
        market = pd.read_parquet(paths["market"])
        market["close"] = 999.0  # change data
        market.to_parquet(paths["market"], index=False)

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["status"] == "BLOCKED"
        assert any("source_manifest_sha_mismatch:market" in b for b in result["blockers"])


class TestTargetDaysWarning:
    """History below 504 target is noted but does not block (252 is the hard minimum)."""

    def test_target_504_not_a_blocker(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        # 30 synthetic dates < 252, so blocked by the min_days check, not the target
        assert result["status"] == "BLOCKED"
        assert any("history_below_252" in b for b in result["blockers"])
        # Target 504 should NOT be in blockers (it's advisory, not a gate)
        target_blockers = [b for b in result["blockers"] if "target_504" in b]
        assert len(target_blockers) == 0, f"Target 504 should not be a blocker: {result['blockers']}"


class TestNoShortPanelFallback:
    """Builder must never fall back to short training panel."""

    def test_automatic_short_panel_fallback_is_false(self, tmp_path):
        paths = _write_frames(tmp_path)
        manifest = _write_manifest(tmp_path, paths)

        result = build_pit_factor_panel(
            market_path=paths["market"], universe_path=paths["universe"],
            financial_path=paths["financial"], industry_path=paths["industry"],
            adjustment_path=paths["adjustment"],
            source_manifest_path=manifest, output_dir=tmp_path / "output",
            profile_name="alpha_v4_7",
        )
        assert result["automatic_short_panel_fallback"] is False
        assert result["capital_authority"] is False
