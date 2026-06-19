"""Execution-mode contracts for execution-safe uplift research.

Only strict precommit is causal with the repository's daily bars. Daily proxy
approximations exist for sensitivity analysis and are permanently non-promotable.
"""

from __future__ import annotations

from dataclasses import dataclass


STRICT_MODE = "strict_t1_open_precommit"
AUCTION_MODE = "auction_0925_preflight"
POST_OPEN_MODE = "post_open_1m_fallback"
MODES = (STRICT_MODE, AUCTION_MODE, POST_OPEN_MODE)


@dataclass(frozen=True)
class ExecutionModeAudit:
    execution_mode: str
    causality_pass: bool
    daily_proxy_approximation: bool
    promotion_eligible: bool
    status: str


def execution_mode_audit(mode: str, allow_daily_proxy_approximation: bool) -> ExecutionModeAudit:
    if mode == STRICT_MODE:
        return ExecutionModeAudit(mode, True, False, True, "STRICT_PRECOMMIT_CAUSAL")
    if mode in {AUCTION_MODE, POST_OPEN_MODE}:
        if not allow_daily_proxy_approximation:
            raise RuntimeError(f"{mode} requires timestamped auction/minute market data; pass --allow-daily-proxy-approximation only for non-causal exploration.")
        return ExecutionModeAudit(mode, False, True, False, "NON_CAUSAL_EXPLORATION_ONLY")
    raise RuntimeError(f"Unknown execution mode: {mode}")
