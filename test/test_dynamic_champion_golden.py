from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.quality.dynamic_champion_golden import (
    DEFAULT_BASELINE,
    GoldenError,
    build_actual,
    verify,
    write_baseline,
)


ROOT = Path(__file__).resolve().parents[1]


def test_current_frozen_outputs_match_content_addressed_baseline():
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    report = verify(baseline, build_actual())
    assert report["status"] == "PASS"
    assert report["failure_count"] == 0


@pytest.mark.parametrize("domain", ["candidates", "orders", "artifact"])
def test_any_domain_drift_fails(domain: str):
    actual = build_actual()
    baseline = {
        "schema_version": "dynamic_champion_content_golden_v1",
        "baseline_id": "test.v1",
        "baseline_version": "v1",
        "expected": copy.deepcopy(actual),
    }
    actual["domains"][domain]["semantic_sha256"] = "0" * 64
    report = verify(baseline, actual)
    assert report["status"] == "FAIL"
    assert any(item["domain"] == domain for item in report["failures"])


def test_missing_required_domain_fails():
    actual = build_actual()
    baseline = {
        "schema_version": "dynamic_champion_content_golden_v1",
        "baseline_id": "test.v1",
        "baseline_version": "v1",
        "expected": copy.deepcopy(actual),
    }
    del actual["domains"]["ledger"]
    report = verify(baseline, actual)
    assert report["status"] == "FAIL"
    assert any(item["reason"] == "missing_domains" for item in report["failures"])


def test_baseline_cannot_be_overwritten(tmp_path: Path):
    target = tmp_path / "baseline.json"
    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(GoldenError, match="overwrite_forbidden"):
        write_baseline(
            target,
            build_actual(),
            baseline_version="v2",
            approved_by="test",
            change_reason="test",
        )


def test_baseline_requires_explicit_version_and_reason(tmp_path: Path):
    with pytest.raises(GoldenError, match="baseline_version_required"):
        write_baseline(
            tmp_path / "new.json",
            build_actual(),
            baseline_version="",
            approved_by="test",
            change_reason="test",
        )
