"""PIT daily tradable universe contract (v5.5 Shadow Engine v2).

The daily Signal Package's universe must come from the canonical PIT
contract — never from an `eligible_universe = True` shortcut.  This module
builds the per-day tradable universe from the release-style snapshots and
enforces availability gates:

  - listing status on the day (is_listed)
  - ST / *ST history (is_st)
  - suspension (is_suspended)
  - delisting / special status transitions (security_status_transition)
  - days since listing (list_days)
  - limit-status column sanity (limit_status)
  - board rules are handled downstream by execution_market_rules (the
    limit ratio is a pure function of symbol + is_st)
  - PIT availability of financial / industry / adjustment / benchmark
    inputs (from *_available_at semantics)

Fail-closed rule: ANY required input missing for the signal date ->
SIGNAL_PACKAGE_BLOCKED.  There is NO fallback to "whole market
tradeable".  This module is pure (no DB) so tests run without a database.

Inputs (DataFrames):
  universe_snapshot : tradable universe rows for the signal date with
      columns trade_date, symbol, is_listed, is_st, is_suspended,
      limit_status, security_status_transition [, universe_available_at]
  availability     : optional per-family latest availability dict
      {financial_available_at, industry_available_at,
       adjustment_available_at, benchmark_available_at} for the date —
      a missing family BLOCKS the package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

REQUIRED_UNIVERSE_COLUMNS = (
    "trade_date", "symbol", "is_listed", "is_st", "is_suspended",
    "limit_status", "security_status_transition",
)

AVAILABILITY_FAMILIES = (
    "financial_available_at", "industry_available_at",
    "adjustment_available_at", "benchmark_available_at",
)

# Status transitions that make a name untradeable regardless of other flags.
UNTRADEABLE_TRANSITIONS = ("DELISTED", "DELISTING", "SUSPENDED", "STOPPED")


@dataclass
class UniverseBuildResult:
    """Result of a daily universe build — never silently partial."""
    status: str                       # "READY" | "SIGNAL_PACKAGE_BLOCKED"
    trade_date: Optional[str] = None
    universe: Optional[pd.DataFrame] = None
    n_tradeable: int = 0
    n_total: int = 0
    blockers: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status == "SIGNAL_PACKAGE_BLOCKED"


def _check_schema(universe: pd.DataFrame) -> list[str]:
    missing = [c for c in REQUIRED_UNIVERSE_COLUMNS if c not in universe.columns]
    return [f"universe_missing_column:{c}" for c in missing]


def _check_availability(availability: dict | None,
                        trade_date: str) -> list[str]:
    """PIT availability gates: every required family must have an
    available_at not later than the signal date."""
    if availability is None:
        return [f"{f}:no_availability_gate" for f in AVAILABILITY_FAMILIES]
    blockers = []
    for family in AVAILABILITY_FAMILIES:
        val = availability.get(family)
        if val is None or (isinstance(val, str) and not val):
            blockers.append(f"{family}:missing")
        elif isinstance(val, str) and val.split("T")[0] > trade_date:
            blockers.append(f"{family}:later_than_signal_date")
    return blockers


def _list_days(universe: pd.DataFrame, trade_date: str) -> pd.Series:
    """Days since listing, derived from security_status_transition
    (LISTED events) when present, else NaN (not a blocker — board rules
    still apply via limit-ratio logic)."""
    if "security_status_transition" not in universe.columns:
        return pd.Series(np.nan, index=universe.index)
    trans = universe["security_status_transition"].astype(str)
    is_listed_event = trans.str.contains("LISTED", na=False)
    listed_dates = pd.to_datetime(universe["trade_date"], errors="coerce")
    days = (pd.to_datetime(trade_date) - listed_dates).dt.days.where(
        is_listed_event, np.nan)
    return days


def build_daily_universe(
    universe_snapshot: pd.DataFrame,
    trade_date: str,
    availability: dict | None = None,
    contract: dict | None = None,
) -> UniverseBuildResult:
    """Build the tradeable universe for one signal date.

    Blocks when:
      - the snapshot is empty / lacks required columns
      - any PIT availability family is missing or later than the date
      - the snapshot carries no rows for the signal date
      - (v5.5.1, when ``contract`` is given) any exclusion the contract
        requires has no RELIABLE source — NaN is_st / is_suspended /
        is_listed, or no new-stock source, BLOCKS instead of defaulting
        to "normal".  Without a contract the legacy fillna behavior is
        preserved (OOS/historical path unchanged).

    contract keys (forward_shadow_v2.yaml universe_contract):
      exclude_st: true                 is_st == 1 never tradeable
      exclude_new_stock_days: 60       is_new == 1 (or list_days < 60)
      exclude_delisting_period: true   DELISTED/DELISTING transitions
      exclude_suspended: true          is_suspended == 1 never tradeable
    """
    result = UniverseBuildResult(status="READY", trade_date=trade_date)
    if universe_snapshot is None or universe_snapshot.empty:
        result.status = "SIGNAL_PACKAGE_BLOCKED"
        result.blockers.append("universe_snapshot_empty")
        return result

    schema_blockers = _check_schema(universe_snapshot)
    if schema_blockers:
        result.status = "SIGNAL_PACKAGE_BLOCKED"
        result.blockers.extend(schema_blockers)
        return result

    day = universe_snapshot[
        universe_snapshot["trade_date"].astype(str) == trade_date].copy()
    if day.empty:
        result.status = "SIGNAL_PACKAGE_BLOCKED"
        result.blockers.append(f"no_universe_rows_for:{trade_date}")
        return result

    avail_blockers = _check_availability(availability, trade_date)
    if avail_blockers:
        result.status = "SIGNAL_PACKAGE_BLOCKED"
        result.blockers.extend(avail_blockers)
        return result

    day["symbol"] = day["symbol"].astype(str).str.zfill(6)
    day["is_listed"] = pd.to_numeric(day["is_listed"], errors="coerce")
    day["is_st"] = pd.to_numeric(day["is_st"], errors="coerce")
    day["is_suspended"] = pd.to_numeric(
        day["is_suspended"], errors="coerce")
    trans = day["security_status_transition"].astype(str)
    day["list_days"] = _list_days(day, trade_date)

    # ── v5.5.1 strict status-source contract (when provided) ──
    exclusions = []
    if contract:
        for col in ("is_listed", "is_st", "is_suspended"):
            if day[col].isna().any():
                result.status = "SIGNAL_PACKAGE_BLOCKED"
                result.blockers.append(f"status_source_missing:{col}")
                return result
        if contract.get("exclude_st"):
            exclusions.append("is_st")
        if contract.get("exclude_suspended"):
            exclusions.append("is_suspended")
        new_stock_days = contract.get("exclude_new_stock_days")
        if new_stock_days:
            if "is_new" in day.columns:
                day["is_new"] = pd.to_numeric(day["is_new"],
                                              errors="coerce")
                if day["is_new"].isna().any():
                    result.status = "SIGNAL_PACKAGE_BLOCKED"
                    result.blockers.append("status_source_missing:is_new")
                    return result
                exclusions.append("is_new")
            elif day["list_days"].notna().all():
                exclusions.append("list_days_lt_60")
            else:
                # The contract demands a 60-day new-stock exclusion but no
                # reliable source exists -> BLOCKED, never default-normal.
                result.status = "SIGNAL_PACKAGE_BLOCKED"
                result.blockers.append("status_source_missing:new_stock")
                return result
        if contract.get("exclude_delisting_period"):
            exclusions.append("delisting_transition")

    tradeable = (
        day["is_listed"].eq(1)
        & day["is_suspended"].eq(0)
        & ~trans.isin(UNTRADEABLE_TRANSITIONS)
        & ~day["limit_status"].astype(str).isin({"DELISTED", "SUSPENDED"})
    )
    if "is_st" in exclusions:
        tradeable &= day["is_st"].ne(1)
    if "is_suspended" in exclusions:
        pass  # already excluded above
    if "is_new" in exclusions:
        tradeable &= day["is_new"].ne(1)
    if "list_days_lt_60" in exclusions:
        tradeable &= day["list_days"] >= 60
    if "delisting_transition" in exclusions:
        pass  # UNTRADEABLE_TRANSITIONS already covers it
    day["tradeable"] = tradeable.fillna(False)
    result.universe = day
    result.n_total = int(len(day))
    result.n_tradeable = int(day["tradeable"].sum())
    if result.n_tradeable == 0:
        result.status = "SIGNAL_PACKAGE_BLOCKED"
        result.blockers.append("zero_tradeable_names")
        return result
    return result
