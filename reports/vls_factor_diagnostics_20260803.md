# VLS Factor IC + Attribution Diagnostics

Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)
Composite score = 0.30*value + 0.25*size + 0.25*liquidity + 0.20*(-momentum)
IC convention: engine-identical forward returns (T+1 open entry, exit at entry+hold_days close)
Pre-registered diagnostic 2026-08-03 (readiness gates factor_ic / alpha_attribution); frozen parameters untouched.

## 1. Factor rank IC at strategy hold (20d), per window

| Window | Factor | Mean IC | ICIR | Pos ratio | Days |
|---|---|---|---|---|---|
| pre_history_2020_2021 | liquidity | +0.0613 | +0.38 | 0.72 | 408 |
| pre_history_2020_2021 | market_beta | -0.0044 | -0.04 | 0.46 | 408 |
| pre_history_2020_2021 | momentum | -0.0536 | -0.51 | 0.28 | 408 |
| pre_history_2020_2021 | score | +0.0347 | +0.19 | 0.61 | 408 |
| pre_history_2020_2021 | size | +0.0244 | +0.15 | 0.60 | 408 |
| pre_history_2020_2021 | value | -0.0327 | -0.21 | 0.39 | 408 |
| pre_history_2020_2021 | volatility | +0.1006 | +0.74 | 0.76 | 408 |
| validation_2022 | liquidity | +0.0726 | +0.72 | 0.75 | 242 |
| validation_2022 | market_beta | +0.0582 | +0.38 | 0.64 | 242 |
| validation_2022 | momentum | -0.0896 | -0.68 | 0.29 | 242 |
| validation_2022 | score | +0.0488 | +0.39 | 0.70 | 242 |
| validation_2022 | size | +0.0304 | +0.27 | 0.61 | 242 |
| validation_2022 | value | -0.0451 | -0.29 | 0.37 | 242 |
| validation_2022 | volatility | +0.0912 | +0.68 | 0.73 | 242 |
| oos1_2023 | liquidity | +0.1405 | +0.85 | 0.81 | 242 |
| oos1_2023 | market_beta | +0.0020 | +0.01 | 0.45 | 242 |
| oos1_2023 | momentum | -0.0259 | -0.18 | 0.45 | 242 |
| oos1_2023 | score | +0.1137 | +0.71 | 0.78 | 242 |
| oos1_2023 | size | +0.1042 | +0.54 | 0.67 | 242 |
| oos1_2023 | value | +0.0065 | +0.05 | 0.46 | 242 |
| oos1_2023 | volatility | +0.1095 | +0.72 | 0.75 | 242 |
| crisis_2024 | liquidity | +0.0171 | +0.07 | 0.62 | 242 |
| crisis_2024 | market_beta | +0.0195 | +0.08 | 0.55 | 242 |
| crisis_2024 | momentum | -0.0953 | -0.54 | 0.25 | 242 |
| crisis_2024 | score | +0.0147 | +0.07 | 0.64 | 242 |
| crisis_2024 | size | +0.0004 | +0.00 | 0.62 | 242 |
| crisis_2024 | value | -0.0417 | -0.31 | 0.45 | 242 |
| crisis_2024 | volatility | +0.0802 | +0.34 | 0.64 | 242 |
| blind_2025_2026 | liquidity | +0.0350 | +0.24 | 0.56 | 362 |
| blind_2025_2026 | market_beta | +0.0085 | +0.04 | 0.54 | 362 |
| blind_2025_2026 | momentum | -0.0705 | -0.49 | 0.33 | 362 |
| blind_2025_2026 | score | +0.0262 | +0.16 | 0.51 | 362 |
| blind_2025_2026 | size | +0.0140 | +0.09 | 0.44 | 362 |
| blind_2025_2026 | value | -0.0381 | -0.25 | 0.42 | 362 |
| blind_2025_2026 | volatility | +0.0706 | +0.34 | 0.66 | 362 |

## 2. Strategy sign vs realized IC (hold=20, full period)

| Factor | Strategy sign | Mean IC | Sign agrees |
|---|---|---|---|
| value | +1 | -0.0303 | **NO** |
| size | +1 | +0.0347 | YES |
| liquidity | +1 | +0.0653 | YES |
| momentum | -1 | -0.0670 | YES |

> **direction warning**: realized IC -0.0303 contradicts strategy sign +1

## 3. Composite IC decay by horizon (full period)

| Horizon | Mean IC | ICIR | Pos ratio |
|---|---|---|---|
| 5d | +0.0274 | +0.17 | 0.58 |
| 10d | +0.0364 | +0.22 | 0.62 |
| 20d | +0.0476 | +0.28 | 0.65 |
| 40d | +0.0625 | +0.35 | 0.66 |

## 4. Single-factor strict-ledger backtests (score = factor * sign)

| Window | Factor | Annual | MDD | Trades |
|---|---|---|---|---|
| pre_history_2020_2021 | value_only | +0.9% (-12.6% vs base) | -22.5% | 16 |
| validation_2022 | value_only | -7.8% (+35.6% vs base) | -24.6% | 12 |
| oos1_2023 | value_only | -5.8% (-37.7% vs base) | -15.7% | 10 |
| crisis_2024 | value_only | -10.0% (-48.7% vs base) | -34.6% | 10 |
| blind_2025_2026 | value_only | -15.2% (-30.6% vs base) | -34.1% | 10 |
| pre_history_2020_2021 | size_only | +8.8% (-4.7% vs base) | -35.8% | 36 |
| validation_2022 | size_only | -45.1% (-1.6% vs base) | -44.4% | 10 |
| oos1_2023 | size_only | +158.4% (+126.5% vs base) | -43.8% | 10 |
| crisis_2024 | size_only | +9.7% (-29.0% vs base) | -40.4% | 10 |
| blind_2025_2026 | size_only | -6.7% (-22.2% vs base) | -57.6% | 12 |
| pre_history_2020_2021 | liquidity_only | +4.3% (-9.2% vs base) | -27.3% | 51 |
| validation_2022 | liquidity_only | -50.9% (-7.4% vs base) | -49.4% | 12 |
| oos1_2023 | liquidity_only | +125.8% (+93.8% vs base) | -40.5% | 14 |
| crisis_2024 | liquidity_only | +37.2% (-1.5% vs base) | -42.2% | 33 |
| blind_2025_2026 | liquidity_only | +42.3% (+26.9% vs base) | -46.5% | 37 |
| pre_history_2020_2021 | momentum_only | +41.6% (+28.1% vs base) | -27.2% | 209 |
| validation_2022 | momentum_only | -20.4% (+23.1% vs base) | -25.3% | 130 |
| oos1_2023 | momentum_only | -11.7% (-43.7% vs base) | -34.7% | 118 |
| crisis_2024 | momentum_only | -10.9% (-49.6% vs base) | -41.6% | 77 |
| blind_2025_2026 | momentum_only | -4.3% (-19.7% vs base) | -42.5% | 124 |

## 5. Weighted factor contribution (attribution)

Contribution_i = w_i * single-factor annual return. Sum vs composite (full-window cross-check).

| Window | Composite | value(w=0.30) | size(w=0.25) | liquidity(w=0.25) | momentum(w=0.20) | Σ contrib |
|---|---|---|---|---|---|---|
| pre_history_2020_2021 | +13.6% | +0.3% | +2.2% | +1.1% | +8.3% | +11.9% |
| validation_2022 | -43.5% | -2.4% | -11.3% | -12.7% | -4.1% | -30.4% |
| oos1_2023 | +31.9% | -1.7% | +39.6% | +31.4% | -2.3% | +67.0% |
| crisis_2024 | +38.7% | -3.0% | +2.4% | +9.3% | -2.2% | +6.5% |
| blind_2025_2026 | +15.4% | -4.6% | -1.7% | +10.6% | -0.9% | +3.5% |

> Note: single-factor portfolios are NOT orthogonal (size/illiquidity/value
> overlap cross-sectionally), so Σ contribution ≠ composite return exactly;
> the table shows where the alpha lives, not a precise decomposition.

## Verdict

- Direction check: **FAIL** — at least one strategy factor's realized IC
  contradicts its sign (value).
- Composite IC at hold=20: see table; direction consistent with the score's
  realized alpha in the OOS windows.
- Attribution: see section 5 — the composite's alpha is carried mainly by
  the factors with positive IC and positive-weight exposure.

## Honest caveats

- Data tier is DIAGNOSTIC (E0): directional evidence only; formal
  E3 requires a binlog-enabled server.
- IC uses raw forward returns (no cost) — cost sensitivity is covered
  by the Phase 3.3 cost2x experiment (<=1.2pp annual degradation).
- Exit is at close (entry+hold_days), matching add_forward_returns()
  used by the engine itself; the engine's live exit is at open.
- Overlapping 20d horizons make daily ICs autocorrelated; ICIR is
  unadjusted (reported for comparison, not as a significance test).
- This is a pre-registered diagnostic, NOT re-optimization: weights,
  signs, and execution parameters are untouched.
- Single-factor backtests use the SAME frozen scores parquet with only
  the score column replaced; each run is independently strict-ledger
  VERIFIED with input hashes in its manifest.
