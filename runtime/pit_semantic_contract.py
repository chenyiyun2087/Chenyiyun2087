#!/usr/bin/env python3
"""PIT Semantic Contract — single authoritative source for all PIT data schemas.

All formal modules (Adapter, Semantic Audit, Factor Builder, Package Builder,
Readiness) MUST use this module to load field definitions from the canonical
contract at config/pit_semantics/ashare_pit_semantics_v1.yaml.

No module may maintain its own hardcoded REQUIRED_COLUMNS or EXPECTED_SCHEMAS.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "config" / "pit_semantics" / "ashare_pit_semantics_v1.yaml"

_contract_cache: dict[str, Any] | None = None
_contract_sha_cache: str | None = None


def _load_raw_contract() -> dict[str, Any]:
    global _contract_cache
    if _contract_cache is None:
        if not CONTRACT_PATH.exists():
            raise FileNotFoundError(f"Semantic contract not found: {CONTRACT_PATH}")
        _contract_cache = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8")) or {}
    return _contract_cache


def load_contract() -> dict[str, Any]:
    return _load_raw_contract()


def get_contract_sha256() -> str:
    global _contract_sha_cache
    if _contract_sha_cache is None:
        _contract_sha_cache = hashlib.sha256(
            CONTRACT_PATH.read_bytes()
        ).hexdigest()
    return _contract_sha_cache


def get_families() -> list[str]:
    contract = _load_raw_contract()
    return sorted(contract.get("families", {}).keys())


def get_family_contract(family: str) -> dict[str, Any]:
    contract = _load_raw_contract()
    fam = contract.get("families", {}).get(family)
    if fam is None:
        raise KeyError(f"Unknown PIT family: {family}")
    return fam


def get_required_columns(family: str) -> set[str]:
    return set(get_family_contract(family).get("required_columns", []))


def get_primary_key(family: str) -> list[str]:
    return list(get_family_contract(family).get("primary_key", []))


def get_available_at_column(family: str) -> str:
    return str(get_family_contract(family).get("available_at_column", ""))


def get_business_time_column(family: str) -> str:
    return str(get_family_contract(family).get("business_time_column", ""))


def get_signal_cutoff() -> str:
    """Return the canonical T-day signal cutoff from the contract."""
    return str(_load_raw_contract().get("signal_cutoff") or "T15:30:00+08:00")


def get_source_families() -> tuple[str, ...]:
    """Return all source families in deterministic contract order."""
    return tuple(sorted(_load_raw_contract().get("families", {}).keys()))


def signal_time_for_trade_dates(trade_dates: pd.Series) -> pd.Series:
    """Build timezone-aware T-day signal timestamps at the contract cutoff."""
    parsed = pd.to_datetime(trade_dates, errors="coerce")
    cutoff = get_signal_cutoff().strip()
    # The contract stores the cutoff as ``T15:30:00+08:00``.  Keep the
    # parser tolerant of a human-readable value without the leading ``T``
    # while still deriving the timestamp from the canonical contract rather
    # than duplicating a second hard-coded timing rule in code.
    if cutoff and not cutoff.startswith("T"):
        cutoff = f"T{cutoff}"
    return pd.to_datetime(
        parsed.dt.strftime("%Y-%m-%d") + cutoff,
        errors="coerce",
        utc=True,
    )


TIMEZONE_PATTERN = re.compile(r"[Zz]$|[+-]\d{2}:\d{2}$")


def validate_explicit_timezone(series: pd.Series) -> list[str]:
    """Check that all non-null values contain an explicit timezone suffix.
    Returns list of offenders (first 5).  A source family must also use one
    consistent offset; silently normalizing a mixture of ``+08:00`` and UTC
    values would make the PIT comparison dependent on parser behavior.
    """
    offenders = []
    offsets: set[str] = set()
    for idx, val in series.dropna().items():
        s = str(val)
        match = TIMEZONE_PATTERN.search(s)
        if not match:
            offenders.append(f"row_{idx}:{s}")
            if len(offenders) >= 5:
                break
        else:
            offset = match.group(0).upper()
            offsets.add("+00:00" if offset == "Z" else offset)
    if len(offsets) > 1:
        offenders.append("mixed_timezone_offsets:" + ",".join(sorted(offsets)))
    return offenders[:5]


def validate_frame_schema(frame: pd.DataFrame, family: str) -> list[str]:
    """Check that a DataFrame satisfies the contract for the given family.
    Returns list of blockers (empty = OK)."""
    blockers = []
    required = get_required_columns(family)
    missing = required - set(frame.columns)
    if missing:
        blockers.append(f"schema_missing_columns:{family}:{sorted(missing)}")

    # Check primary key uniqueness
    pk = get_primary_key(family)
    if pk and all(c in frame.columns for c in pk):
        dupes = frame.duplicated(subset=pk, keep=False)
        if dupes.any():
            blockers.append(f"primary_key_duplicates:{family}:{int(dupes.sum())}")

    # Check available_at timezone
    avail_col = get_available_at_column(family)
    if avail_col and avail_col in frame.columns:
        tz_offenders = validate_explicit_timezone(frame[avail_col])
        if tz_offenders:
            blockers.append(f"available_at_no_timezone:{family}:{tz_offenders}")

    return blockers
