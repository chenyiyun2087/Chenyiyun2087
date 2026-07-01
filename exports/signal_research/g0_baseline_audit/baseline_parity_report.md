# G0 Baseline Parity Audit Report
Generated: 2026-07-01T13:43:32.852109

## Summary
- Legacy NAV end: 2.2098
- Meta Curve A NAV end: 3.0836
- Legacy return: 124.20%
- Meta Curve A return: -69.35%
- Legacy max DD: -17.60%
- Meta Curve A max DD: -73.71%
- First divergence date: None

## Root Causes
- ⚠️  INITIAL CAPITAL: Legacy=500,000, Meta=10,000,000 — different by 1900%
- ⚠️  POSITION RATIO: Legacy=70%, Meta=65%
- 🔴 EXECUTION FRAMEWORK: Legacy uses full ExecutionLedger + M7 rules + risk governor
- 🔴 EXECUTION FRAMEWORK: Meta Curve A uses simplified ShadowAccount (no strict ledger, no M7 exits)
- 🔴 PRICE BASIS: Legacy uses raw_close for precommit, raw_open for execution
- 🔴 PRICE BASIS: Meta Curve A uses adj_open for execution, adj_close for NAV
- 🔴 RISK GOVERNANCE: Legacy has adaptive position scaling (0.32~0.80)
- 🔴 RISK GOVERNANCE: Meta Curve A uses fixed position_ratio=0.65
- 🔴 FORCED EXITS: Legacy has trailing stops, time stops, score exits
- 🔴 FORCED EXITS: Meta Curve A has no forced exits (only hold_days lock)

## Verdict
❌ G0 FAILED — Meta Curve A is not comparable to legacy backtest