"""Canonical forward-epoch and engineering-soak governance.

The active manifest is the only source of forward dates.  Runtime code must
not embed a ``TRUE_BLIND_START`` constant: before an immutable formal epoch is
frozen, all observations remain engineering soak and are invalid for E4 or
selection.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FORWARD_EPOCHS_PATH = PROJECT_ROOT / "config" / "forward_epochs.yaml"
SOAK_REQUIRED_HASHES = ("code", "config", "dependency", "candidate", "stat_plan", "pit_contract")
SOAK_REQUIRED_CHECKS = SOAK_REQUIRED_HASHES
SOAK_REQUIRED_METRICS = (
    "expired_data_count",
    "manual_package_count",
    "ledger_imbalance_count",
    "verifier_blocker_count",
    "duplicate_package_count",
    "missing_package_count",
)
SOAK_TARGET_DAYS = 20
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ForwardEpoch:
    epoch_id: str
    status: str
    kind: str
    start: str | None
    release_id: str | None = None
    seal_sha256: str | None = None
    evidence_sha256: str | None = None
    selection_status: str = "INVALID_FOR_SELECTION"
    e4_status: str = "INVALID_FOR_E4"

    @property
    def formal(self) -> bool:
        return self.kind == "FORMAL_BLIND" and self.status in {"FROZEN", "ACTIVE", "ACCUMULATING"} and bool(self.start)

    @property
    def engineering_soak(self) -> bool:
        return self.kind == "ENGINEERING_SOAK" or self.status == "ENGINEERING_SOAK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "epoch_id": self.epoch_id,
            "status": self.status,
            "kind": self.kind,
            "start": self.start,
            "release_id": self.release_id,
            "seal_sha256": self.seal_sha256,
            "evidence_sha256": self.evidence_sha256,
            "selection_status": self.selection_status,
            "e4_status": self.e4_status,
        }


@dataclass(frozen=True)
class ForwardEpochManifest:
    schema_version: str
    active_epoch_id: str | None
    epochs: tuple[ForwardEpoch, ...]
    source_path: Path | None = None

    @property
    def active_epoch(self) -> ForwardEpoch | None:
        if not self.active_epoch_id:
            return None
        return next((e for e in self.epochs if e.epoch_id == self.active_epoch_id), None)

    @property
    def formal_epoch(self) -> ForwardEpoch | None:
        # There may be an active soak and no formal epoch.  Do not promote the
        # soak implicitly; only an explicitly declared FORMAL_BLIND qualifies.
        for epoch in self.epochs:
            if epoch.formal and epoch.status in {"ACTIVE", "ACCUMULATING", "FROZEN"}:
                return epoch
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_epoch_id": self.active_epoch_id,
            "epochs": [epoch.to_dict() for epoch in self.epochs],
        }


def _as_epoch(raw: Mapping[str, Any]) -> ForwardEpoch:
    epoch_id = str(raw.get("epoch_id") or raw.get("id") or "")
    if not epoch_id:
        raise ValueError("forward_epoch_missing_id")
    start = raw.get("start")
    if start in ("", "null", "None"):
        start = None
    if start is not None:
        date.fromisoformat(str(start))
    kind = str(raw.get("kind") or raw.get("epoch_kind") or "")
    status = str(raw.get("status") or "")
    if not kind:
        kind = "ENGINEERING_SOAK" if "SOAK" in status else "FORMAL_BLIND"
    return ForwardEpoch(
        epoch_id=epoch_id,
        status=status,
        kind=kind,
        start=str(start) if start is not None else None,
        release_id=str(raw.get("release_id")) if raw.get("release_id") else None,
        seal_sha256=str(raw.get("seal_sha256")) if raw.get("seal_sha256") else None,
        evidence_sha256=str(raw.get("evidence_sha256")) if raw.get("evidence_sha256") else None,
        selection_status=str(raw.get("selection_status") or "INVALID_FOR_SELECTION"),
        e4_status=str(raw.get("e4_status") or "INVALID_FOR_E4"),
    )


def load_forward_epoch_manifest(path: Path | str | None = None) -> ForwardEpochManifest:
    manifest_path = Path(path or DEFAULT_FORWARD_EPOCHS_PATH)
    if not manifest_path.exists():
        raise FileNotFoundError(f"forward_epoch_manifest_missing:{manifest_path}")
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError("forward_epoch_manifest_not_mapping")
    schema = str(payload.get("schema_version") or "")
    if schema != "forward_epochs_v1":
        raise ValueError(f"unsupported_forward_epoch_schema:{schema}")
    raw_epochs = payload.get("epochs") or []
    if isinstance(raw_epochs, Mapping):
        raw_epochs = [dict(value, epoch_id=key) for key, value in raw_epochs.items()]
    epochs = tuple(_as_epoch(raw) for raw in raw_epochs)
    ids = {e.epoch_id for e in epochs}
    active_id = payload.get("active_epoch_id")
    if active_id is not None and str(active_id) not in ids:
        raise ValueError("active_forward_epoch_not_registered")
    active = next((e for e in epochs if e.epoch_id == active_id), None)
    if active and active.kind == "ENGINEERING_SOAK" and active.selection_status != "INVALID_FOR_SELECTION":
        raise ValueError("engineering_soak_must_be_invalid_for_selection")
    return ForwardEpochManifest(schema, str(active_id) if active_id is not None else None, epochs, manifest_path.resolve())


def resolve_active_epoch(path: Path | str | None = None) -> ForwardEpoch | None:
    return load_forward_epoch_manifest(path).active_epoch


def resolve_formal_epoch(path: Path | str | None = None) -> ForwardEpoch | None:
    return load_forward_epoch_manifest(path).formal_epoch


def _normalise_hashes(hashes: Mapping[str, Any] | None) -> dict[str, str]:
    return {key: str((hashes or {}).get(key) or "") for key in SOAK_REQUIRED_HASHES}


def _normalise_metrics(metrics: Mapping[str, Any] | None) -> dict[str, int]:
    """Normalize the six explicit soak defect counters fail-closed."""

    normalized: dict[str, int] = {}
    for key in SOAK_REQUIRED_METRICS:
        raw = (metrics or {}).get(key, 0)
        try:
            normalized[key] = int(raw)
        except (TypeError, ValueError):
            # Invalid/non-numeric counters are represented as one defect;
            # never let a malformed metric unlock a freeze.
            normalized[key] = 1
    return normalized


def _state_rows(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("days", [])
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def update_engineering_soak(
    state: Mapping[str, Any] | None,
    trade_date: str,
    hashes: Mapping[str, Any] | None,
    *,
    sse_open: bool = True,
    open_dates: Iterable[str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    expired_data_count: int = 0,
    manual_package_count: int = 0,
    ledger_imbalance_count: int = 0,
    verifier_blocker_count: int = 0,
    duplicate_package_count: int = 0,
    missing_package_count: int = 0,
    defects: Iterable[str] = (),
    manual_package: bool = False,
    critical_fault: bool = False,
) -> dict[str, Any]:
    """Advance/reset the engineering-soak counter for one SSE day.

    A hash drift, manual补包, critical fault, missing required fingerprint, or
    any defect resets the consecutive counter to zero.  The function is pure;
    callers choose when/where to persist its returned state.
    """

    parsed = date.fromisoformat(str(trade_date))
    old = dict(state or {})
    rows = _state_rows(old)
    new_hashes = _normalise_hashes(hashes)
    supplied_metrics = dict(metrics or {})
    supplied_metrics.update({
        "expired_data_count": expired_data_count if "expired_data_count" not in supplied_metrics else supplied_metrics["expired_data_count"],
        "manual_package_count": manual_package_count if "manual_package_count" not in supplied_metrics else supplied_metrics["manual_package_count"],
        "ledger_imbalance_count": ledger_imbalance_count if "ledger_imbalance_count" not in supplied_metrics else supplied_metrics["ledger_imbalance_count"],
        "verifier_blocker_count": verifier_blocker_count if "verifier_blocker_count" not in supplied_metrics else supplied_metrics["verifier_blocker_count"],
        "duplicate_package_count": duplicate_package_count if "duplicate_package_count" not in supplied_metrics else supplied_metrics["duplicate_package_count"],
        "missing_package_count": missing_package_count if "missing_package_count" not in supplied_metrics else supplied_metrics["missing_package_count"],
    })
    metric_counts = _normalise_metrics(supplied_metrics)
    problems = sorted({str(value) for value in defects if str(value)})
    if not sse_open or parsed.weekday() >= 5:
        return {**old, "schema_version": "engineering_soak_v1", "status": "NON_TRADING_DAY", "updated_at": trade_date}
    if any(not value for value in new_hashes.values()):
        problems.append("MISSING_FINGERPRINT")
    previous = rows[-1] if rows else None
    if previous:
        if any(previous.get("hashes", {}).get(key) != value for key, value in new_hashes.items()):
            problems.append("FINGERPRINT_DRIFT")
        try:
            prev_day = date.fromisoformat(str(previous["trade_date"]))
            if open_dates is not None:
                calendar = sorted(str(value) for value in open_dates)
                try:
                    prev_index = calendar.index(prev_day.isoformat())
                    current_index = calendar.index(parsed.isoformat())
                    if current_index != prev_index + 1:
                        problems.append("NON_CONSECUTIVE_SSE_DAY")
                except ValueError:
                    problems.append("DATE_NOT_IN_SSE_CALENDAR")
            elif (parsed - prev_day).days > 5:
                problems.append("NON_CONSECUTIVE_SSE_DAY")
        except (KeyError, ValueError):
            problems.append("INVALID_PREVIOUS_DAY")
    if manual_package:
        problems.append("MANUAL_PACKAGE")
        metric_counts["manual_package_count"] = max(1, metric_counts["manual_package_count"])
    if critical_fault:
        problems.append("CRITICAL_FAULT")
    for key, value in metric_counts.items():
        if value != 0:
            problems.append(f"{key}_nonzero")
        if value < 0:
            problems.append(f"{key}_invalid")
    clean = not problems
    row = {
        "trade_date": trade_date,
        "hashes": new_hashes,
        "metrics": metric_counts,
        **metric_counts,
        "defects": sorted(set(problems)),
        "manual_package": bool(manual_package),
        "critical_fault": bool(critical_fault),
        "zero_defect": clean,
    }
    # A duplicate date is a correction/rewind, not a new day.  Replace its
    # previous row and recompute from the surviving consecutive suffix.
    rows = [existing for existing in rows if existing.get("trade_date") != trade_date]
    rows.append(row)
    rows.sort(key=lambda value: str(value.get("trade_date") or ""))
    streak = 0
    for candidate in reversed(rows):
        if candidate.get("zero_defect") is not True:
            break
        if streak and candidate.get("trade_date") is None:
            break
        streak += 1
    return {
        "schema_version": "engineering_soak_v1",
        "status": "READY_TO_FREEZE" if streak >= SOAK_TARGET_DAYS else "ACTIVE_ENGINEERING_SOAK",
        "consecutive_zero_defect_days": streak,
        "target_days": SOAK_TARGET_DAYS,
        "days": rows,
        "last_trade_date": trade_date,
        "last_hashes": new_hashes,
        "last_metrics": metric_counts,
        **metric_counts,
        "updated_at": trade_date,
    }


def next_sse_open_day(trade_date: str, open_dates: Iterable[str]) -> str:
    dates = sorted({str(value) for value in open_dates if str(value) > str(trade_date)})
    if not dates:
        raise ValueError("next_sse_trading_day_unavailable")
    return dates[0]


def freeze_forward_epoch(
    soak_state: Mapping[str, Any],
    *,
    freeze_date: str,
    open_dates: Iterable[str],
    epoch_id: str,
    release_id: str,
    git_sha: str,
    config_sha: str,
    dependency_sha: str,
    candidate_sha: str,
    stat_plan_sha: str,
    pit_contract_sha: str,
    test_result_sha: str,
    seal_sha: str | None = None,
) -> dict[str, Any]:
    """Build an immutable formal epoch manifest; no historical backfill."""

    streak = int(soak_state.get("consecutive_zero_defect_days", 0) or 0)
    if streak < SOAK_TARGET_DAYS:
        raise ValueError("engineering_soak_lt_20_days")
    required_shas = (git_sha, config_sha, dependency_sha, candidate_sha, stat_plan_sha, pit_contract_sha, test_result_sha)
    if any(not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None for value in required_shas):
        raise ValueError("freeze_sha_binding_invalid")
    if seal_sha is not None and (not isinstance(seal_sha, str) or _SHA256_RE.fullmatch(seal_sha) is None):
        raise ValueError("freeze_seal_sha_invalid")
    start = next_sse_open_day(freeze_date, open_dates)
    if start <= freeze_date:
        raise ValueError("forward_epoch_start_must_follow_freeze")
    payload = {
        "schema_version": "forward_epoch_manifest_v1",
        "epoch_id": epoch_id,
        "kind": "FORMAL_BLIND",
        "status": "FROZEN",
        "selection_status": "VALID_FOR_SELECTION",
        "e4_status": "ACCUMULATING",
        "freeze_date": freeze_date,
        "start": start,
        "release_id": release_id,
        "seal_sha256": seal_sha,
        "bindings": {
            "git_sha": git_sha,
            "config_sha": config_sha,
            "dependency_sha": dependency_sha,
            "candidate_sha": candidate_sha,
            "stat_plan_sha": stat_plan_sha,
            "pit_contract_sha": pit_contract_sha,
            "test_result_sha": test_result_sha,
        },
        "soak_days": streak,
        "immutable": True,
    }
    payload["manifest_sha256"] = canonical_sha(payload)
    return payload


build_forward_epoch_manifest = freeze_forward_epoch
freeze_epoch = freeze_forward_epoch


def write_immutable_json(path: Path | str, payload: Mapping[str, Any]) -> Path:
    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"immutable_manifest_exists:{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(destination)
    destination.chmod(0o444)
    return destination


class EngineeringSoakTracker:
    """Small stateful adapter around :func:`update_engineering_soak`."""

    def __init__(self, state: Mapping[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})

    def update(self, trade_date: str, hashes: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.state = update_engineering_soak(self.state, trade_date, hashes, **kwargs)
        return dict(self.state)

    @property
    def consecutive_days(self) -> int:
        return int(self.state.get("consecutive_zero_defect_days", 0) or 0)

    @property
    def ready_to_freeze(self) -> bool:
        return self.consecutive_days >= SOAK_TARGET_DAYS


update_soak_tracker = update_engineering_soak
is_soak_freeze_ready = lambda state: int((state or {}).get("consecutive_zero_defect_days", 0) or 0) >= SOAK_TARGET_DAYS


__all__ = [
    "ForwardEpoch", "ForwardEpochManifest", "DEFAULT_FORWARD_EPOCHS_PATH",
    "SOAK_REQUIRED_HASHES", "SOAK_REQUIRED_METRICS", "SOAK_TARGET_DAYS", "canonical_sha", "sha256_file",
    "load_forward_epoch_manifest", "resolve_active_epoch", "resolve_formal_epoch",
    "update_engineering_soak", "update_soak_tracker", "EngineeringSoakTracker",
    "is_soak_freeze_ready", "next_sse_open_day", "freeze_forward_epoch",
    "write_immutable_json",
]
