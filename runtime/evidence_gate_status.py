"""Shared three-layer evidence gate helpers.

This tiny module is intentionally free of filesystem writes.  It gives
readiness reports, registry migration, and runtime verifiers one vocabulary
and one resolver.  In particular, ``path.exists()`` can only set the artifact
layer; it never turns the economic layer green.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from runtime.formal_status_semantics import (
    ArtifactStatus,
    ContractStatus,
    EconomicGateStatus,
    GateEconomicStatus,
    GateStatus,
    make_gate_status,
    resolve_gate_status,
    validate_gate_status_dict,
)


def artifact_status_for(path: Path | str | None) -> ArtifactStatus:
    """Return presence only; callers must parse/validate separately."""

    if path is None or not Path(path).is_file():
        return ArtifactStatus.MISSING
    return ArtifactStatus.ARTIFACT_PRESENT


def evaluate_gate(
    *,
    artifact: Path | str | None = None,
    artifact_status: ArtifactStatus | str | None = None,
    contract_status: ContractStatus | str = ContractStatus.NOT_EVALUATED,
    economic_status: GateEconomicStatus | str = GateEconomicStatus.NOT_EVALUATED,
    contract_checker: Callable[[], bool] | None = None,
    economic_checker: Callable[[], bool] | None = None,
    required_dimensions: Iterable[str] = ("artifact", "contract", "economic"),
    reasons: Iterable[str] = (),
) -> GateStatus:
    """Build one gate status without inferring economic success from a file."""

    if artifact_status is None:
        artifact_status = artifact_status_for(artifact)
    if contract_checker is not None:
        contract_status = ContractStatus.CONTRACT_VALID if contract_checker() else ContractStatus.INVALID
    if economic_checker is not None:
        economic_status = GateEconomicStatus.ECONOMIC_PASS if economic_checker() else GateEconomicStatus.FAIL
    status = make_gate_status(
        artifact_status=artifact_status,
        contract_status=contract_status,
        economic_status=economic_status,
        required_dimensions=tuple(required_dimensions),
        reasons=tuple(reasons),
    )
    return status


def resolved_status(payload: GateStatus | Mapping[str, Any], *, required_dimensions: Iterable[str] | None = None) -> str:
    return resolve_gate_status(payload, required_dimensions=required_dimensions)


__all__ = [
    "ArtifactStatus", "ContractStatus", "GateEconomicStatus", "EconomicGateStatus",
    "GateStatus", "artifact_status_for", "evaluate_gate", "resolved_status",
    "resolve_gate_status", "validate_gate_status_dict",
]
