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
from runtime.contracts import (
    EvidenceStatus,
    ExecutionTrace,
    LedgerReconciliationReport,
    ManualFill,
    PortfolioRiskDecision,
    ProductionState,
    ReleaseIdentity,
    SnapshotManifest,
)
from runtime.evidence_store import EvidenceStore
from runtime.portfolio_risk import evaluate_portfolio_risk, assert_buy_allowed
from runtime.independent_ledger import replay_orders
from runtime.ledger_reconciliation import reconcile_ledgers
from runtime.open_execution import evaluate_opening_execution
from runtime.llm_governance import validate_llm_feature_usage
from runtime.production_stability_hold import (
    ProductionStabilityHold,
    ProductionUpgradePaused,
    assert_production_upgrade_allowed,
    load_production_stability_hold,
)

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
    "ReleaseIdentity",
    "SnapshotManifest",
    "EvidenceStore",
    "LedgerReconciliationReport",
    "PortfolioRiskDecision",
    "ManualFill",
    "ExecutionTrace",
    "ProductionState",
    "EvidenceStatus",
    "evaluate_portfolio_risk",
    "assert_buy_allowed",
    "replay_orders",
    "reconcile_ledgers",
    "evaluate_opening_execution",
    "validate_llm_feature_usage",
    "ProductionStabilityHold",
    "ProductionUpgradePaused",
    "assert_production_upgrade_allowed",
    "load_production_stability_hold",
]
