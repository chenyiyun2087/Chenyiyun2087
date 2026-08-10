#!/usr/bin/env python3
"""Append-only multi-experiment ledger for Wave 4.

The registry is intentionally an explicit-output interface.  Constructing a
runner or validating a record never creates an ``exports/`` file.  A caller
must pass a path to ``append`` (usually a reviewer's temporary evidence
directory) before any JSONL is written.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.research.advanced_statistical_validation import benjamini_hochberg
from scripts.research.research_preregistration import (
    CONSUMED_DEVELOPMENT_SAMPLE,
    SCHEMA_VERSION as PREREG_SCHEMA_VERSION,
    sha256_payload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "experiments" / "experiment_registry.jsonl"
REGISTRY_SCHEMA_VERSION = "experiment_registry_v1"
REQUIRED_FIELDS: tuple[str, ...] = (
    "hypothesis_id",
    "strategy_id",
    "dataset_status",
    "params_hash",
    "tests",
    "p_raw",
    "p_adjusted",
    "decision",
)
ALLOWED_DECISIONS = {"OBSERVE", "BLOCKED", "REJECTED", "DIAGNOSTIC_ONLY", "FORMAL_PENDING"}
CONSUMED_STATUSES = {
    CONSUMED_DEVELOPMENT_SAMPLE,
    "CONSUMED_HISTORICAL_HOLDOUT",
    "ENGINEERING_SOAK",
    "ENGINEERING_SOAK_LEGACY",
}


class RegistryError(ValueError):
    """Raised when an append-only registry operation would be unsafe."""


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def params_hash(params: Mapping[str, Any] | Any) -> str:
    """Stable hash for the exact pre-registered parameter payload."""

    return sha256_payload(params)


def _p_value(value: Any, field: str) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{field}_invalid") from exc
    if not 0.0 <= result <= 1.0:
        raise RegistryError(f"{field}_outside_0_1")
    return result


def validate_record(record: Mapping[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Validate one ledger row and enforce consumed-sample semantics."""

    try:
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise RegistryError("registry_fields_missing:" + ",".join(missing))
        hypothesis_id = str(record.get("hypothesis_id") or "").strip()
        strategy_id = str(record.get("strategy_id") or "").strip()
        if not hypothesis_id or not strategy_id:
            raise RegistryError("registry_identity_missing")
        dataset_status = str(record.get("dataset_status") or "").strip()
        if not dataset_status:
            raise RegistryError("dataset_status_missing")
        sample_status = str(record.get("sample_status") or dataset_status)
        oos_flag = bool(record.get("independent_oos", record.get("is_oos", False)))
        if dataset_status in CONSUMED_STATUSES or sample_status in CONSUMED_STATUSES:
            if oos_flag or "OOS" in dataset_status.upper() or "OUT_OF_SAMPLE" in dataset_status.upper():
                raise RegistryError("consumed_sample_cannot_be_independent_oos")
        raw_hash = str(record.get("params_hash") or "")
        if len(raw_hash) != 64 or any(character not in "0123456789abcdefABCDEF" for character in raw_hash):
            raise RegistryError("params_hash_must_be_sha256")
        tests = record.get("tests")
        if not isinstance(tests, (list, tuple, Mapping)) or len(tests) == 0:
            raise RegistryError("tests_missing")
        p_raw = _p_value(record.get("p_raw"), "p_raw")
        p_adjusted = _p_value(record.get("p_adjusted"), "p_adjusted")
        decision = str(record.get("decision") or "").upper()
        if decision not in ALLOWED_DECISIONS:
            raise RegistryError(f"decision_invalid:{decision}")
        normalised = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "hypothesis_id": hypothesis_id,
            "strategy_id": strategy_id,
            "dataset_status": dataset_status,
            "sample_status": sample_status,
            "params_hash": raw_hash.lower(),
            "tests": copy.deepcopy(tests),
            "p_raw": p_raw,
            "p_adjusted": p_adjusted,
            "decision": decision,
            "independent_oos": False if dataset_status in CONSUMED_STATUSES or sample_status in CONSUMED_STATUSES else oos_flag,
        }
        # Preserve provenance fields (experiment_id, release_id, hashes of
        # the input package, etc.) while keeping the required fields
        # normalised.  JSONL rows are append-only snapshots, so dropping
        # provenance here would make later audits impossible.
        for key, value in record.items():
            normalised.setdefault(str(key), copy.deepcopy(value))
        return normalised
    except (RegistryError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, RegistryError):
                raise
            raise RegistryError(str(exc)) from exc
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "BLOCKED", "reason": str(exc)}


def make_record(
    *,
    hypothesis_id: str,
    strategy_id: str,
    dataset_status: str,
    params: Mapping[str, Any] | None = None,
    params_hash_value: str | None = None,
    tests: Iterable[str] | Mapping[str, Any],
    p_raw: float | None = None,
    p_adjusted: float | None = None,
    decision: str = "OBSERVE",
    sample_status: str | None = None,
    independent_oos: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """Build and validate a JSONL row without writing it."""

    if params_hash_value is None:
        if params is None:
            raise RegistryError("params_or_params_hash_required")
        params_hash_value = params_hash(params)
    row: dict[str, Any] = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "hypothesis_id": str(hypothesis_id),
        "strategy_id": str(strategy_id),
        "dataset_status": str(dataset_status),
        "sample_status": str(sample_status or dataset_status),
        "params_hash": str(params_hash_value),
        "tests": copy.deepcopy(tests),
        "p_raw": p_raw,
        "p_adjusted": p_adjusted,
        "decision": str(decision).upper(),
        "independent_oos": bool(independent_oos),
    }
    if params is not None:
        row["params"] = copy.deepcopy(dict(params))
    row.update(copy.deepcopy(extra))
    # validate_record returns a normalised copy; preserve explicit provenance
    # fields supplied by the caller in the stored row.
    normalised = validate_record(row)
    for key, value in row.items():
        normalised.setdefault(key, value)
    return normalised


class ExperimentRegistry:
    """A small append-only JSONL registry with duplicate protection."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else None

    @property
    def writable(self) -> bool:
        return self.path is not None

    def records(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RegistryError(f"registry_invalid_jsonl:{line_number}") from exc
            if not isinstance(parsed, Mapping):
                raise RegistryError(f"registry_row_not_mapping:{line_number}")
            records.append(dict(parsed))
        return records

    def validate(self) -> dict[str, Any]:
        try:
            records = self.records()
            seen_hypotheses: set[str] = set()
            seen_experiments: set[str] = set()
            for index, row in enumerate(records):
                validate_record(row)
                hypothesis = str(row["hypothesis_id"])
                experiment = str(row.get("experiment_id") or "")
                if hypothesis in seen_hypotheses:
                    raise RegistryError(f"duplicate_hypothesis_id:{hypothesis}")
                if experiment and experiment in seen_experiments:
                    raise RegistryError(f"duplicate_experiment_id:{experiment}")
                seen_hypotheses.add(hypothesis)
                if experiment:
                    seen_experiments.add(experiment)
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "PASS", "rows": len(records)}
        except RegistryError as exc:
            return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "BLOCKED", "reason": str(exc)}

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and append one row; never rewrite existing bytes."""

        if self.path is None:
            raise RegistryError("explicit_output_path_required")
        row = validate_record(record)
        existing = self.records()
        existing_hypotheses = {str(item.get("hypothesis_id")) for item in existing}
        existing_experiments = {str(item.get("experiment_id")) for item in existing if item.get("experiment_id")}
        if row["hypothesis_id"] in existing_hypotheses:
            raise RegistryError(f"duplicate_hypothesis_id:{row['hypothesis_id']}")
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id and experiment_id in existing_experiments:
            raise RegistryError(f"duplicate_experiment_id:{experiment_id}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ``a`` is append-only at the filesystem level; no truncate/writeback
        # path is used.  A newline is part of the canonical record framing.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(row) + "\n")
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "APPENDED", "row": row, "path": str(self.path)}


def append_experiment(record: Mapping[str, Any], output: str | Path | None = None) -> dict[str, Any]:
    """Validate a row; write only when an explicit output path is supplied."""

    row = validate_record(record)
    if output is None:
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "status": "VALIDATED_NO_WRITE",
            "row": row,
            "write_performed": False,
        }
    return ExperimentRegistry(output).append(row)


def adjust_pvalues(
    records: Sequence[Mapping[str, Any]], *, alpha: float = 0.05
) -> dict[str, Any]:
    """Return an in-memory BH/FDR update; the registry remains append-only."""

    rows = [validate_record(row) for row in records]
    labels = [str(row["hypothesis_id"]) for row in rows]
    available = [row["p_raw"] for row in rows]
    if any(value is None for value in available):
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "BLOCKED", "reason": "p_raw_missing"}
    corrected = benjamini_hochberg(dict(zip(labels, available)), alpha=alpha)
    updated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["p_adjusted"] = corrected["tests"][row["hypothesis_id"]]["p_adjusted"]
        updated.append(item)
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "status": "PASS", "alpha": float(alpha), "records": updated}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    registry = ExperimentRegistry(args.registry)
    result = registry.validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "PROJECT_ROOT", "DEFAULT_REGISTRY_PATH", "REGISTRY_SCHEMA_VERSION", "REQUIRED_FIELDS",
    "RegistryError", "params_hash", "validate_record", "make_record", "ExperimentRegistry",
    "append_experiment", "adjust_pvalues",
]
