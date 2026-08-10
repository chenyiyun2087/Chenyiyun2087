#!/usr/bin/env python3
"""Pre-registration contracts for Wave 4 research.

This module is deliberately small and dependency-light so that a research
plan can be validated before a database, backtest or export is touched.  A
pre-registration is an immutable *plan*, not evidence of a successful
strategy.  In particular, the 2022--2026-08-09 sample is marked as consumed
and can never be re-labelled as independent OOS by this module.

The sealing helpers are pure functions.  They calculate a hash for a future
seal, but do not create a formal seal artifact in this wave.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORWARD_EPOCHS = PROJECT_ROOT / "config" / "forward_epochs.yaml"
SCHEMA_VERSION = "research_preregistration_v1"
CONSUMED_DEVELOPMENT_SAMPLE = "CONSUMED_DEVELOPMENT_SAMPLE"
FORMAL_BLIND = "FORMAL_BLIND"
FORMAL_EPOCH_MIN_START = "2026-08-10"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The project has deliberately used this span for development/engineering
# checks.  It is a consumed sample even if a local data package happens to
# contain fewer/more rows.
CONSUMED_SAMPLE_START = "2022-01-01"
CONSUMED_SAMPLE_END = "2026-08-09"

REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "hypothesis_id",
    "strategy_id",
    "formula",
    "direction",
    "availability",
    "universe",
    "hold_period",
    "top_n",
    "risk_constraints",
    "cost_model",
    "benchmark",
    "tests",
    "failure_conditions",
    "code_path",
    "definition_path",
    "code_hash",
    "config_hash",
    "sample_policy",
)


class PreregistrationError(ValueError):
    """Raised when a pre-registration is incomplete or has drifted."""


class PreregistrationDriftError(PreregistrationError):
    """Raised when immutable pre-registration fields changed."""


def canonical_json(payload: Any) -> bytes:
    """Return stable JSON bytes used for all hashes in this module."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_paths(paths: Sequence[str | Path]) -> str:
    """Hash a deterministic list of files without reading a directory glob."""

    entries: list[dict[str, str]] = []
    for item in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        if not item.exists() or not item.is_file():
            raise FileNotFoundError(str(item))
        entries.append({"path": str(item), "sha256": sha256_file(item)})
    return sha256_payload(entries)


def _date(value: Any, field: str) -> str:
    if value is None or str(value).strip() == "":
        raise PreregistrationError(f"{field}_missing")
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise PreregistrationError(f"{field}_invalid") from exc


def _hash_field(value: Any, field: str) -> str:
    if isinstance(value, Mapping):
        # Accept the descriptive shape {sha256: ...} while normalising the
        # card to a simple hash string for downstream ledgers.
        value = value.get("sha256") or value.get("hash")
    value = str(value or "").lower()
    if not _SHA256_RE.fullmatch(value):
        raise PreregistrationError(f"{field}_must_be_sha256")
    return value


def consumed_sample_policy() -> dict[str, Any]:
    """Return the non-overridable 2022--2026-08-09 development policy."""

    return {
        "status": CONSUMED_DEVELOPMENT_SAMPLE,
        "start": CONSUMED_SAMPLE_START,
        "end": CONSUMED_SAMPLE_END,
        "selection_allowed": False,
        "independent_oos": False,
        "reporting": "DIAGNOSTIC_ONLY",
        "reason": "2022-08-09 development observations are consumed and cannot be independent OOS",
    }


def build_preregistration_card(
    *,
    hypothesis_id: str,
    strategy_id: str,
    formula: str,
    direction: str,
    availability: str | Mapping[str, Any],
    universe: str | Mapping[str, Any],
    hold_period: int | str,
    top_n: int,
    risk_constraints: Mapping[str, Any],
    cost_model: Mapping[str, Any],
    benchmark: str | Mapping[str, Any],
    tests: Sequence[str] | Mapping[str, Any],
    failure_conditions: Sequence[str],
    code_hash: str,
    config_hash: str,
    code_path: str | None = None,
    definition_path: str | None = None,
    experiment_id: str | None = None,
    created_at: str = "2026-08-10",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete card with an explicit consumed-sample policy.

    Callers must provide hashes; silently hashing the current working tree
    would make it too easy to alter a plan after the fact.
    """

    if not str(hypothesis_id).strip() or not str(strategy_id).strip():
        raise PreregistrationError("identity_missing")
    if int(top_n) <= 0:
        raise PreregistrationError("top_n_invalid")
    if not isinstance(risk_constraints, Mapping) or not risk_constraints:
        raise PreregistrationError("risk_constraints_missing")
    if not isinstance(cost_model, Mapping) or not cost_model:
        raise PreregistrationError("cost_model_missing")
    if not failure_conditions:
        raise PreregistrationError("failure_conditions_missing")
    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "hypothesis_id": str(hypothesis_id),
        "strategy_id": str(strategy_id),
        "experiment_id": str(experiment_id or f"exp_{strategy_id}"),
        "created_at": _date(created_at, "created_at"),
        "formula": str(formula),
        "direction": str(direction),
        "availability": copy.deepcopy(availability),
        "universe": copy.deepcopy(universe),
        "hold_period": hold_period,
        "top_n": int(top_n),
        "risk_constraints": copy.deepcopy(dict(risk_constraints)),
        "cost_model": copy.deepcopy(dict(cost_model)),
        "benchmark": copy.deepcopy(benchmark),
        "tests": copy.deepcopy(tests),
        "failure_conditions": [str(x) for x in failure_conditions],
        "code_path": str(code_path or f"scripts/research/{strategy_id}.py"),
        "definition_path": str(definition_path or f"config/strategy_definitions/{strategy_id}.yaml"),
        "code_hash": _hash_field(code_hash, "code_hash"),
        "config_hash": _hash_field(config_hash, "config_hash"),
        "sample_policy": consumed_sample_policy(),
        "status": "PRE_REGISTERED",
        "promotion_status": "BLOCKED",
        "capital_cny": 0.0,
    }
    if extra:
        # Extra keys are useful for implementation-specific diagnostics, but
        # cannot replace any required field or the sample policy.
        for key, value in extra.items():
            if key in {"sample_policy", "code_hash", "config_hash"}:
                continue
            card[key] = copy.deepcopy(value)
    validate_preregistration(card)
    return card


def validate_preregistration(card: Mapping[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Validate a card and return a schema/status report.

    Validation is fail-closed: malformed cards raise ``PreregistrationError``
    by default.  ``strict=False`` returns a ``BLOCKED`` report instead, which
    is convenient for CLI runners.
    """

    try:
        missing = [field for field in REQUIRED_FIELDS if field not in card]
        if missing:
            raise PreregistrationError("required_fields_missing:" + ",".join(missing))
        if str(card.get("schema_version")) != SCHEMA_VERSION:
            raise PreregistrationError("schema_version_invalid")
        _date(card.get("created_at"), "created_at")
        if not str(card.get("code_path") or "").strip():
            raise PreregistrationError("code_path_missing")
        if not str(card.get("definition_path") or "").strip():
            raise PreregistrationError("definition_path_missing")
        _hash_field(card.get("code_hash"), "code_hash")
        _hash_field(card.get("config_hash"), "config_hash")
        if not str(card.get("formula") or "").strip():
            raise PreregistrationError("formula_missing")
        if not str(card.get("direction") or "").strip():
            raise PreregistrationError("direction_missing")
        if int(card.get("top_n", 0)) <= 0:
            raise PreregistrationError("top_n_invalid")
        sample = card.get("sample_policy")
        if not isinstance(sample, Mapping):
            raise PreregistrationError("sample_policy_missing")
        if str(sample.get("status")) != CONSUMED_DEVELOPMENT_SAMPLE:
            raise PreregistrationError("consumed_sample_status_required")
        if _date(sample.get("start"), "sample_policy.start") != CONSUMED_SAMPLE_START:
            raise PreregistrationError("consumed_sample_start_invalid")
        if _date(sample.get("end"), "sample_policy.end") != CONSUMED_SAMPLE_END:
            raise PreregistrationError("consumed_sample_end_invalid")
        if bool(sample.get("independent_oos", True)):
            raise PreregistrationError("consumed_sample_cannot_be_oos")
        if bool(sample.get("selection_allowed", True)):
            raise PreregistrationError("consumed_sample_selection_forbidden")
        if str(card.get("promotion_status", "BLOCKED")) not in {"BLOCKED", "RESEARCH_ONLY"}:
            raise PreregistrationError("promotion_status_must_be_blocked")
        if float(card.get("capital_cny", 0.0)) != 0.0:
            raise PreregistrationError("capital_must_be_zero")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "hypothesis_id": str(card["hypothesis_id"]),
            "strategy_id": str(card["strategy_id"]),
            "sample_status": CONSUMED_DEVELOPMENT_SAMPLE,
            "independent_oos": False,
        }
    except (PreregistrationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, PreregistrationError):
                raise
            raise PreregistrationError(str(exc)) from exc
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "reason": str(exc),
        }


def assert_valid_preregistration(card: Mapping[str, Any]) -> None:
    validate_preregistration(card, strict=True)


def _immutable_view(card: Mapping[str, Any]) -> dict[str, Any]:
    """Fields covered by the pre-registration seal (status is not mutable)."""

    return {
        key: copy.deepcopy(value)
        for key, value in card.items()
        if key not in {"seal_sha256", "sealed_at", "status"}
    }


def preregistration_hash(card: Mapping[str, Any]) -> str:
    """Return the pure hash that a future formal seal would bind."""

    validate_preregistration(card)
    return sha256_payload(_immutable_view(card))


def seal_preregistration(card: Mapping[str, Any]) -> dict[str, Any]:
    """Return a seal preview without writing a seal artifact."""

    return {
        "status": "SEAL_PREVIEW_ONLY",
        "seal_sha256": preregistration_hash(card),
        "formal_artifact_created": False,
    }


def verify_preregistration_immutable(
    original: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify that candidate has the exact same pre-registered parameters."""

    validate_preregistration(original)
    validate_preregistration(candidate)
    expected = preregistration_hash(original)
    actual = preregistration_hash(candidate)
    if expected != actual:
        raise PreregistrationDriftError(
            f"preregistration_drift: expected {expected}, got {actual}"
        )
    return {"status": "PASS", "seal_sha256": expected, "immutable": True}


def _resolve_source_path(value: str | Path, *, card_path: str | Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    project_path = PROJECT_ROOT / path
    if project_path.exists():
        return project_path
    if card_path is not None:
        relative_path = Path(card_path).resolve().parent / path
        if relative_path.exists():
            return relative_path
    return project_path


def canonical_definition_hash(path: str | Path) -> str:
    """Hash a definition's canonical YAML, excluding its own hash fields."""

    definition_path = Path(path)
    if not definition_path.exists():
        raise PreregistrationError(f"definition_missing:{definition_path}")
    raw = yaml.safe_load(definition_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise PreregistrationError("definition_root_not_mapping")
    payload = {key: copy.deepcopy(value) for key, value in raw.items() if key not in {"code_hash", "config_hash"}}
    return sha256_payload(payload)


def verify_preregistration_source_bindings(
    card: Mapping[str, Any], *, card_path: str | Path | None = None, strict: bool = True
) -> dict[str, Any]:
    """Verify code/config hashes against the exact registered source files."""

    try:
        validate_preregistration(card)
        code_path = _resolve_source_path(str(card["code_path"]), card_path=card_path)
        definition_path = _resolve_source_path(str(card["definition_path"]), card_path=card_path)
        if not code_path.exists() or not code_path.is_file():
            raise PreregistrationDriftError(f"code_source_missing:{code_path}")
        actual_code_hash = sha256_file(code_path)
        actual_config_hash = canonical_definition_hash(definition_path)
        expected_code_hash = _hash_field(card["code_hash"], "code_hash")
        expected_config_hash = _hash_field(card["config_hash"], "config_hash")
        if actual_code_hash != expected_code_hash:
            raise PreregistrationDriftError(
                f"code_hash_drift: expected {expected_code_hash}, got {actual_code_hash}"
            )
        if actual_config_hash != expected_config_hash:
            raise PreregistrationDriftError(
                f"config_hash_drift: expected {expected_config_hash}, got {actual_config_hash}"
            )
        return {
            "status": "PASS",
            "code_path": str(code_path),
            "definition_path": str(definition_path),
            "code_hash": actual_code_hash,
            "config_hash": actual_config_hash,
        }
    except (PreregistrationError, TypeError, ValueError) as exc:
        if strict:
            if isinstance(exc, PreregistrationError):
                raise
            raise PreregistrationDriftError(str(exc)) from exc
        return {"status": "BLOCKED", "reason": str(exc)}


def validate_formal_evidence(
    *,
    pit_qualifier: Mapping[str, Any] | None,
    forward_evidence: Mapping[str, Any] | None,
    formal_epoch: Mapping[str, Any] | None,
    returns_dates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Require an independent E3 PIT qualifier and bound forward evidence."""

    try:
        if not isinstance(pit_qualifier, Mapping):
            raise PreregistrationError("pit_qualifier_required")
        component = str(pit_qualifier.get("component") or "").strip()
        pit_status = str(pit_qualifier.get("status") or "").upper()
        qualified_level = str(
            pit_qualifier.get("qualified_evidence_level")
            or pit_qualifier.get("evidence_level")
            or ""
        ).upper()
        pit_hash = pit_qualifier.get("content_sha256") or pit_qualifier.get("content_hash")
        if not component or pit_status not in {"PASS", "QUALIFIED", "DATA_E3_QUALIFIED", "E3_QUALIFIED"}:
            raise PreregistrationError("pit_qualifier_not_qualified")
        if qualified_level not in {"E3", "DATA_E3_QUALIFIED", "E3_QUALIFIED"}:
            raise PreregistrationError("pit_qualifier_e3_required")
        pit_hash = _hash_field(pit_hash, "pit_content_hash")
        if not isinstance(forward_evidence, Mapping):
            raise PreregistrationError("forward_evidence_required")
        if not isinstance(formal_epoch, Mapping) or not formal_epoch.get("epoch_id") or not formal_epoch.get("start"):
            raise PreregistrationError("formal_epoch_required")
        epoch_id = str(formal_epoch["epoch_id"])
        if str(forward_evidence.get("epoch_id") or "") != epoch_id:
            raise PreregistrationError("forward_epoch_id_mismatch")
        forward_start = _date(
            forward_evidence.get("start")
            or forward_evidence.get("epoch_start")
            or forward_evidence.get("signal_start")
            or formal_epoch["start"],
            "forward_evidence.start",
        )
        epoch_start = _date(formal_epoch["start"], "formal_epoch.start")
        if forward_start < epoch_start:
            raise PreregistrationError("forward_evidence_before_epoch")
        package_hash = (
            forward_evidence.get("evidence_sha256")
            or forward_evidence.get("package_sha256")
            or forward_evidence.get("content_sha256")
        )
        package_hashes = forward_evidence.get("signal_package_sha256") or forward_evidence.get("package_sha256_list")
        if package_hash is not None:
            package_hash = _hash_field(package_hash, "forward_evidence_hash")
        elif package_hashes:
            if not isinstance(package_hashes, (list, tuple)):
                raise PreregistrationError("forward_package_hashes_invalid")
            package_hash = [_hash_field(value, "forward_package_hash") for value in package_hashes]
        else:
            raise PreregistrationError("forward_evidence_hash_missing")
        observed_dates = list(
            returns_dates
            or forward_evidence.get("returns_dates")
            or forward_evidence.get("signal_dates")
            or forward_evidence.get("package_dates")
            or forward_evidence.get("dates")
            or []
        )
        if not observed_dates:
            raise PreregistrationError("forward_returns_dates_required")
        normalized_dates = [_date(value, "forward_returns_date") for value in observed_dates]
        if any(value < epoch_start for value in normalized_dates):
            raise PreregistrationError("forward_returns_before_epoch")
        return {
            "status": "PASS",
            "evidence_level": "E3",
            "pit_component": component,
            "pit_content_sha256": pit_hash,
            "epoch_id": epoch_id,
            "epoch_start": epoch_start,
            "forward_evidence_sha256": package_hash,
            "returns_dates": normalized_dates,
        }
    except (PreregistrationError, TypeError, ValueError) as exc:
        return {"status": "BLOCKED", "evidence_level": "E0", "reason": str(exc)}


# Compatibility spellings for small research notebooks.  They all resolve to
# the same fail-closed implementation above; no mutable global state is
# introduced by these aliases.
create_preregistration_card = build_preregistration_card
validate_card = validate_preregistration
validate_card_schema = validate_preregistration
compute_preregistration_hash = preregistration_hash
compute_seal_hash = preregistration_hash
seal_preview = seal_preregistration


def load_preregistration(path: str | Path, *, validate: bool = True) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise PreregistrationError("preregistration_root_not_mapping")
    card = dict(raw)
    if validate:
        validate_preregistration(card)
    return card


def formal_blind_epoch_status(path: str | Path = DEFAULT_FORWARD_EPOCHS) -> dict[str, Any]:
    """Inspect the canonical forward-epoch manifest without promoting soak."""

    path = Path(path)
    if not path.exists():
        return {"status": "BLOCKED_FORWARD_EVIDENCE", "reason": "forward_epochs_missing", "formal_epoch": None}
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive for CLI use
        return {"status": "BLOCKED_FORWARD_EVIDENCE", "reason": f"forward_epochs_invalid:{exc}", "formal_epoch": None}
    epochs = manifest.get("epochs") or []
    formal = [
        epoch for epoch in epochs
        if str(epoch.get("kind")) == FORMAL_BLIND
        and str(epoch.get("status")) in {"FROZEN", "ACTIVE", "ACCUMULATING"}
        and epoch.get("start")
        and str(epoch.get("start")) >= FORMAL_EPOCH_MIN_START
    ]
    if not formal:
        return {
            "status": "BLOCKED_FORWARD_EVIDENCE",
            "reason": "no_future_formal_blind_epoch",
            "formal_epoch": None,
            "active_epoch_id": manifest.get("active_epoch_id"),
        }
    selected = sorted(formal, key=lambda e: str(e.get("start")))[-1]
    return {"status": "FORMAL_BLIND_AVAILABLE", "formal_epoch": copy.deepcopy(selected)}


def sample_status_for_window(start: str, end: str) -> dict[str, Any]:
    """Classify a window; 2022--2026 is never returned as independent OOS."""

    start_n, end_n = _date(start, "start"), _date(end, "end")
    if start_n <= CONSUMED_SAMPLE_END and end_n >= CONSUMED_SAMPLE_START:
        return {
            "status": CONSUMED_DEVELOPMENT_SAMPLE,
            "independent_oos": False,
            "selection_allowed": False,
        }
    return {"status": "UNCLASSIFIED", "independent_oos": False, "selection_allowed": False}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--forward-epochs", type=Path, default=DEFAULT_FORWARD_EPOCHS)
    parser.add_argument("--seal-preview", action="store_true")
    args = parser.parse_args()
    try:
        card = load_preregistration(args.config)
        result: dict[str, Any] = validate_preregistration(card)
        if args.seal_preview:
            result.update(seal_preregistration(card))
        result["forward_epoch"] = formal_blind_epoch_status(args.forward_epochs)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (FileNotFoundError, PreregistrationError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


__all__ = [
    "SCHEMA_VERSION", "CONSUMED_DEVELOPMENT_SAMPLE", "CONSUMED_SAMPLE_START",
    "CONSUMED_SAMPLE_END", "FORMAL_EPOCH_MIN_START", "REQUIRED_FIELDS", "PreregistrationError",
    "PreregistrationDriftError", "canonical_json", "sha256_payload", "sha256_file",
    "hash_paths", "consumed_sample_policy", "build_preregistration_card",
    "validate_preregistration", "assert_valid_preregistration", "preregistration_hash",
    "seal_preregistration", "verify_preregistration_immutable", "load_preregistration",
    "create_preregistration_card", "validate_card", "validate_card_schema",
    "compute_preregistration_hash", "compute_seal_hash", "seal_preview",
    "canonical_definition_hash", "verify_preregistration_source_bindings",
    "validate_formal_evidence", "formal_blind_epoch_status", "sample_status_for_window",
]
