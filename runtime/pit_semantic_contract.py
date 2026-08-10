#!/usr/bin/env python3
"""PIT Semantic Contract — single authoritative source for all PIT data schemas.

All formal modules (Adapter, Semantic Audit, Factor Builder, Package Builder,
Readiness) MUST use this module to load field definitions from the canonical
contract at config/pit_semantics/ashare_t2130_t1_v1.yaml.

No module may maintain its own hardcoded REQUIRED_COLUMNS or EXPECTED_SCHEMAS.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "config" / "pit_semantics" / "ashare_t2130_t1_v1.yaml"

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


def get_canonical_execution_columns(family: str) -> set[str]:
    return set(get_family_contract(family).get("canonical_execution_columns", []))


def get_economic_columns(family: str) -> set[str]:
    return set(get_family_contract(family).get("economic_columns", []))


def get_primary_key(family: str) -> list[str]:
    return list(get_family_contract(family).get("primary_key", []))


def get_available_at_column(family: str) -> str:
    return str(get_family_contract(family).get("available_at_column", ""))


def get_business_time_column(family: str) -> str:
    return str(get_family_contract(family).get("business_time_column", ""))


def get_signal_cutoff() -> str:
    """Return the canonical T-day signal cutoff from the contract."""
    return str(_load_raw_contract().get("signal_cutoff") or "T21:30:00+08:00")


def get_source_families() -> tuple[str, ...]:
    """Return all source families in deterministic contract order."""
    contract = _load_raw_contract()
    configured = contract.get("canonical_families") or []
    families = tuple(str(value) for value in configured)
    if families:
        # A typo in the registry must fail closed instead of silently dropping
        # a canonical family from adapter/audit/package traversal.
        declared = set(contract.get("families", {}).keys())
        missing = [name for name in families if name not in declared]
        if missing:
            raise ValueError(f"canonical_families_not_declared:{','.join(missing)}")
        return families
    return tuple(sorted(contract.get("families", {}).keys()))


def get_lineage_columns() -> tuple[str, ...]:
    """Return the four provenance columns required on every canonical frame."""
    values = _load_raw_contract().get("lineage_columns") or [
        "source_published_at",
        "warehouse_loaded_at",
        "decision_cutoff",
        "availability_source",
    ]
    return tuple(str(value) for value in values)


def get_formal_cutoff_config() -> dict[str, str]:
    """Return the release-lineage cutoff policy from the canonical contract."""
    raw = _load_raw_contract().get("formal_cutoff") or {}
    return {
        "timezone": str(raw.get("timezone") or _load_raw_contract().get("timezone") or "Asia/Shanghai"),
        "default_time": str(raw.get("default_time") or "21:30:00"),
        "hard_time": str(raw.get("hard_time") or "21:30:00"),
    }


def _parse_clock(value: str, fallback: str) -> time:
    text = str(value or fallback).strip().replace("T", "")
    try:
        return datetime.strptime(text[:8], "%H:%M:%S").time()
    except ValueError:
        return datetime.strptime(fallback, "%H:%M:%S").time()


def formal_cutoff_for_dates(
    dates: pd.Series,
    *,
    hard: bool = False,
) -> pd.Series:
    """Build timezone-aware lineage cutoff timestamps for business dates."""
    cfg = get_formal_cutoff_config()
    clock = _parse_clock(cfg["hard_time"] if hard else cfg["default_time"], "21:30:00")
    parsed = pd.to_datetime(
        dates if isinstance(dates, pd.Series) else pd.Series(dates),
        errors="coerce",
    )
    # Timestamp construction with an explicit offset is intentionally done via
    # the timezone name; this handles Asia/Shanghai consistently and rejects
    # naive provider strings later in the validation layer.
    return pd.to_datetime(
        parsed.dt.strftime("%Y-%m-%d") + f"T{clock.strftime('%H:%M:%S')}",
        errors="coerce",
    ).dt.tz_localize(cfg["timezone"], ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def _is_date_only(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return False
    # YYYYMMDD and YYYY-MM-DD are date-only; a timestamp has a time marker.
    return bool(re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", text))


def conservative_financial_availability(
    values: pd.Series,
    *,
    trade_calendar: pd.DataFrame | None = None,
    session_time: str = "09:00:00",
) -> tuple[pd.Series, pd.Series]:
    """Normalize financial date-only availability conservatively.

    A date-only announcement is not assumed to be visible during that day's
    session.  It is moved to the next open trading session (or next business
    day when no calendar is supplied) and the returned source marker identifies
    the conservative transformation.  Existing timezone-aware timestamps are
    preserved byte-for-byte at the semantic level.
    """
    raw = values.copy()
    parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    markers = pd.Series("provider_timestamp", index=raw.index, dtype="object")
    open_dates: list[pd.Timestamp] = []
    if trade_calendar is not None and not trade_calendar.empty:
        date_col = "cal_date" if "cal_date" in trade_calendar.columns else "trade_date"
        if date_col in trade_calendar.columns:
            cal = pd.to_datetime(trade_calendar[date_col], errors="coerce").dt.normalize()
            is_open = pd.to_numeric(trade_calendar.get("is_open", 1), errors="coerce").fillna(0).astype(bool)
            open_dates = sorted(cal[is_open & cal.notna()].drop_duplicates().tolist())
    for idx, value in raw.items():
        if not _is_date_only(value):
            continue
        date = pd.to_datetime(value, errors="coerce")
        if pd.isna(date):
            continue
        if open_dates:
            candidates = [item for item in open_dates if item > date.normalize()]
            next_day = candidates[0] if candidates else date.normalize() + pd.offsets.BDay(1)
        else:
            next_day = date.normalize() + pd.offsets.BDay(1)
        parsed.loc[idx] = pd.Timestamp(
            f"{next_day.date().isoformat()}T{session_time}+08:00"
        ).tz_convert("UTC")
        markers.loc[idx] = "financial_date_only_conservative_next_session"
    return parsed, markers


def validate_lineage_frame(
    frame: pd.DataFrame,
    family: str,
    *,
    strict: bool = True,
    business_dates: pd.Series | None = None,
) -> list[str]:
    """Validate source publication/loading/availability provenance.

    This helper is shared by Adapter, Semantic Audit, and Factor Builder so a
    file source cannot pass one stage using a different timestamp interpretation.
    """
    blockers: list[str] = []
    required = get_lineage_columns()
    missing = [column for column in required if column not in frame.columns]
    if missing and strict:
        blockers.extend(f"lineage_column_missing:{family}:{column}" for column in missing)
        return blockers
    avail_col = get_available_at_column(family)
    if avail_col not in frame.columns:
        if strict:
            blockers.append(f"available_at_missing:{family}:{avail_col}")
        return blockers
    date_series = business_dates
    if date_series is None:
        business_col = get_business_time_column(family)
        date_series = frame.get(business_col, pd.Series(index=frame.index, dtype="object"))
    decision = frame.get("decision_cutoff")
    if decision is None:
        if strict:
            blockers.append(f"lineage_column_missing:{family}:decision_cutoff")
        return blockers
    parsed_dates = pd.to_datetime(date_series, errors="coerce")
    default_cutoff = formal_cutoff_for_dates(pd.Series(parsed_dates, index=frame.index))
    hard_cutoff = formal_cutoff_for_dates(pd.Series(parsed_dates, index=frame.index), hard=True)
    decision_ts = pd.to_datetime(decision, errors="coerce", utc=True)
    if decision_ts.isna().any():
        blockers.append(f"lineage_decision_cutoff_invalid:{family}")
    if (decision_ts > hard_cutoff).fillna(False).any():
        blockers.append(f"lineage_decision_cutoff_after_hard_limit:{family}")
    if (decision_ts > default_cutoff).fillna(False).any():
        blockers.append(f"lineage_decision_cutoff_after_formal_cutoff:{family}")
    for column in ("source_published_at", "warehouse_loaded_at", avail_col):
        if column not in frame.columns:
            if strict:
                blockers.append(f"lineage_column_missing:{family}:{column}")
            continue
        values = frame[column]
        offenders = validate_explicit_timezone(values)
        if offenders:
            blockers.append(f"lineage_timezone_missing:{family}:{column}")
        parsed = pd.to_datetime(values, errors="coerce", utc=True)
        if parsed.isna().any():
            blockers.append(f"lineage_timestamp_invalid:{family}:{column}")
        if (parsed > decision_ts).fillna(False).any():
            blockers.append(f"lineage_{column}_after_decision_cutoff:{family}")
        if (parsed > hard_cutoff).fillna(False).any():
            blockers.append(f"lineage_{column}_after_hard_limit:{family}")
    if "availability_source" in frame.columns:
        source = frame["availability_source"].astype(str).str.strip()
        if source.eq("").any() or source.str.lower().isin({"nan", "none", "null"}).any():
            blockers.append(f"lineage_availability_source_missing:{family}")
        if strict and source.str.contains(r"placeholder|fake|dummy|constant|data_e0", case=False, regex=True).any():
            blockers.append(f"lineage_availability_source_placeholder:{family}")
    elif strict:
        blockers.append(f"lineage_column_missing:{family}:availability_source")
    return sorted(set(blockers))


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
