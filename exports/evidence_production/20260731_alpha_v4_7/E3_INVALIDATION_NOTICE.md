# E3 Evidence Invalidated — 2026-07-31

## Reason

The Alpha v4.7 E3 conclusion was rejected by independent review.
All E3-claimed evidence (panel, factor validation, net alpha, rolling windows)
produced during the 2026-07-31 upgrade session has been moved to this directory.

## Review Findings (Summary)

| Severity | Issue |
|----------|-------|
| P0 | Hardcoded MySQL root credentials committed to repository |
| P0 | Builder auto-fills missing `available_at` with signal time (bypasses PIT gate) |
| P0 | Builder fills NaN PB with 0.0 before coverage check (vacuous financial coverage) |
| P0 | `evidence_origin: HISTORICAL_REAL` is a self-declared config string with no verification |
| P0 | Adapter→Builder provenance chain broken (TOCTOU: manifest modified after adapter PASS) |
| P0 | Runner scripts not reproducible as committed |
| P1 | Builder enforces only a subset of config gates (11 of ~25) |
| P1 | Documentation contradicts committed code state |
| P1 | All alpha_v3–v4_6 profiles aliased to v4.7 (destroys historical reproducibility) |

## Current Status

- Historical Evidence: **E0 / INVALIDATED**
- Research: **BLOCKED**
- Trading: **BLOCKED**
- Capital: **NO_SCALE / 0 CNY**
- Canary: **CLOSED**
- Broker API: **CLOSED**
- `capital_authority: false` retained

## Recovery Path

See remediation plan at: `.claude/plans/magical-hopping-feigenbaum.md`

1. Phase 1: Rotate credentials, quarantine E3 claims
2. Phase 2: Fix data contracts (remove auto-fill, real PIT semantics)
3. Phase 3: Rebuild immutable pipeline (no post-adapter modification)
4. Phase 4: Add regression tests and CI enforcement
