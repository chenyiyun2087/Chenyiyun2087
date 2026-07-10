"""Unified runtime decision layer.

Serves backtest replay, daily candidate export, shadow monitoring, and canary
execution from the same deterministic functions. No direct dependency on
scripts/research/* — all strategy logic lives here or in the production config.
"""

from runtime.release_manifest import ReleaseManifest, freeze_production_release
from runtime.decision_engine import generate_targets
from runtime.governance import ensure_governance_schema, persist_evidence, persist_release, write_evidence_package
from runtime.provenance import ProvenanceEnvelope
from runtime.release_registry import get_release, load_release_registry

__all__ = [
    "ReleaseManifest",
    "freeze_production_release",
    "generate_targets",
    "ensure_governance_schema",
    "persist_release",
    "persist_evidence",
    "write_evidence_package",
    "ProvenanceEnvelope",
    "get_release",
    "load_release_registry",
]
