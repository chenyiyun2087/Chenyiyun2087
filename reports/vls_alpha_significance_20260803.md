# VLS Alpha Significance Study

Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)
Blind window: 2025-01-01 .. 2026-07-31 (362 trading days)
Pre-registered diagnostic 2026-08-03 (readiness gate alpha_proof_guard); frozen parameters untouched.

## 1. IC-level HAC significance (blind window, horizon-dependent lag)

| Factor | Horizon | Mean IC | HAC std | Inflation | HAC t | p(1-sided) |
|---|---|---|---|---|---|---|
| liquidity | 5d | +0.0147 | 0.2728 | 1.8x | +1.05 | 0.147 |
| liquidity | 10d | +0.0237 | 0.3734 | 2.6x | +1.22 | 0.111 |
| liquidity | 20d | +0.0350 | 0.5561 | 3.8x | +1.20 | 0.116 |
| liquidity | 40d | +0.0419 | 0.8693 | 5.5x | +0.89 | 0.187 |
| market_beta | 5d | +0.0039 | 0.3303 | 1.8x | +0.23 | 0.409 |
| market_beta | 10d | +0.0064 | 0.4784 | 2.6x | +0.26 | 0.398 |
| market_beta | 20d | +0.0085 | 0.6881 | 3.5x | +0.24 | 0.407 |
| market_beta | 40d | +0.0086 | 0.6026 | 3.5x | +0.27 | 0.395 |
| momentum | 5d | -0.0514 | 0.2299 | 1.7x | -4.34 | 1.000 |
| momentum | 10d | -0.0608 | 0.3161 | 2.3x | -3.71 | 1.000 |
| momentum | 20d | -0.0705 | 0.4025 | 2.8x | -3.33 | 1.000 |
| momentum | 40d | -0.0733 | 0.3793 | 3.2x | -3.57 | 1.000 |
| score | 5d | +0.0190 | 0.2879 | 1.8x | +1.28 | 0.100 |
| score | 10d | +0.0230 | 0.4038 | 2.6x | +1.10 | 0.137 |
| score | 20d | +0.0262 | 0.6035 | 3.7x | +0.83 | 0.205 |
| score | 40d | +0.0281 | 0.9068 | 5.3x | +0.57 | 0.283 |
| size | 5d | +0.0041 | 0.2980 | 1.8x | +0.26 | 0.396 |
| size | 10d | +0.0081 | 0.4029 | 2.5x | +0.39 | 0.349 |
| size | 20d | +0.0140 | 0.6106 | 3.7x | +0.44 | 0.331 |
| size | 40d | +0.0118 | 0.8863 | 5.3x | +0.25 | 0.403 |
| value | 5d | -0.0133 | 0.2728 | 1.8x | -0.94 | 0.827 |
| value | 10d | -0.0229 | 0.3911 | 2.6x | -1.13 | 0.870 |
| value | 20d | -0.0381 | 0.5227 | 3.5x | -1.39 | 0.917 |
| value | 40d | -0.0401 | 0.5277 | 4.0x | -1.41 | 0.920 |
| volatility | 5d | +0.0627 | 0.3525 | 1.8x | +3.45 | 0.000 |
| volatility | 10d | +0.0716 | 0.5263 | 2.7x | +2.62 | 0.005 |
| volatility | 20d | +0.0706 | 0.7143 | 3.5x | +1.88 | 0.030 |
| volatility | 40d | +0.0808 | 0.5721 | 3.4x | +2.61 | 0.005 |

## 2. Liquidity single-factor shuffle null (blind, full engine)

Seeded cross-sectional permutations: **100 runs** (seeds 20260803..20260902).

| Statistic | Annualized return | Max drawdown |
|---|---|---|
| Liquidity null mean | +7.6% | -29.2% |
| Liquidity null median | +6.4% | -30.0% |
| Liquidity null p10 | -7.0% | -35.1% |
| Liquidity null p90 | +24.5% | -20.9% |
| Best liquidity null | +44.8% | -39.2% |
| **Liquidity actual (single-factor)** | **+42.3%** | **-46.5%** |
| **p-value (actual ≥ null)** | **0.010** | — |

| Composite null mean (for comparison) | +5.8% | -28.4% |

## 3. Reconciliation: IC vs portfolio-return significance

- Composite alpha on blind: +15.4% annual, portfolio null p=0.190 — NOT distinguishable.
- Liquidity single-factor on blind: +42.3% annual, liquidity null p=0.010 — DISTINGUISHABLE from random.

- IC-level: composite HAC t at hold=20 (see table) — the cross-sectional signal
  is NOT significant on the blind window once overlapping-horizon autocorrelation
  is corrected (HAC std inflation 2.3x+ vs raw std).

## Verdict

- alpha_proof_guard remains **BLOCKED**: the composite strategy's alpha is NOT
  distinguishable from random at either the portfolio-return level (p=0.190) or
  the IC level (HAC t < 1.65).
- The factor diagnostics' +42.3% liquidity single-factor result does NOT overturn
  this: it is a diagnostic finding, and its own shuffle null p-value is reported
  above.  A single factor running the whole Top10 portfolio has extreme MDD
  (-46%) and is not a deployable configuration under the frozen strategy.
- Combined evidence: the score carries medium-term cross-sectional information
  (composite IC positive in 4/5 windows, rising with horizon) but the blind-window
  alpha is not statistically established.  No capital authorization is warranted.

## Honest caveats

- Data tier is DIAGNOSTIC (E0): directional evidence only; formal
  E3 requires a binlog-enabled server.
- HAC t-stats use horizon-dependent lag (Newey-West/Bartlett kernel);
  ICs on overlapping horizons are strongly autocorrelated.
- The liquidity null uses the same seeds as the composite null, so the
  two distributions are directly comparable.
- This is a pre-registered diagnostic, NOT re-optimization: frozen
  parameters are untouched and no weights were adjusted.
