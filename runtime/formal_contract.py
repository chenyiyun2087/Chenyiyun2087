"""Shared formal evidence contract — single source of truth for strategy set,
canonical hashing, and cross-module identity constants.

All Producers, Validators, and PR-I must import from here — never duplicate.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# ---------------------------------------------------------------------------
# Frozen five-strategy admission set
# ---------------------------------------------------------------------------
FORMAL_STRATEGIES = frozenset({
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
})

# ---------------------------------------------------------------------------
# Canonical SHA-256 — MUST be the single implementation used by ALL
# Producers, Validators, and PR-I.  Do NOT copy-paste serialisation args.
# ---------------------------------------------------------------------------


def canonical_sha(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a JSON-serialisable dict."""
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
