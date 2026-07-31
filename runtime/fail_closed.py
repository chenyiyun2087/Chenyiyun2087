#!/usr/bin/env python3
"""Fail-closed utilities — standardized error handling for formal evidence runs.

Every formal component must convert exceptions to structured BLOCKED reports.
No exception may propagate without a report.  No BLOCKED run may leave stale
PASS artifacts.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from runtime.formal_evidence_contract import EvidenceStatus, canonical_sha


def blocked_report(
    component: str,
    stage: str,
    error_code: str,
    *,
    exception: Exception | None = None,
    extra: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Produce a standardized BLOCKED report and write it to output_dir.

    If output_dir is provided, the report is written as blocked_report.json
    and stale PASS artifacts are cleaned.
    """
    report: dict[str, Any] = {
        "schema_version": "formal_evidence_backbone_v5_0",
        "status": "BLOCKED",
        "component": component,
        "stage": stage,
        "error_code": error_code,
        "blockers": [error_code],  # backward compat with builder/adapter reports
        "capital_authority": False,
        "evidence_status": EvidenceStatus().as_dict(),
        "traceback": traceback.format_exc() if exception else None,
    }
    if exception is not None:
        report["exception_type"] = type(exception).__name__
        report["exception_message"] = str(exception)
    if extra:
        report["extra"] = extra
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Clean stale qualified artifacts
        for stale in ["factor_panel_daily.parquet", "pit_source_manifest.json"]:
            (output_dir / stale).unlink(missing_ok=True)
        (output_dir / "blocked_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return report


def fail_closed(
    component: str,
    stage: str,
    exc: Exception,
    *,
    output_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert an exception to a BLOCKED report.  Never throws."""
    try:
        return blocked_report(
            component=component,
            stage=stage,
            error_code=f"UNHANDLED_EXCEPTION_{type(exc).__name__.upper()}",
            exception=exc,
            extra=extra,
            output_dir=output_dir,
        )
    except Exception:
        # Absolute last resort — must return something structured
        return {
            "status": "BLOCKED",
            "component": component,
            "stage": stage,
            "error_code": "FAIL_CLOSED_ITSELF_FAILED",
            "capital_authority": False,
        }
