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
from typing import Any, Iterable, Mapping

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
# Three-layer gate semantics
# ---------------------------------------------------------------------------


class ArtifactStatus(str, Enum):
    """Presence and integrity of the evidence object a gate consumes.

    ``ARTIFACT_PRESENT`` means that the object was found *and* parsed by the
    producer.  A path merely existing is intentionally not enough: callers
    should report ``ARTIFACT_INVALID`` when the object is corrupt, stale, or
    does not satisfy its own schema.
    """

    ARTIFACT_PRESENT = "ARTIFACT_PRESENT"
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    # Compatibility names.  Their serialized values remain canonical.
    MISSING = "ARTIFACT_MISSING"
    INVALID = "ARTIFACT_INVALID"


class ContractStatus(str, Enum):
    """Whether the semantic/replay contract for an artifact was proved."""

    CONTRACT_VALID = "CONTRACT_VALID"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    CONTRACT_NOT_EVALUATED = "CONTRACT_NOT_EVALUATED"
    # Compatibility names.  Their serialized values remain canonical.
    INVALID = "CONTRACT_INVALID"
    NOT_EVALUATED = "CONTRACT_NOT_EVALUATED"


class GateEconomicStatus(str, Enum):
    """Economic qualification is independent from artifact correctness."""

    ECONOMIC_PASS = "ECONOMIC_PASS"
    ECONOMIC_FAIL = "ECONOMIC_FAIL"
    ECONOMIC_NOT_EVALUATED = "ECONOMIC_NOT_EVALUATED"
    ECONOMIC_NOT_APPLICABLE = "ECONOMIC_NOT_APPLICABLE"
    # Compatibility names.  Their serialized values remain canonical.
    FAIL = "ECONOMIC_FAIL"
    NOT_EVALUATED = "ECONOMIC_NOT_EVALUATED"
    NOT_APPLICABLE = "ECONOMIC_NOT_APPLICABLE"


# Several external readers use the more natural ``EconomicGateStatus`` name;
# keep one canonical enum while making the spelling explicit and discoverable.
EconomicGateStatus = GateEconomicStatus


def _raw_value(value: object) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _coerce_artifact(value: object) -> ArtifactStatus:
    raw = _raw_value(value)
    return ArtifactStatus({"MISSING": "ARTIFACT_MISSING", "INVALID": "ARTIFACT_INVALID"}.get(raw, raw))


def _coerce_contract(value: object) -> ContractStatus:
    raw = _raw_value(value)
    return ContractStatus({"INVALID": "CONTRACT_INVALID", "NOT_EVALUATED": "CONTRACT_NOT_EVALUATED"}.get(raw, raw))


def _coerce_economic(value: object) -> GateEconomicStatus:
    raw = _raw_value(value)
    return GateEconomicStatus({
        "FAIL": "ECONOMIC_FAIL",
        "NOT_EVALUATED": "ECONOMIC_NOT_EVALUATED",
        "NOT_APPLICABLE": "ECONOMIC_NOT_APPLICABLE",
    }.get(raw, raw))


_GATE_DIMENSIONS = ("artifact", "contract", "economic")


@dataclass(frozen=True)
class GateStatus:
    """The canonical status of one evidence gate.

    ``resolved_status`` is deliberately derived, never accepted from an
    untrusted producer.  A gate can resolve to PASS only when each required
    dimension is green.  Economic ``NOT_APPLICABLE`` is green only when the
    gate explicitly omits the economic dimension from ``required_dimensions``.
    """

    artifact_status: ArtifactStatus = ArtifactStatus.ARTIFACT_MISSING
    contract_status: ContractStatus = ContractStatus.CONTRACT_NOT_EVALUATED
    economic_status: GateEconomicStatus = GateEconomicStatus.ECONOMIC_NOT_EVALUATED
    required_dimensions: tuple[str, ...] = _GATE_DIMENSIONS
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_status", _coerce_artifact(self.artifact_status))
        object.__setattr__(self, "contract_status", _coerce_contract(self.contract_status))
        object.__setattr__(self, "economic_status", _coerce_economic(self.economic_status))
        dims = tuple(self.required_dimensions)
        unknown = [d for d in dims if d not in _GATE_DIMENSIONS]
        if unknown:
            raise ValueError(f"unknown_gate_dimensions:{unknown}")
        object.__setattr__(self, "required_dimensions", dims)
        object.__setattr__(self, "reasons", tuple(str(r) for r in self.reasons))

    @property
    def resolved_status(self) -> str:
        return resolve_gate_status(self)

    @property
    def resolved(self) -> str:
        """Short alias retained for report/fixture consumers."""
        return self.resolved_status

    @property
    def passed(self) -> bool:
        return self.resolved_status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_status": self.artifact_status.value,
            "contract_status": self.contract_status.value,
            "economic_status": self.economic_status.value,
            "required_dimensions": list(self.required_dimensions),
            "resolved_status": self.resolved_status,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GateStatus":
        return cls(
            artifact_status=_coerce_artifact(payload.get("artifact_status", "ARTIFACT_MISSING")),
            contract_status=_coerce_contract(payload.get("contract_status", "CONTRACT_NOT_EVALUATED")),
            economic_status=_coerce_economic(payload.get("economic_status", "ECONOMIC_NOT_EVALUATED")),
            required_dimensions=tuple(payload.get("required_dimensions") or _GATE_DIMENSIONS),
            reasons=tuple(payload.get("reasons") or ()),
        )


def resolve_gate_status(
    gate: GateStatus | Mapping[str, Any],
    required_dimensions: Iterable[str] | None = None,
) -> str:
    """Resolve a gate without trusting a producer-supplied PASS field.

    The return vocabulary intentionally stays tiny (``PASS``/``BLOCKED``),
    while the three source dimensions retain the precise failure reason.  A
    missing artifact or unevaluated required contract can therefore never be
    mistaken for an economic pass merely because a file exists.
    """

    if not isinstance(gate, GateStatus):
        gate = GateStatus.from_dict(dict(gate))
    dimensions = tuple(gate.required_dimensions if required_dimensions is None else required_dimensions)
    if "artifact" in dimensions and gate.artifact_status is not ArtifactStatus.ARTIFACT_PRESENT:
        return "BLOCKED"
    if "contract" in dimensions and gate.contract_status is not ContractStatus.CONTRACT_VALID:
        return "BLOCKED"
    if "economic" in dimensions and gate.economic_status is not GateEconomicStatus.ECONOMIC_PASS:
        return "BLOCKED"
    return "PASS"


def make_gate_status(
    *,
    artifact_status: ArtifactStatus | str = ArtifactStatus.ARTIFACT_MISSING,
    contract_status: ContractStatus | str = ContractStatus.CONTRACT_NOT_EVALUATED,
    economic_status: GateEconomicStatus | str = GateEconomicStatus.ECONOMIC_NOT_EVALUATED,
    required_dimensions: Iterable[str] = _GATE_DIMENSIONS,
    reasons: Iterable[str] = (),
) -> GateStatus:
    """Convenience constructor used by readiness/reporting producers."""

    def _value(value: object) -> str:
        return value.value if isinstance(value, Enum) else str(value)

    return GateStatus(
        artifact_status=_coerce_artifact(_value(artifact_status)),
        contract_status=_coerce_contract(_value(contract_status)),
        economic_status=_coerce_economic(_value(economic_status)),
        required_dimensions=tuple(required_dimensions),
        reasons=tuple(reasons),
    )


def validate_gate_status_dict(payload: Mapping[str, Any]) -> list[str]:
    """Return fail-closed blockers for a serialized three-layer gate."""

    blockers: list[str] = []
    for key, enum_type in (
        ("artifact_status", ArtifactStatus),
        ("contract_status", ContractStatus),
        ("economic_status", GateEconomicStatus),
    ):
        raw = payload.get(key)
        value = _raw_value(raw or "")
        try:
            if enum_type is ArtifactStatus:
                _coerce_artifact(value)
            elif enum_type is ContractStatus:
                _coerce_contract(value)
            else:
                _coerce_economic(value)
        except ValueError:
            blockers.append(f"invalid_{key}:{value}")
    dims = payload.get("required_dimensions", list(_GATE_DIMENSIONS))
    if not isinstance(dims, (list, tuple)):
        blockers.append("required_dimensions_not_sequence")
    else:
        blockers.extend(f"unknown_required_dimension:{d}" for d in dims if d not in _GATE_DIMENSIONS)
    # Producer supplied resolved fields are informational only.  If present,
    # verify they agree with the computed value; never let them unlock a gate.
    try:
        computed = resolve_gate_status(payload)
        declared = payload.get("resolved_status")
        if declared is not None and str(declared) != computed:
            blockers.append(f"resolved_status_mismatch:{declared}!={computed}")
    except (TypeError, ValueError):
        # The enum errors above are more useful than a second parser trace.
        pass
    return blockers


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
