"""Unified runtime decision layer.

Serves backtest replay, daily candidate export, shadow monitoring, and canary
execution from the same deterministic functions. No direct dependency on
scripts/research/* — all strategy logic lives here or in the production config.
"""

from runtime.release_manifest import ReleaseManifest, freeze_production_release
from runtime.decision_engine import generate_targets

__all__ = ["ReleaseManifest", "freeze_production_release", "generate_targets"]
