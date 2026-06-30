# Dual-ledger Reconciliation and Mismatch Report

## Result

`NOT_VERIFIABLE` — no numerical dual-ledger equality claim is made.

The strict smoke replay failed closed before loading account data because verified corporate-action and security-lifecycle snapshots were absent. Additionally, `runtime/ledger_runtime.py` exposes order-state validation, position-weight checks, and a reconciliation contract, but not an independent order-fill/cash/NAV replay engine. The strict ledger is therefore not presently paired with a second executable ledger capable of producing the requested independent account result.

## Consequence

- Cash, holding, quantity, trade-count and NAV residuals remain blank rather than being imputed as zero.
- Historical strict-ledger self-reconciliation is not treated as dual-ledger reconciliation.
- All strategies with historical account evidence are blocked from a production-usable conclusion for this audit.
- `LEDGER_MISMATCH` is not asserted because no pair of independently computed ledgers exists to compare; the stronger evidence label here is `NOT_VERIFIABLE`.

## Required remediation evidence

1. Versioned corporate-action source, atomic snapshot and manifest.
2. Versioned listed/suspended lifecycle source, authoritative calendar panel and manifest.
3. A runtime account replay implementation that consumes the same frozen events as the strict ledger.
4. Per-order and per-day reconciliation with explicit rounding tolerances and explained residuals.
