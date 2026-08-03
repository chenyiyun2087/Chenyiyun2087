"""Canonical formal status semantics — single source of truth for the
four decomposed status dimensions used by the unified formal registry.

Background (2026-08-03 evaluation): the project historically conflated three
meanings of "VERIFIED" — (1) engineering run completed, (2) strict ledger
reconciled, (3) economic alpha qualified.  A run whose *execution* is VERIFIED
and whose *ledger* is VERIFIED can still be *economically* failed.  Consumers
must therefore read the four status dimensions separately, never a single
`status` field.

Every producer (formal runner, champion runner, PIT pipeline, migration
scripts) and every validator imports from here — never duplicate status
vocabularies.

Invariants:
- ``capital_status`` may never be derived from evidence; it is always
  ``BLOCKED`` unless set by an explicit human-approved capital decision.
- ``data_status`` of E0 means the snapshot contains derived/placeholder
  fields and must not feed a formal E3 run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Status vocabularies (one enum per dimension)
# ---------------------------------------------------------------------------


class ExecutionStatus(str, Enum):
    """Strict-ledger execution integrity: did the run execute correctly?"""

    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"
    VERIFIED = "VERIFIED"  # 0 T+1 violations, 0 conservation errors, REPRODUCIBLE

    @classmethod
    def from_ledger(cls, ledger_status: str | None) -> "ExecutionStatus":
        if ledger_status == "VERIFIED":
            return cls.VERIFIED
        if ledger_status in ("BLOCKED", "PARTIAL_UNVERIFIED", "MISMATCH_BLOCKED"):
            return cls.BLOCKED
        return cls.NOT_RUN


class DataStatus(str, Enum):
    """Point-in-time data evidence level (mirrors evidence_strength_levels)."""

    E0_DIAGNOSTIC = "E0_DIAGNOSTIC"  # placeholder / derived fields (engineering only)
    E1_SNAPSHOT = "E1_SNAPSHOT"  # real data under consistent snapshot, lineage traced
    E2_AUDITED = "E2_AUDITED"  # semantic audit passed, coverage thresholds met
    E3_FORMAL = "E3_FORMAL"  # release-scoped historical real data, replayable


class EconomicStatus(str, Enum):
    """Economic alpha evidence: has the strategy earned excess return?

    This is deliberately NOT derivable from execution integrity.
    """

    UNPROVEN = "UNPROVEN"  # no economic evidence yet
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"  # in-window research positive, no OOS
    OOS_VERIFIED = "OOS_VERIFIED"  # independent out-of-sample evidence passed gates
    ECONOMIC_FAILED = "ECONOMIC_FAILED"  # OOS / walk-forward / attribution failed


class CapitalStatus(str, Enum):
    """Capital deployment authority.  NEVER auto-derived from evidence."""

    BLOCKED = "BLOCKED"  # 0 CNY
    CANARY_50K = "CANARY_50K"  # 50K CNY canary (human-approved)
    CONTROLLED_500K = "CONTROLLED_500K"  # 500K controlled scale (human-approved)


# ---------------------------------------------------------------------------
# Decomposed status record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormalStatus:
    """The four decomposed status dimensions for one strategy/run."""

    execution_status: ExecutionStatus = ExecutionStatus.NOT_RUN
    data_status: DataStatus = DataStatus.E0_DIAGNOSTIC
    economic_status: EconomicStatus = EconomicStatus.UNPROVEN
    capital_status: CapitalStatus = CapitalStatus.BLOCKED

    def to_dict(self) -> dict[str, str]:
        return {
            "execution_status": self.execution_status.value,
            "data_status": self.data_status.value,
            "economic_status": self.economic_status.value,
            "capital_status": self.capital_status.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FormalStatus":
        return cls(
            execution_status=ExecutionStatus(str(payload.get("execution_status", "NOT_RUN"))),
            data_status=DataStatus(str(payload.get("data_status", "E0_DIAGNOSTIC"))),
            economic_status=EconomicStatus(str(payload.get("economic_status", "UNPROVEN"))),
            capital_status=CapitalStatus(str(payload.get("capital_status", "BLOCKED"))),
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

VALID_EXECUTION = {s.value for s in ExecutionStatus}
VALID_DATA = {s.value for s in DataStatus}
VALID_ECONOMIC = {s.value for s in EconomicStatus}
VALID_CAPITAL = {s.value for s in CapitalStatus}


def validate_status_dict(payload: dict[str, Any]) -> list[str]:
    """Return a list of blockers; empty list means the dict is a valid
    decomposed status record.  Fail-closed: unknown values are blockers."""
    blockers: list[str] = []
    execution = str(payload.get("execution_status", ""))
    if execution not in VALID_EXECUTION:
        blockers.append(f"invalid_execution_status:{execution}")
    data = str(payload.get("data_status", ""))
    if data not in VALID_DATA:
        blockers.append(f"invalid_data_status:{data}")
    economic = str(payload.get("economic_status", ""))
    if economic not in VALID_ECONOMIC:
        blockers.append(f"invalid_economic_status:{economic}")
    capital = str(payload.get("capital_status", ""))
    if capital not in VALID_CAPITAL:
        blockers.append(f"invalid_capital_status:{capital}")
    # Capital authority must be explicitly false unless a human-approved tier.
    if capital not in ("BLOCKED", "CANARY_50K", "CONTROLLED_500K"):
        blockers.append("capital_status_must_be_human_approved_tier")
    return blockers
