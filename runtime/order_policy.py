"""Release order policy — governs what orders a strategy release is authorized to emit.

The policy tier controls capital authority, rebalancing permission, and order-source
trust.  It is enforced at the order-generation boundary, not inside the execution
ledger (which remains a raw-simulation tool).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyTier(str, Enum):
    """Order authority tier for a strategy release."""

    # Full authority at approved principal: rebalance, buy, sell all permitted.
    PRODUCTION_APPROVED = "PRODUCTION_APPROVED"
    # Rebalance + buy permitted, but external capital injection is blocked.
    # Existing profits may compound; principal is capped at approved_principal.
    ACTIVE_FIXED_CAPITAL = "ACTIVE_FIXED_CAPITAL"
    # Maintain existing positions only — no new buys, sells for risk exit only.
    ACTIVE_EXISTING_ONLY = "ACTIVE_EXISTING_ONLY"
    # Track / shadow only — no real orders emitted, no capital committed.
    SHADOW = "SHADOW"
    # All new orders blocked; only risk-exit (sell) orders permitted.
    BLOCKED = "BLOCKED"


class ScalePolicy(str, Enum):
    """Capital scaling policy."""

    # No external capital injection permitted.  Profits may remain in the
    # account and compound, but the approved principal is a hard cap on
    # externally-sourced funds.
    NO_EXTERNAL_SCALE = "NO_EXTERNAL_SCALE"
    # Explicit approval required for any principal change (new release +
    # manual approval record).  The default for all non-production tiers.
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


ALLOWED_ORDER_SOURCES = frozenset({"trusted_live_snapshot"})
FORBIDDEN_ORDER_SOURCES = frozenset({"cli_override", "env_var_override"})


class ReleaseOrderPolicyConfig(BaseModel):
    """Immutable order-policy configuration loaded from YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="1.0")
    current_policy: PolicyTier = Field(default=PolicyTier.ACTIVE_FIXED_CAPITAL)
    scale_policy: ScalePolicy = Field(default=ScalePolicy.NO_EXTERNAL_SCALE)
    approved_principal: float = Field(default=0.0, ge=0)
    principal_currency: str = Field(default="CNY")
    approved_at: str = ""
    approved_by: str = ""
    require_release_for_principal_change: bool = Field(default=True)
    require_manual_approval_for_principal_change: bool = Field(default=True)
    allowed_order_sources: list[str] = Field(
        default_factory=lambda: sorted(ALLOWED_ORDER_SOURCES)
    )
    forbidden_order_sources: list[str] = Field(
        default_factory=lambda: sorted(FORBIDDEN_ORDER_SOURCES)
    )

    @model_validator(mode="after")
    def _validate_sources(self) -> "ReleaseOrderPolicyConfig":
        allowed = set(self.allowed_order_sources)
        forbidden = set(self.forbidden_order_sources)
        overlap = allowed & forbidden
        if overlap:
            raise ValueError(
                f"order sources in both allowed and forbidden: {sorted(overlap)}"
            )
        if not allowed.issubset(ALLOWED_ORDER_SOURCES):
            unknown = sorted(allowed - ALLOWED_ORDER_SOURCES)
            raise ValueError(f"unknown allowed order sources: {unknown}")
        if not forbidden.issubset(FORBIDDEN_ORDER_SOURCES):
            unknown = sorted(forbidden - FORBIDDEN_ORDER_SOURCES)
            raise ValueError(f"unknown forbidden order sources: {unknown}")
        return self


# ---------------------------------------------------------------------------
# Policy-allowed actions per tier
# ---------------------------------------------------------------------------

_REBALANCE_ALLOWED: dict[PolicyTier, bool] = {
    PolicyTier.PRODUCTION_APPROVED: True,
    PolicyTier.ACTIVE_FIXED_CAPITAL: True,
    PolicyTier.ACTIVE_EXISTING_ONLY: False,
    PolicyTier.SHADOW: False,
    PolicyTier.BLOCKED: False,
}

_BUY_ALLOWED: dict[PolicyTier, bool] = {
    PolicyTier.PRODUCTION_APPROVED: True,
    PolicyTier.ACTIVE_FIXED_CAPITAL: True,
    PolicyTier.ACTIVE_EXISTING_ONLY: False,
    PolicyTier.SHADOW: False,
    PolicyTier.BLOCKED: False,
}

_SELL_ALLOWED: dict[PolicyTier, bool] = {
    PolicyTier.PRODUCTION_APPROVED: True,
    PolicyTier.ACTIVE_FIXED_CAPITAL: True,
    PolicyTier.ACTIVE_EXISTING_ONLY: True,  # risk exit only
    PolicyTier.SHADOW: False,
    PolicyTier.BLOCKED: True,  # risk exit only
}

_EMIT_REAL_ORDERS: dict[PolicyTier, bool] = {
    PolicyTier.PRODUCTION_APPROVED: True,
    PolicyTier.ACTIVE_FIXED_CAPITAL: True,
    PolicyTier.ACTIVE_EXISTING_ONLY: True,
    PolicyTier.SHADOW: False,
    PolicyTier.BLOCKED: True,
}


class OrderPolicyGate:
    """Stateless validator that enforces the release order policy."""

    # ------------------------------------------------------------------
    # Order-level validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_order(
        *,
        side: str,
        policy: PolicyTier,
        approved_principal: float,
        current_capital: float,
        order_notional: float,
    ) -> tuple[bool, str]:
        """Return (allowed, reason) for a single order under *policy*.

        Parameters
        ----------
        side : ``"BUY"`` or ``"SELL"``
        policy : current tier
        approved_principal : principal cap in account currency
        current_capital : total capital (cash + positions) before this order
        order_notional : gross notional of this order (shares × price)
        """
        side_upper = str(side).upper().strip()
        if side_upper not in ("BUY", "SELL"):
            return False, f"unknown_side:{side}"

        if side_upper == "BUY" and not _BUY_ALLOWED.get(policy, False):
            return False, f"buy_blocked_by_policy:{policy.value}"
        if side_upper == "SELL" and not _SELL_ALLOWED.get(policy, False):
            return False, f"sell_blocked_by_policy:{policy.value}"

        # ACTIVE_FIXED_CAPITAL: total capital after buy must not exceed
        # approved_principal (prevents external injection).
        if policy == PolicyTier.ACTIVE_FIXED_CAPITAL and side_upper == "BUY":
            if current_capital + order_notional > approved_principal + 1e-9:
                return (
                    False,
                    f"capital_injection_blocked: "
                    f"current={current_capital:.2f} + order={order_notional:.2f} "
                    f"exceeds approved={approved_principal:.2f}",
                )

        return True, ""

    @staticmethod
    def validate_rebalance(
        *,
        policy: PolicyTier,
    ) -> tuple[bool, str]:
        """Return (allowed, reason) for a full rebalance cycle."""
        if not _REBALANCE_ALLOWED.get(policy, False):
            return False, f"rebalance_blocked_by_policy:{policy.value}"
        return True, ""

    @staticmethod
    def validate_capital_change(
        *,
        new_principal: float,
        old_principal: float,
        scale_policy: ScalePolicy,
    ) -> tuple[bool, str]:
        """Return (allowed, reason) for a principal change.

        Under NO_EXTERNAL_SCALE, the principal can only increase via retained
        profits (i.e. the account NAV organically) — a *declared* principal
        increase is always an external injection and is blocked.
        """
        if abs(new_principal - old_principal) < 1e-9:
            return True, ""
        if scale_policy == ScalePolicy.NO_EXTERNAL_SCALE:
            return (
                False,
                f"external_capital_injection_blocked: "
                f"{old_principal:.2f} → {new_principal:.2f}",
            )
        return True, ""

    # ------------------------------------------------------------------
    # Order-source validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_order_source(source: str) -> tuple[bool, str]:
        """Return (allowed, reason) for an order's data source.

        Orders MUST come from a trusted live snapshot.  CLI overrides and
        environment-variable overrides are forbidden regardless of policy tier.
        """
        source_key = str(source).strip().lower()
        if source_key in FORBIDDEN_ORDER_SOURCES:
            return False, f"forbidden_order_source:{source_key}"
        if source_key not in ALLOWED_ORDER_SOURCES:
            return False, f"unrecognized_order_source:{source_key}"
        return True, ""

    # ------------------------------------------------------------------
    # Convenience: policy gate summary
    # ------------------------------------------------------------------

    @staticmethod
    def gate_summary(policy: PolicyTier) -> dict[str, Any]:
        """Return a human-readable summary of what *policy* permits."""
        return {
            "policy": policy.value,
            "rebalance_allowed": _REBALANCE_ALLOWED.get(policy, False),
            "buy_allowed": _BUY_ALLOWED.get(policy, False),
            "sell_allowed": _SELL_ALLOWED.get(policy, False),
            "emits_real_orders": _EMIT_REAL_ORDERS.get(policy, False),
        }
