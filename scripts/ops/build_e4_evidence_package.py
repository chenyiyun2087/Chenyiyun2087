#!/usr/bin/env python3
"""Build a fail-closed, review-only E4 evidence package.

Every source is validated before an economic count is emitted.  In
particular, a path merely existing, an arbitrary completed-round-trip field,
or a missing reconciliation field can never produce an economic PASS.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from runtime.epoch_governance import canonical_sha
from runtime.formal_status_semantics import (
    ArtifactStatus,
    ContractStatus,
    GateEconomicStatus,
    make_gate_status,
)


_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA_RE.fullmatch(value))


def _sha_path(path: Path | str | None) -> str | None:
    """Hash a readable file/tree, returning None for any path failure."""

    if path is None:
        return None
    try:
        source = Path(path)
        if not source.exists():
            return None
        digest = hashlib.sha256()
        if source.is_file():
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif source.is_dir():
            children = sorted(child for child in source.rglob("*") if child.is_file())
            if not children:
                return None
            for child in children:
                digest.update(str(child.relative_to(source)).encode())
                child_sha = _sha_path(child)
                if not child_sha:
                    return None
                digest.update(child_sha.encode())
        else:
            return None
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _read_path(path: Path | str | None) -> tuple[Any, str | None, str | None]:
    """Return (parsed, sha, error), never raising on input evidence."""

    if path is None:
        return None, None, "PATH_MISSING"
    source = Path(path)
    sha = _sha_path(source)
    if not sha:
        return None, None, "PATH_MISSING_OR_UNHASHABLE"
    try:
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".json":
            return json.loads(text), sha, None
        if source.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(text), sha, None
        if source.suffix.lower() == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            return rows, sha, None
        # Event ledgers are JSONL.  A non-JSONL text artifact is not a valid
        # structured E4 source, even though it is hashable.
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return rows, sha, None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return None, sha, f"READ_ERROR:{type(exc).__name__}"


def _canonical_object_sha(value: object) -> str | None:
    if value is None:
        return None
    return canonical_sha(value)


def _epoch(payload: Mapping[str, Any] | Path | str) -> tuple[dict[str, Any], str | None, list[str]]:
    """Load one formal epoch and verify immutable/self-hash invariants."""

    errors: list[str] = []
    manifest_sha: str | None = None
    if isinstance(payload, (Path, str)):
        raw, manifest_sha, error = _read_path(payload)
        if error or not isinstance(raw, Mapping):
            return {}, manifest_sha, [error or "EPOCH_NOT_MAPPING"]
        raw = dict(raw)
    else:
        raw = dict(payload)
    # A config manifest may contain a list of legacy/soak epochs.  Exactly one
    # explicit formal epoch is required for E4.
    if isinstance(raw.get("epochs"), list):
        formal = [item for item in raw["epochs"] if isinstance(item, Mapping) and item.get("kind") == "FORMAL_BLIND"]
        if len(formal) != 1:
            return {}, manifest_sha, ["FORMAL_EPOCH_NOT_UNIQUE"]
        raw = dict(formal[0])
    if str(raw.get("kind") or "") != "FORMAL_BLIND":
        errors.append("EPOCH_NOT_FORMAL")
    if raw.get("immutable") is not True:
        errors.append("EPOCH_NOT_IMMUTABLE")
    if not raw.get("epoch_id"):
        errors.append("EPOCH_ID_MISSING")
    if not raw.get("start"):
        errors.append("EPOCH_START_MISSING")
    declared = raw.get("manifest_sha256")
    unsigned = {key: value for key, value in raw.items() if key != "manifest_sha256"}
    if not _is_sha(declared):
        errors.append("EPOCH_MANIFEST_SHA_MISSING")
    elif canonical_sha(unsigned) != str(declared).lower():
        errors.append("EPOCH_MANIFEST_SHA_MISMATCH")
    return raw, manifest_sha or _canonical_object_sha(raw), errors


def _package_records(signal_packages: Iterable[Path | str | Mapping[str, Any]] | Path | str | None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if signal_packages is None:
        return [], ["SIGNAL_PACKAGES_REQUIRED"]
    if isinstance(signal_packages, (Path, str)):
        root = Path(signal_packages)
        if root.is_dir() and not (root / "signal_package_manifest.json").exists():
            signal_packages = sorted(path.parent for path in root.rglob("signal_package_manifest.json"))
        else:
            signal_packages = [root]
    for item in signal_packages:
        if isinstance(item, Mapping):
            row = dict(item)
            row["package_sha256"] = row.get("package_sha256") or row.get("package_sha") or row.get("signal_package_sha256")
            row["source_sha256"] = _canonical_object_sha(row)
        else:
            source = Path(item)
            source_tree_sha = _sha_path(source)
            if not source_tree_sha:
                errors.append(f"PACKAGE_PATH_UNHASHABLE:{source}")
            manifest_path = source / "signal_package_manifest.json" if source.is_dir() else source
            payload, source_sha, error = _read_path(manifest_path)
            if error or not isinstance(payload, Mapping):
                errors.append(f"PACKAGE_MANIFEST_INVALID:{source}")
                continue
            row = dict(payload)
            row["package_path"] = str(source)
            row["source_sha256"] = source_tree_sha or source_sha
            # package_sha256 must be explicitly present in the manifest or
            # package root inventory; deriving it from path existence is not
            # an evidence binding.
            if not row.get("package_sha256"):
                inventory, _, inv_error = _read_path(source / "package_sha256.json")
                if not inv_error and isinstance(inventory, Mapping):
                    row["package_sha256"] = inventory.get("package_sha256")
            if not row.get("package_sha256"):
                row["package_sha256"] = row.get("package_sha") or row.get("signal_package_sha256")
        rows.append(row)
    return rows, errors


def _calendar_dates(
    *,
    sse_calendar: Path | str | Iterable[str] | Mapping[str, Any] | None,
    trading_dates: Iterable[str] | Path | str | None,
) -> tuple[set[str], list[str]]:
    """Resolve explicit SSE dates; no weekday/date fabrication is allowed."""

    errors: list[str] = []
    raw: Any = None
    if trading_dates is not None:
        if isinstance(trading_dates, (Path, str)):
            raw, _, error = _read_path(trading_dates)
            if error:
                return set(), ["SSE_CALENDAR_UNREADABLE"]
        else:
            raw = list(trading_dates)
    elif isinstance(sse_calendar, (Path, str)):
        raw, _, error = _read_path(sse_calendar)
        if error:
            return set(), ["SSE_CALENDAR_UNREADABLE"]
    elif isinstance(sse_calendar, Mapping):
        raw = sse_calendar
    elif sse_calendar is not None:
        raw = list(sse_calendar)
    if raw is None:
        return set(), ["SSE_CALENDAR_REQUIRED"]
    if isinstance(raw, Mapping):
        raw = raw.get("trading_dates") or raw.get("open_dates") or raw.get("dates")
    if not isinstance(raw, (list, tuple, set)):
        return set(), ["SSE_CALENDAR_INVALID"]
    dates: list[str] = []
    for value in raw:
        if isinstance(value, Mapping):
            if value.get("is_open") not in (None, 1, True, "1", "true", "True"):
                continue
            value = value.get("trade_date") or value.get("cal_date") or value.get("date")
        text = str(value or "")
        try:
            # Validate ISO dates and reject fabricated/non-date strings.
            import datetime as _dt
            _dt.date.fromisoformat(text)
        except (TypeError, ValueError):
            errors.append(f"SSE_DATE_INVALID:{text}")
            continue
        dates.append(text)
    if len(set(dates)) != len(dates):
        errors.append("SSE_CALENDAR_DUPLICATE_DATE")
    return set(dates), errors


def _ledger_rows(event_ledger: Path | str | Iterable[Mapping[str, Any]] | None) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    if event_ledger is None:
        return [], None, ["EVENT_LEDGER_REQUIRED"]
    if isinstance(event_ledger, (Path, str)):
        source = Path(event_ledger)
        if source.is_dir():
            sha = _sha_path(source)
            if not sha:
                return [], None, ["EVENT_LEDGER_UNREADABLE"]
            rows: list[dict[str, Any]] = []
            try:
                for path in sorted(source.rglob("*.jsonl")):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            value = json.loads(line)
                            if isinstance(value, Mapping):
                                rows.append(dict(value))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                return [], sha, ["EVENT_LEDGER_UNREADABLE"]
            return rows, sha, []
        raw, sha, error = _read_path(source)
        if error or not isinstance(raw, list):
            return [], sha, ["EVENT_LEDGER_UNREADABLE"]
        return [dict(row) for row in raw if isinstance(row, Mapping)], sha, []
    try:
        rows = [dict(row) for row in event_ledger]
    except (TypeError, ValueError):
        return [], None, ["EVENT_LEDGER_INVALID"]
    return rows, _canonical_object_sha(rows), []


def _round_trip_count(rows: list[dict[str, Any]]) -> int:
    """Count only explicit completion markers or verified summaries."""

    seen: set[str] = set()
    count = 0
    for row in rows:
        if row.get("round_trip_completed") is not True:
            continue
        identity = row.get("round_trip_id") or row.get("trade_id") or row.get("order_id")
        key = str(identity) if identity else f"row:{len(seen)}"
        if key not in seen:
            seen.add(key)
            count += 1
    for row in rows:
        verified = (
            row.get("verified_summary") is True
            or row.get("round_trip_summary_verified") is True
            or row.get("summary_verified") is True
            or str(row.get("summary_status") or "").upper() == "VERIFIED"
        )
        if verified:
            try:
                count += int(row.get("completed_round_trips", row.get("round_trips", 0)))
            except (TypeError, ValueError):
                continue
    return count


def _source_payload(
    source: Path | str | Mapping[str, Any] | None,
    *,
    name: str,
    required_fields: tuple[str, ...],
) -> tuple[Mapping[str, Any] | None, str | None, list[str]]:
    if source is None:
        return None, None, [f"{name.upper()}_REQUIRED"]
    if isinstance(source, (Path, str)):
        raw, sha, error = _read_path(source)
        if error or not isinstance(raw, Mapping):
            return None, sha, [f"{name.upper()}_UNREADABLE"]
    elif isinstance(source, Mapping):
        raw = dict(source)
        sha = _canonical_object_sha(raw)
    else:
        return None, None, [f"{name.upper()}_INVALID"]
    errors = [f"{name.upper()}_FIELD_MISSING:{field}" for field in required_fields if field not in raw]
    if not sha:
        errors.append(f"{name.upper()}_SHA_MISSING")
    return raw, sha, errors


def build_e4_evidence_package(
    epoch_manifest: Mapping[str, Any] | Path | str,
    *,
    release_id: str,
    signal_packages: Iterable[Path | str | Mapping[str, Any]],
    event_ledger: Path | str | Iterable[Mapping[str, Any]] | None,
    nav_snapshots: Path | str | Mapping[str, Any] | None,
    reconciliation: Path | str | Mapping[str, Any] | None,
    sse_calendar: Path | str | Iterable[str] | Mapping[str, Any] | None = None,
    trading_dates: Iterable[str] | Path | str | None = None,
) -> dict[str, Any]:
    epoch, epoch_sha, errors = _epoch(epoch_manifest)
    formal = not errors and str(epoch.get("status") or "") in {"FROZEN", "ACTIVE", "ACCUMULATING"}
    epoch_id = str(epoch.get("epoch_id") or "")
    start = str(epoch.get("start") or "")

    calendar, calendar_errors = _calendar_dates(sse_calendar=sse_calendar, trading_dates=trading_dates)
    errors.extend(calendar_errors)
    packages, package_errors = _package_records(signal_packages)
    errors.extend(package_errors)
    if not packages:
        errors.append("SIGNAL_PACKAGES_REQUIRED")
    package_dates: list[str] = []
    for index, package in enumerate(packages):
        package_date = str(package.get("signal_date") or package.get("trade_date") or "")
        package_dates.append(package_date)
        if package.get("package_status") != "SEALED":
            errors.append(f"PACKAGE_NOT_SEALED:{index}")
        if not _is_sha(package.get("package_sha256")):
            errors.append(f"PACKAGE_SHA_MISSING:{index}")
        if str(package.get("epoch_id") or package.get("forward_epoch_id") or "") != epoch_id:
            errors.append(f"PACKAGE_EPOCH_MISMATCH:{index}")
        if not package.get("release_id"):
            errors.append(f"PACKAGE_RELEASE_MISSING:{index}")
        elif str(package.get("release_id")) != release_id:
            errors.append(f"PACKAGE_RELEASE_MISMATCH:{index}")
        if not package_date:
            errors.append(f"PACKAGE_DATE_MISSING:{index}")
        elif package_date not in calendar:
            errors.append(f"PACKAGE_DATE_NOT_SSE:{package_date}")
        elif start and package_date < start:
            errors.append(f"PACKAGE_PRE_EPOCH:{package_date}")
    if len(set(package_dates)) != len(package_dates):
        errors.append("PACKAGE_DATE_DUPLICATE")
    dates = sorted(set(package_dates) & calendar)
    if dates and calendar:
        expected = {value for value in calendar if value >= start and value <= dates[-1]} if start else set(calendar)
        missing_dates = sorted(expected - set(dates))
        if missing_dates:
            errors.append(f"PACKAGE_DATE_MISSING:{missing_dates[:3]}")

    event_rows, event_sha, event_errors = _ledger_rows(event_ledger)
    errors.extend(event_errors)
    round_trips = _round_trip_count(event_rows)
    nav_payload, nav_sha, nav_errors = _source_payload(
        nav_snapshots,
        name="nav",
        required_fields=(),
    )
    errors.extend(nav_errors)
    recon_payload, recon_sha, recon_errors = _source_payload(
        reconciliation,
        name="reconciliation",
        required_fields=("reconciliation_errors", "conservation_errors"),
    )
    errors.extend(recon_errors)
    reconciliation_error_count: int | None = None
    conservation_error_count: int | None = None
    if recon_payload is not None:
        try:
            reconciliation_error_count = int(recon_payload["reconciliation_errors"])
            conservation_error_count = int(recon_payload["conservation_errors"])
        except (KeyError, TypeError, ValueError):
            errors.append("RECONCILIATION_ERROR_FIELDS_INVALID")
    if reconciliation_error_count not in (None, 0):
        errors.append("RECONCILIATION_ERRORS_NONZERO")
    if conservation_error_count not in (None, 0):
        errors.append("CONSERVATION_ERRORS_NONZERO")

    contract_valid = not errors
    complete = bool(
        contract_valid
        and formal
        and len(dates) >= 60
        and round_trips >= 30
        and reconciliation_error_count == 0
        and conservation_error_count == 0
    )
    artifact_status = ArtifactStatus.ARTIFACT_PRESENT if not errors and epoch_sha and event_sha and nav_sha and recon_sha else ArtifactStatus.ARTIFACT_INVALID if any(error.endswith("UNREADABLE") or "MISSING" in error or "REQUIRED" in error for error in errors) else ArtifactStatus.ARTIFACT_PRESENT
    if not epoch_sha or not event_sha or not nav_sha or not recon_sha:
        artifact_status = ArtifactStatus.ARTIFACT_MISSING
    contract_status = ContractStatus.CONTRACT_VALID if contract_valid else ContractStatus.CONTRACT_INVALID
    economic_status = GateEconomicStatus.ECONOMIC_PASS if complete else GateEconomicStatus.ECONOMIC_FAIL if formal and (len(dates) >= 60 or errors) else GateEconomicStatus.ECONOMIC_NOT_EVALUATED
    gate = make_gate_status(
        artifact_status=artifact_status,
        contract_status=contract_status,
        economic_status=economic_status,
        reasons=tuple(dict.fromkeys(errors or (["E4_REQUIREMENTS_ACCUMULATING"] if not complete else []))),
    )
    payload = {
        "schema_version": "e4_evidence_package_v1",
        "release_id": release_id,
        "epoch_id": epoch_id or None,
        "epoch_manifest_sha256": epoch_sha,
        "epoch_start": start or None,
        "signal_package_sha256": [row.get("package_sha256") for row in packages],
        "event_ledger_sha256": event_sha,
        "nav_sha256": nav_sha,
        "reconciliation_sha256": recon_sha,
        "formal_trading_days": len(dates) if formal and contract_valid else 0,
        "completed_round_trips": round_trips if formal and contract_valid else 0,
        "reconciliation_errors": reconciliation_error_count,
        "conservation_errors": conservation_error_count,
        "gate": gate.to_dict(),
        "status": "ECONOMIC_PASS" if complete else "ACCUMULATING",
        "capital_authority": False,
        "allowed_new_capital_cny": 0,
    }
    payload["evidence_sha256"] = canonical_sha(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch-manifest", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--signal-package", type=Path, action="append", default=[])
    parser.add_argument("--event-ledger", type=Path, required=True)
    parser.add_argument("--nav", type=Path, required=True)
    parser.add_argument("--reconciliation", type=Path, required=True)
    calendar_group = parser.add_mutually_exclusive_group(required=True)
    calendar_group.add_argument("--sse-calendar", type=Path)
    calendar_group.add_argument("--trading-dates", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    trading_dates = None
    if args.trading_dates:
        raw_dates = _read_path(args.trading_dates)[0]
        trading_dates = raw_dates if isinstance(raw_dates, list) else None
    payload = build_e4_evidence_package(
        args.epoch_manifest,
        release_id=args.release_id,
        signal_packages=args.signal_package,
        event_ledger=args.event_ledger,
        nav_snapshots=args.nav,
        reconciliation=args.reconciliation,
        sse_calendar=args.sse_calendar,
        trading_dates=trading_dates,
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


build_e4_package = build_e4_evidence_package
build_e4_evidence = build_e4_evidence_package


if __name__ == "__main__":
    raise SystemExit(main())
