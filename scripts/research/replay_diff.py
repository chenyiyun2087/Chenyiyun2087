"""Deterministic, bounded diagnostics for Alpha evidence replays.

The helpers in this module explain replay drift without weakening the exact
SHA gate.  They never authorize production, canary trading, or capital.
"""

from __future__ import annotations

import importlib.metadata
import hashlib
import locale
import os
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from runtime.acceptance_config import canonical_sha


VOLATILE_KEYS = {"content_sha256", "generated_at", "provenance"}


def _cpu_fingerprint() -> dict[str, Any]:
    flags: list[str] = []
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("flags", "features")) and ":" in line:
                flags = sorted(set(line.split(":", 1)[1].split()))
                break
    return {
        "architecture": platform.machine(),
        "processor": platform.processor() or "unknown",
        "flags": flags,
        "flags_status": "AVAILABLE" if flags else "NOT_EXPOSED_BY_HOST",
    }


def _dependency_lock_sha256() -> dict[str, str | None]:
    project_root = Path(__file__).resolve().parents[2]
    for name in (
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        path = project_root / name
        if path.is_file():
            return {
                "path": str(path.relative_to(project_root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return {"path": None, "sha256": None}


def build_environment_manifest(
    timezone: str,
    runtime_determinism: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fingerprint the complete Python package environment deterministically."""
    packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").strip().lower()
        if name:
            packages[name] = str(distribution.version)
    determinism = dict(runtime_determinism or {})
    determinism["blas_backend"] = str(
        np.__config__.CONFIG.get("Build Dependencies", {}).get("blas", {}).get("name")
        or "unknown"
    )
    determinism["effective_locale"] = locale.setlocale(locale.LC_ALL, None)
    determinism["effective_thread_env"] = {
        key: os.environ.get(key)
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
    }
    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "kernel_version": platform.version(),
        "architecture": platform.machine(),
        "cpu_fingerprint": _cpu_fingerprint(),
        "filesystem_encoding": sys.getfilesystemencoding(),
        "container_image_sha256": (
            os.environ.get("CONTAINER_IMAGE_SHA256")
            or os.environ.get("CONTAINER_IMAGE_DIGEST")
            or "NOT_PROVIDED"
        ),
        "dependency_lock": _dependency_lock_sha256(),
        "timezone": timezone,
        "packages": dict(sorted(packages.items())),
        "runtime_determinism": determinism,
    }
    return {
        "schema_version": "alpha_v3_7_environment_manifest_v1",
        "status": "PASS",
        "completeness_warnings": [
            key
            for key, missing in (
                (
                    "cpu_flags_not_exposed",
                    environment["cpu_fingerprint"]["flags_status"]
                    != "AVAILABLE",
                ),
                (
                    "container_image_digest_not_provided",
                    environment["container_image_sha256"] == "NOT_PROVIDED",
                ),
                (
                    "dependency_lock_not_found",
                    environment["dependency_lock"]["sha256"] is None,
                ),
            )
            if missing
        ],
        "environment": environment,
        "environment_lock_hash": canonical_sha(environment),
    }


def _scalar(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _records(
    frame: pd.DataFrame,
    *,
    preferred_columns: tuple[str, ...],
    sort_columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    columns = [column for column in preferred_columns if column in frame.columns]
    scoped = frame[columns].copy()
    for column in scoped.columns:
        if "date" in column or column.endswith("_time"):
            parsed = pd.to_datetime(scoped[column], errors="coerce")
            scoped[column] = parsed.map(
                lambda value: value.isoformat() if not pd.isna(value) else None
            )
    available_sort = [column for column in sort_columns if column in scoped.columns]
    if available_sort:
        scoped = scoped.sort_values(available_sort, kind="mergesort")
    return [
        {str(key): _scalar(value) for key, value in row.items()}
        for row in scoped.to_dict(orient="records")
    ]


def build_replay_snapshot(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    attribution: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    """Create the structured snapshot consumed by the replay diff engine."""
    nav_records = _records(
        nav,
        preferred_columns=(
            "strategy",
            "trade_date",
            "nav",
            "total_equity",
            "cash",
            "market_value",
            "position_count",
            "gross_exposure",
            "risk_decision",
        ),
        sort_columns=("strategy", "trade_date"),
    )
    trade_records = _records(
        trades,
        preferred_columns=(
            "order_id",
            "strategy",
            "trade_date",
            "symbol",
            "side",
            "signal_time",
            "execute_time",
            "price",
            "shares",
            "quantity",
            "gross_amount",
            "cost",
            "fill_status",
            "limit_status",
            "reason",
        ),
        sort_columns=(
            "order_id",
            "strategy",
            "trade_date",
            "symbol",
            "side",
        ),
    )
    normalized_attribution = {
        key: value for key, value in attribution.items() if key not in VOLATILE_KEYS
    }
    normalized_risk = {
        key: value for key, value in risk.items() if key not in VOLATILE_KEYS
    }
    components = {
        "nav": nav_records,
        "trades": trade_records,
        "attribution": normalized_attribution,
        "risk": normalized_risk,
    }
    return {
        "schema_version": "alpha_v3_5_replay_snapshot_v1",
        "status": "PASS" if nav_records else "BLOCKED",
        "blockers": [] if nav_records else ["replay_nav_snapshot_missing"],
        "component_sha256": {
            key: canonical_sha(value) for key, value in components.items()
        },
        "components": components,
    }


def _diff_values(
    reference: Any,
    current: Any,
    *,
    path: str,
    rows: list[dict[str, Any]],
    limit: int,
) -> int:
    """Return total differences while retaining only a bounded sample."""
    if isinstance(reference, dict) and isinstance(current, dict):
        count = 0
        for key in sorted(set(reference).union(current)):
            count += _diff_values(
                reference.get(key),
                current.get(key),
                path=f"{path}.{key}" if path else str(key),
                rows=rows,
                limit=limit,
            )
        return count
    if isinstance(reference, list) and isinstance(current, list):
        count = 0
        for index in range(max(len(reference), len(current))):
            left = reference[index] if index < len(reference) else None
            right = current[index] if index < len(current) else None
            count += _diff_values(
                left,
                right,
                path=f"{path}[{index}]",
                rows=rows,
                limit=limit,
            )
        return count
    if reference == current:
        return 0
    if len(rows) < limit:
        component = path.split(".", 1)[0].split("[", 1)[0]
        severity = "WARNING" if component == "attribution" else "CRITICAL"
        rows.append(
            {
                "path": path,
                "reference": reference,
                "current": current,
                "severity": severity,
                "action": "MANUAL_REVIEW" if severity == "WARNING" else "BLOCK",
            }
        )
    return 1


def build_replay_diff_report(
    reference_manifest: dict[str, Any],
    replay_contract: dict[str, Any],
    current_snapshot: dict[str, Any],
    *,
    max_rows: int,
) -> dict[str, Any]:
    """Explain metadata, NAV, trade, attribution, and risk replay drift."""
    report_entry = (
        (reference_manifest.get("reports") or {}).get(
            "replay_snapshot_report.json"
        )
        or {}
    )
    reference_path_raw = str(report_entry.get("path") or "")
    reference_path = Path(reference_path_raw) if reference_path_raw else None
    reference_snapshot: dict[str, Any] = {}
    if reference_path is not None and reference_path.is_file():
        import json

        try:
            loaded = json.loads(reference_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                reference_snapshot = loaded
        except (OSError, json.JSONDecodeError):
            reference_snapshot = {}
    reference_contract = reference_manifest.get("replay_contract")
    if not isinstance(reference_contract, dict) or not reference_snapshot:
        return {
            "schema_version": "alpha_v3_7_replay_diff_v1",
            "status": "BLOCKED",
            "promotion_eligible": False,
            "blockers": ["structured_replay_reference_missing_or_invalid"],
            "summary": {},
            "diff_rows": [],
            "diff_rows_truncated": False,
        }

    component_diffs: dict[str, int] = {}
    diff_rows: list[dict[str, Any]] = []
    total = 0
    total += _diff_values(
        reference_contract,
        replay_contract,
        path="metadata",
        rows=diff_rows,
        limit=max_rows,
    )
    for component in ("nav", "trades", "attribution", "risk"):
        count = _diff_values(
            (reference_snapshot.get("components") or {}).get(component),
            (current_snapshot.get("components") or {}).get(component),
            path=component,
            rows=diff_rows,
            limit=max_rows,
        )
        component_diffs[component] = count
        total += count
    summary = {
        "metadata_difference_count": total - sum(component_diffs.values()),
        "nav_difference_count": component_diffs["nav"],
        "trade_difference_count": component_diffs["trades"],
        "attribution_difference_count": component_diffs["attribution"],
        "risk_difference_count": component_diffs["risk"],
        "total_difference_count": total,
    }
    critical = sum(1 for row in diff_rows if row["severity"] == "CRITICAL")
    warning = sum(1 for row in diff_rows if row["severity"] == "WARNING")
    return {
        "schema_version": "alpha_v3_7_replay_diff_v1",
        "status": "PASS" if total == 0 else "BLOCKED",
        "promotion_eligible": False,
        "blockers": [] if total == 0 else ["structured_replay_drift_detected"],
        "summary": summary,
        "severity_summary": {
            "retained_critical_difference_count": critical,
            "retained_warning_difference_count": warning,
            "exact_gate_policy": "ANY_DIFFERENCE_BLOCKS",
        },
        "diff_rows": diff_rows,
        "diff_rows_truncated": total > len(diff_rows),
    }
