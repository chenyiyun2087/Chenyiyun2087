# VLS Turnover-Penalty Study — Score-Buffer Rebalancing (2026-08-02)

## Headline

**Score-buffer rebalancing cuts turnover 78–88% and cost 72–83% on the
champion cell (t10/h20) at near-parity return, and turns the 2x-cost stress
from +85.9% into +94.9%. The b=0.05 "spike" (+249%) is a concentration
artifact — a single position compounding to 51% — NOT selection alpha.**

## Method

- New research flag `--rebalance-score-buffer` on
  `research_trusted_strategy_account_backtest.py` (default 0.0; a b=0.0
  rerun reproduces the baseline bit-for-bit: +108.0%, 856 trades, 42.8K
  cost — production default path untouched).
- Semantics: at each rebalance an unlocked holding is kept (locked,
  budget-occupying, no re-weight — extending the existing hold-days lock)
  when its current-day ranking score ≥ (N-th target cutoff − buffer).
- Buffer is **absolute in ranking-score units**: the VLS snapshot score is a
  per-day cross-sectional z-score (σ 0.19; daily top-10 cutoff ≈ 0.33 ± 0.04),
  so a relative cutoff buffer would be ill-defined. Buffer 0.10 ≈ 30% of the
  typical cutoff.
- Same formal PIT pipeline as the parent study: frozen cc3890 inputs,
  python3.14, strict T+1 open precommit, 0.075% + 0.10% costs, 2022–2024,
  vls_mom_contrarian_v1 fixed-weight scores.

## Results (champion cell t10/h20, 0.075%+0.10% costs)

| Buffer | Total | Annual | MDD | Trades | Cost | Max pos weight |
|--------|-------|--------|-----|--------|------|----------------|
| 0.00 (base) | +108.0% | +30.0% | -37.0% | 856 | 42.8K | 0.28 |
| 0.03 | +109.5% | +30.3% | -34.0% | 498 | 31.2K | 0.21 |
| 0.04 | +129.3% | +34.6% | -37.0% | 448 | 31.3K | 0.29 |
| **0.05** | **+249.1%** | +56.4% | -36.6% | 408 | 40.3K | **0.51 ⚠** |
| 0.10 | +101.8% | +28.6% | **-32.8%** | 188 | 11.9K | **0.16** |
| 0.15 | +102.7% | +28.8% | **-30.0%** | 102 | 7.2K | 0.28 |
| 0.20 | +113.5% | +31.2% | -37.1% | 64 | 4.1K | 0.22 |

Sell reasons at b=0.10: only 89 `rebalance_unlocked` sells vs 382 at baseline.

### Cost-resilience: 2x costs (0.15% + 0.20%)

| Config | Total |
|--------|-------|
| t10/h20 base, 2x costs | +85.9% |
| t10/h20 buffer 0.10, 2x costs | **+94.9%** |

Under doubled costs the buffer is a net win (+9pp): it buys fewer executions
at exactly the point where executions are expensive.

### Robustness cells (buffer 0.10)

| Cell | Base | Buffer 0.10 | Δ |
|------|------|-------------|---|
| t10/h30 | +74.2% (526 tr, 25.2K) | **+117.1%** (168 tr, 11.3K) | +42.9pp |
| t5/h30 | +96.3% (250 tr, 27.1K) | **+110.7%** (111 tr, 14.7K) | +14.4pp |

Longer-hold cells improve more — their low baseline turnover is cut in half
again while return rises.

## Concentration analysis — why b=0.05 is not alpha

Max single-position weight by buffer: 0.28 → 0.21 → 0.29 → **0.51** → 0.16 →
0.28 → 0.22. The b=0.04→0.05 step is +120pp for a 0.01 buffer increment — a
regime change, not a smooth edge. At b=0.05 two names (605 at 43.9%, 2394 at
31.5%) compound unchecked because buffer-kept positions are never re-weighted;
those runs carry the entire +249%. At b=0.10 the kept set is stable and
diversified (max weight 0.16).

This is real money in the backtest but a wide-outcome bet — one or two names
decide the result. It must not be quoted as selection alpha.

## Interpretation

1. **b=0.03–0.04**: near-free lunch — equal/higher return (+1.5/+21pp),
   turnover −42/−48%, no concentration change (max weight ≤ 0.29).
2. **b=0.10–0.15**: execution-resilience setting — trades −78/−88%, cost
   −72/−83%, MDD −4 to −7pp better, return −5pp drag; strictly better than
   baseline under 2x costs.
3. **b=0.05**: avoid — concentration cliff with a wide outcome distribution.
4. **b=0.20+**: turnover keeps falling but MDD/return become non-monotonic
   (name-mix noise); no benefit over 0.10–0.15.

Non-monotonic returns across buffers mean per-cell return margins are noise;
only the turnover/cost/MDD effects are robust.

## Honest caveats

1. Same 2022–2024 window and same-period factor selection as the parent study;
   research-mode ledger (PARTIAL_UNVERIFIED) — directional evidence.
2. Buffer-kept positions are never re-weighted: at large buffers the strategy
   becomes "hold until the score decays", a different strategy than 20-day
   rebalancing.
3. The b=0.05 result must not be quoted as alpha (concentration artifact).
4. Benchmark remains pool-equal-weight; costs are the only frictions modeled.

## Recommendation

Champion cell stays t10/h20. If promotion is pursued, adopt **buffer 0.10** as
the turnover-penalized configuration (or run both b=0.0 and b=0.10 as formal
immutable runs). The buffer flag is research-only; the production default path
is unchanged.

## Next steps

- Formal immutable runs of champion t10/h20 with b=0.10 if promotion pursued.
- Walk-forward / DSR-PBO once 2018–2021 panel history exists.
- Bounded alternative to the unbounded freeze: re-weight kept positions when
  weight drift exceeds a band (e.g., ±50% relative) — keeps buffer's turnover
  benefit without the concentration tail.

---

# Addendum — Drift-Band Reweighting (same session, commit 642c52a9+)

## Headline

**New engine controls (`--rebalance-weight-drift-band` + a fresh-entry weight
cap) bound the concentration tail: max single-position weight drops 0.51 →
0.32 in the b=0.05 zone and stays 0.16 in the b=0.10 zone. The bounded b=0.05
edge survives at +172.7–183.8% (vs +249.1% unbounded, +108.0% baseline) — the
small-buffer "hold winners longer" effect is not just the concentration
artifact. Verified b=0/band=0 path is behavior-identical (all 705 nav rows
identical; only a metadata column added).**

## Mechanism discovered — the real cause of the b=0.05 cliff

The concentration was not (only) compounding of kept positions. With the
buffer freezing ~9 of 10 slots, the adjustable budget (target gross − locked
value) normalizes into the 1–2 remaining slots: **fresh buys entered at
0.37–0.51 weight** (symbol 605: 12,600 shares = 57% of initial cash on what
should be a 1/10 equal-weight buy). Fixes:

1. **Fresh-entry cap**: fresh names are capped at per-slot target × (1+band);
   residual budget stays as cash (engine already tolerates cash residuals).
2. **Kept-position trims** at band edge (overweight-only — no forced top-ups
   into laggards) handle compounding.

## Grid results (t10/h20, 0.075% + 0.10% costs unless noted)

| Config | Total | Annual | MDD | Trades | Cost | Max weight |
|--------|-------|--------|-----|--------|------|------------|
| b=0.05 unbounded | +249.1% | +56.4% | -36.6% | 408 | 40.3K | **0.51** |
| b=0.05 + band 0.25 | +172.7% | +43.2% | -37.7% | 421 | 32.9K | **0.32** |
| b=0.05 + band 0.50 | +175.7% | +43.8% | -37.7% | 410 | 33.9K | **0.32** |
| b=0.05 + band 0.75 | +183.8% | +45.3% | -37.4% | 409 | 34.6K | **0.32** |
| b=0.10 + band 0.25 | +103.4% | +28.9% | -33.0% | 200 | 12.1K | 0.16 |
| b=0.10 + band 0.50 | +101.9% | +28.6% | -32.8% | 188 | 11.9K | 0.16 |
| b=0.10 + band 0.75 | +101.8% | +28.6% | -32.8% | 188 | 11.9K | 0.16 |
| b=0.10 + band 0.50, 2x cost | +95.3% | +27.1% | -32.8% | 189 | 23.5K | 0.16 |

## Findings

1. **Band width barely matters** (0.25→0.75: +172.7→+183.8, identical max
   weight 0.32) — the fresh-entry cap does the bounding work; kept-position
   trims are rare (2 trims at band 0.5 with the cap vs 27 without).
2. **Bounded b=0.05 excess is spread across periods**: 2022H2 +17.4pp,
   2023H1 +6.8pp vs baseline; 2024H1 crisis −23.7% ≈ baseline −23.9%. The
   +249→+176 collapse is the removal of the 2022H2 605-run (44% position) —
   the unbounded premium was that concentration.
3. **b=0.10 zone is unaffected by the band** (already diversified); 2x cost
   +95.3% ≈ +94.9% without band. The band matters exactly where the buffer
   concentrates (small buffers).
4. **The band bounds weight, not beta**: MDD at the b=0.05 zone stays
   −37~−38%, same 2024H1 small-cap crisis exposure.

## Honest caveats

- Per-cell return differences remain within outcome noise (the buffer curve
  is non-monotonic); robust claims are turnover/cost/MDD/concentration.
- The bounded b=0.05 +176% is front-loaded (2022H2–2023H1) — regime-specific
  until walk-forward confirms it.
- Research-mode ledger (PARTIAL_UNVERIFIED); same 2022–2024 window.

## Recommendation

If promotion is pursued: champion t10/h20 with **b=0.10** (execution-
resilience, max weight 0.16) as primary; **b=0.05 + band 0.50** as the
higher-return variant (+175.7%, max weight 0.32) only if walk-forward
confirms the hold-winners-longer effect. b=0.05 without band remains
disqualified (concentration cliff).

## Next steps

- Formal immutable runs of champion t10/h20 with b=0.10 and with b=0.05+band
  if promotion pursued.
- Walk-forward / DSR-PBO once 2018–2021 panel history exists.
