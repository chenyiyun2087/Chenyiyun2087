from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.quality.build_strict_ledger_regression_fixture import build_payload
from scripts.quality.regression_baseline import BaselineFormatError, compare_payloads, validate_baseline_payload


REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = REPO_ROOT / "regression" / "baselines" / "strict_ledger_core.v1.json"


def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_strict_ledger_fixture_matches_approved_baseline():
    report = compare_payloads(_baseline(), build_payload())
    assert report["status"] == "PASS"
    assert report["failure_count"] == 0


def test_metric_drift_fails_without_explicit_tolerance():
    actual = build_payload()
    actual["result"]["metrics"]["end_equity"] = 1009.99
    report = compare_payloads(_baseline(), actual)
    assert report["status"] == "FAIL"
    assert any(item["path"] == "metrics.end_equity" for item in report["failures"])


def test_symbol_drift_fails_when_ordered_list_is_exact():
    actual = build_payload()
    actual["result"]["selection"]["symbols"] = ["000002"]
    report = compare_payloads(_baseline(), actual)
    assert report["status"] == "FAIL"
    assert any(item["path"] == "selection.symbols" for item in report["failures"])


def test_explicit_numeric_tolerance_can_be_used_for_non_safety_metric():
    baseline = _baseline()
    baseline["tolerances"]["metrics.end_equity"] = {"absolute": 0.5}
    actual = build_payload()
    actual["result"]["metrics"]["end_equity"] = 1009.75
    report = compare_payloads(baseline, actual)
    assert report["status"] == "PASS"


def test_invalid_baseline_contract_is_rejected_before_comparison():
    invalid = copy.deepcopy(_baseline())
    del invalid["expected"]["metrics"]
    try:
        validate_baseline_payload(invalid)
    except BaselineFormatError as exc:
        assert "metrics" in str(exc)
    else:
        raise AssertionError("Invalid baseline must be rejected")


def test_baseline_update_requires_explicit_human_approval_metadata():
    invalid = copy.deepcopy(_baseline())
    del invalid["metadata"]["approved_by"]
    try:
        validate_baseline_payload(invalid)
    except BaselineFormatError as exc:
        assert "approved_by" in str(exc)
    else:
        raise AssertionError("Unapproved baseline must be rejected")
