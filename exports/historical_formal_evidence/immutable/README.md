# Historical Formal Evidence — IMMUTABLE ZONE (v5.4.1)

## Rules
- **Never modified** by any daily task, pipeline, or research script.
- Formal scores, PIT snapshots, and historical run manifests live here
  (or remain in place under `exports/formal_evidence/` — the two locations
  are equivalent: both are immutable by contract).
- Any write attempt by a daily/shadow task MUST fail (see
  `test_forward_shadow_immutability.py` once Shadow Engine v2 lands).
- Corrections require a NEW release/run directory — never in-place edits.

## Enforcement
- v5.4.1: shadow tasks disabled (`task_registry/pipeline.yaml`).
- v5.5: `build_daily_alpha_signal_package.py` verifies historical SHAs
  before sealing each daily package; mutation -> `SIGNAL_PACKAGE_BLOCKED`.

Evidence here: `exports/formal_evidence/` (PIT releases, formal scores,
strict-ledger run manifests, registry) — all content-addressed.
