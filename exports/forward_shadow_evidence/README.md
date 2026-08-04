# Forward Shadow Evidence — APPEND-ONLY ZONE (v5.4.1)

## Rules
- The ONLY zone that may accumulate true-forward evidence (E4).
- Append-only: existing packages are never rewritten.  A correction
  creates `revision_2/` next to the original.
- Structure:
  - `packages/YYYY-MM-DD/` — sealed daily Signal Packages (v5.5
    `build_daily_alpha_signal_package.py`)
  - `execution/` — order/fill records from the shadow state machine
  - `status/` — running shadow status/gates

## Status (2026-08-04)
Empty until Shadow Engine v2 (v5.5) passes its integration suite.  The
2026-08-03 pre-v5.5 records are NOT here — they are isolated under
`exports/forward_shadow_smoke_tests/20260803/` (evidence_eligible=false).
