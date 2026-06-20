"""Compare versioned quantitative-regression baselines without external dependencies.

The module deliberately treats safety invariants as exact contracts and permits
numeric drift only when the approved baseline declares an explicit tolerance.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET


SUPPORTED_SCHEMA_VERSION = "1.0"


class BaselineFormatError(ValueError):
    """Raised when a baseline or actual-result payload violates the contract."""


@dataclass(frozen=True)
class Failure:
    path: str
    message: str
    expected: Any = None
    actual: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
        }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineFormatError(f"File does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BaselineFormatError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BaselineFormatError(f"Top-level JSON value must be an object: {path}")
    return value


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineFormatError(f"{path} must be an object")
    return value


def _get_required(mapping: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise BaselineFormatError(f"Missing required field: {path}.{key}")
    return mapping[key]


def _deep_get(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for key in dotted_path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            raise KeyError(dotted_path)
        current = current[key]
    return current


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            flattened.update(_flatten(item, path))
        else:
            flattened[path] = item
    return flattened


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineFormatError(f"{path} must be numeric, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise BaselineFormatError(f"{path} must be finite")
    return number


def _tolerance(tolerances: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    raw = tolerances.get(path, tolerances.get(path.rsplit(".", 1)[0] + ".*" if "." in path else "*", {}))
    if raw is None:
        return {}
    return _require_mapping(raw, f"tolerances.{path}")


def _compare_exact(expected: Any, actual: Any, path: str, failures: list[Failure]) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            failures.append(Failure(path, "Expected object", expected, actual))
            return
        for key, expected_value in expected.items():
            if key not in actual:
                failures.append(Failure(f"{path}.{key}", "Missing required value", expected_value, None))
                continue
            _compare_exact(expected_value, actual[key], f"{path}.{key}", failures)
        return
    if expected != actual:
        failures.append(Failure(path, "Exact invariant mismatch", expected, actual))


def _compare_metrics(
    expected_metrics: Mapping[str, Any],
    actual_metrics: Mapping[str, Any],
    tolerances: Mapping[str, Any],
    failures: list[Failure],
) -> None:
    for metric_path, expected in _flatten(expected_metrics).items():
        full_path = f"metrics.{metric_path}"
        try:
            actual = _deep_get(actual_metrics, metric_path)
        except KeyError:
            failures.append(Failure(full_path, "Missing metric", expected, None))
            continue

        expected_number = _number(expected, f"expected.{full_path}")
        actual_number = _number(actual, f"actual.{full_path}")
        rules = _tolerance(tolerances, full_path)
        absolute = _number(rules.get("absolute", 0.0), f"tolerances.{full_path}.absolute")
        relative = _number(rules.get("relative", 0.0), f"tolerances.{full_path}.relative")
        allowed = max(absolute, abs(expected_number) * relative)
        delta = abs(actual_number - expected_number)
        if delta > allowed + 1e-12:
            failures.append(
                Failure(
                    full_path,
                    f"Metric drift {delta:.12g} exceeds allowed {allowed:.12g}",
                    expected_number,
                    actual_number,
                )
            )


def _compare_selection(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    tolerances: Mapping[str, Any],
    failures: list[Failure],
) -> None:
    expected_symbols = expected.get("symbols")
    if expected_symbols is not None:
        if not isinstance(expected_symbols, list) or not all(isinstance(item, str) for item in expected_symbols):
            raise BaselineFormatError("expected.selection.symbols must be a list of strings")
        actual_symbols = actual.get("symbols")
        if not isinstance(actual_symbols, list) or not all(isinstance(item, str) for item in actual_symbols):
            failures.append(Failure("selection.symbols", "Missing or invalid symbol list", expected_symbols, actual_symbols))
        else:
            rules = _tolerance(tolerances, "selection.symbols")
            max_changes = int(rules.get("max_symbol_changes", 0))
            order_sensitive = bool(rules.get("order_sensitive", True))
            if order_sensitive:
                changes = sum(
                    1 for expected_symbol, actual_symbol in zip(expected_symbols, actual_symbols)
                    if expected_symbol != actual_symbol
                ) + abs(len(expected_symbols) - len(actual_symbols))
            else:
                changes = len(set(expected_symbols).symmetric_difference(actual_symbols))
            if changes > max_changes:
                failures.append(
                    Failure(
                        "selection.symbols",
                        f"Symbol drift {changes} exceeds allowed {max_changes}",
                        expected_symbols,
                        actual_symbols,
                    )
                )

    expected_weights = expected.get("weights")
    if expected_weights is not None:
        expected_weights = _require_mapping(expected_weights, "expected.selection.weights")
        actual_weights = actual.get("weights")
        if not isinstance(actual_weights, Mapping):
            failures.append(Failure("selection.weights", "Missing weight mapping", expected_weights, actual_weights))
            return
        for symbol, expected_weight in expected_weights.items():
            actual_weight = actual_weights.get(symbol)
            path = f"selection.weights.{symbol}"
            if actual_weight is None:
                failures.append(Failure(path, "Missing symbol weight", expected_weight, None))
                continue
            expected_number = _number(expected_weight, f"expected.{path}")
            actual_number = _number(actual_weight, f"actual.{path}")
            rules = _tolerance(tolerances, path)
            absolute = _number(rules.get("absolute", 0.0), f"tolerances.{path}.absolute")
            relative = _number(rules.get("relative", 0.0), f"tolerances.{path}.relative")
            allowed = max(absolute, abs(expected_number) * relative)
            if abs(actual_number - expected_number) > allowed + 1e-12:
                failures.append(Failure(path, "Weight drift exceeds tolerance", expected_number, actual_number))


def validate_baseline_payload(baseline: Mapping[str, Any]) -> None:
    schema_version = _get_required(baseline, "schema_version", "baseline")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise BaselineFormatError(
            f"Unsupported schema_version {schema_version!r}; expected {SUPPORTED_SCHEMA_VERSION!r}"
        )
    if not isinstance(_get_required(baseline, "baseline_id", "baseline"), str):
        raise BaselineFormatError("baseline.baseline_id must be a string")
    expected = _require_mapping(_get_required(baseline, "expected", "baseline"), "baseline.expected")
    _require_mapping(_get_required(expected, "invariants", "baseline.expected"), "baseline.expected.invariants")
    _require_mapping(_get_required(expected, "metrics", "baseline.expected"), "baseline.expected.metrics")
    if "selection" in expected:
        _require_mapping(expected["selection"], "baseline.expected.selection")
    if "artifacts" in expected:
        _require_mapping(expected["artifacts"], "baseline.expected.artifacts")
    _require_mapping(_get_required(baseline, "tolerances", "baseline"), "baseline.tolerances")
    _require_mapping(_get_required(baseline, "metadata", "baseline"), "baseline.metadata")


def validate_actual_payload(actual: Mapping[str, Any]) -> None:
    schema_version = _get_required(actual, "schema_version", "actual")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise BaselineFormatError(
            f"Unsupported actual schema_version {schema_version!r}; expected {SUPPORTED_SCHEMA_VERSION!r}"
        )
    if not isinstance(_get_required(actual, "baseline_id", "actual"), str):
        raise BaselineFormatError("actual.baseline_id must be a string")
    result = _require_mapping(_get_required(actual, "result", "actual"), "actual.result")
    _require_mapping(_get_required(result, "invariants", "actual.result"), "actual.result.invariants")
    _require_mapping(_get_required(result, "metrics", "actual.result"), "actual.result.metrics")
    if "selection" in result:
        _require_mapping(result["selection"], "actual.result.selection")
    if "artifacts" in result:
        _require_mapping(result["artifacts"], "actual.result.artifacts")
    _require_mapping(_get_required(actual, "metadata", "actual"), "actual.metadata")


def compare_payloads(baseline: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    validate_baseline_payload(baseline)
    validate_actual_payload(actual)

    failures: list[Failure] = []
    if baseline["baseline_id"] != actual["baseline_id"]:
        failures.append(Failure("baseline_id", "Baseline identifier mismatch", baseline["baseline_id"], actual["baseline_id"]))

    baseline_metadata = baseline["metadata"]
    actual_metadata = actual["metadata"]
    for required_metadata_key in ("component", "fixture_id"):
        if required_metadata_key in baseline_metadata:
            _compare_exact(
                baseline_metadata[required_metadata_key],
                actual_metadata.get(required_metadata_key),
                f"metadata.{required_metadata_key}",
                failures,
            )

    expected = baseline["expected"]
    result = actual["result"]
    tolerances = baseline["tolerances"]
    _compare_exact(expected["invariants"], result["invariants"], "invariants", failures)
    _compare_metrics(expected["metrics"], result["metrics"], tolerances, failures)

    if "selection" in expected:
        actual_selection = result.get("selection")
        if not isinstance(actual_selection, Mapping):
            failures.append(Failure("selection", "Missing selection payload", expected["selection"], actual_selection))
        else:
            _compare_selection(expected["selection"], actual_selection, tolerances, failures)

    if "artifacts" in expected:
        actual_artifacts = result.get("artifacts")
        if not isinstance(actual_artifacts, Mapping):
            failures.append(Failure("artifacts", "Missing artifact manifest", expected["artifacts"], actual_artifacts))
        else:
            _compare_exact(expected["artifacts"], actual_artifacts, "artifacts", failures)

    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "baseline_id": baseline["baseline_id"],
        "status": "PASS" if not failures else "FAIL",
        "failure_count": len(failures),
        "failures": [failure.as_dict() for failure in failures],
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_junit(path: Path, report: Mapping[str, Any]) -> None:
    suite = ET.Element("testsuite", name="golden-regression", tests="1", failures=str(report["failure_count"]))
    case = ET.SubElement(suite, "testcase", classname="regression", name=str(report["baseline_id"]))
    if report["status"] != "PASS":
        failure = ET.SubElement(case, "failure", message=f"{report['failure_count']} regression failure(s)")
        failure.text = json.dumps(report["failures"], ensure_ascii=False, indent=2)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and compare versioned golden-regression artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a baseline or actual JSON contract.")
    validate_parser.add_argument("--baseline", type=Path)
    validate_parser.add_argument("--actual", type=Path)

    compare_parser = subparsers.add_parser("compare", help="Compare actual output against an approved baseline.")
    compare_parser.add_argument("--baseline", required=True, type=Path)
    compare_parser.add_argument("--actual", required=True, type=Path)
    compare_parser.add_argument("--report", type=Path)
    compare_parser.add_argument("--junit", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "validate":
            if bool(args.baseline) == bool(args.actual):
                raise BaselineFormatError("Specify exactly one of --baseline or --actual")
            if args.baseline:
                validate_baseline_payload(_load_json(args.baseline))
                print(f"VALID baseline: {args.baseline}")
            else:
                validate_actual_payload(_load_json(args.actual))
                print(f"VALID actual: {args.actual}")
            return 0

        baseline = _load_json(args.baseline)
        actual = _load_json(args.actual)
        report = compare_payloads(baseline, actual)
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        if args.report:
            _write_json(args.report, report)
        if args.junit:
            args.junit.parent.mkdir(parents=True, exist_ok=True)
            _write_junit(args.junit, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    except BaselineFormatError as exc:
        print(f"REGRESSION CONTRACT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
