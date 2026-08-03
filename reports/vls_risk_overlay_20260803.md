# VLS Drawdown-Guard Risk Overlay Comparison

Overlay: vls_drawdown_guard_v1.yaml (pre-registered 2026-08-03, NOT fitted)
Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)
Execution: strict-ledger VERIFIED, T+1 open precommit, cost 7.5bp + 10bp slippage

| Split | Metric | Baseline | Overlay | Delta |
|---|---|---|---|---|
| pre_history_2020_2021 | total_return | +22.7% | +17.4% | -5.4% |
| pre_history_2020_2021 | annualized_return | +13.6% | +10.4% | -3.1% |
| pre_history_2020_2021 | max_drawdown | -27.4% | -25.6% | 1.9% |
| pre_history_2020_2021 | trade_count | 87 | 89 | 2 |
| pre_history_2020_2021 | turnover | 9.4x | 9.3x | -0.1x |
| pre_history_2020_2021 | total_cost | 3,581 | 3,487 | -95 |
| pre_history_2020_2021 | avg_gross_exposure | 78.3% | 78.0% | -0.3% |
| pre_history_2020_2021 | daily_win_rate | 45.0% | 45.5% | 0.5% |
| validation_2022 | total_return | -41.9% | -31.4% | +10.5% |
| validation_2022 | annualized_return | -43.5% | -32.7% | +10.8% |
| validation_2022 | max_drawdown | -42.8% | -38.3% | 4.4% |
| validation_2022 | trade_count | 40 | 38 | -2 |
| validation_2022 | turnover | 4.7x | 3.8x | -0.9x |
| validation_2022 | total_cost | 1,269 | 1,071 | -198 |
| validation_2022 | avg_gross_exposure | 97.5% | 87.7% | -9.8% |
| validation_2022 | daily_win_rate | 41.1% | 43.6% | 2.5% |
| oos1_2023 | total_return | +30.2% | +24.2% | -6.0% |
| oos1_2023 | annualized_return | +31.9% | +25.5% | -6.4% |
| oos1_2023 | max_drawdown | -27.3% | -27.3% | 0.0% |
| oos1_2023 | trade_count | 32 | 30 | -2 |
| oos1_2023 | turnover | 3.2x | 3.0x | -0.2x |
| oos1_2023 | total_cost | 1,170 | 1,087 | -83 |
| oos1_2023 | avg_gross_exposure | 98.0% | 96.4% | -1.5% |
| oos1_2023 | daily_win_rate | 43.2% | 43.2% | 0.0% |
| crisis_2024 | total_return | +36.5% | +20.0% | -16.6% |
| crisis_2024 | annualized_return | +38.7% | +21.1% | -17.6% |
| crisis_2024 | max_drawdown | -36.7% | -29.2% | 7.5% |
| crisis_2024 | trade_count | 50 | 50 | 0 |
| crisis_2024 | turnover | 5.6x | 4.8x | -0.8x |
| crisis_2024 | total_cost | 1,922 | 1,623 | -299 |
| crisis_2024 | avg_gross_exposure | 97.2% | 71.9% | -25.3% |
| crisis_2024 | daily_win_rate | 45.2% | 44.0% | -1.2% |
| blind_2025_2026 | total_return | +24.2% | +19.8% | -4.4% |
| blind_2025_2026 | annualized_return | +15.4% | +12.7% | -2.7% |
| blind_2025_2026 | max_drawdown | -33.0% | -31.7% | 1.3% |
| blind_2025_2026 | trade_count | 64 | 58 | -6 |
| blind_2025_2026 | turnover | 6.4x | 5.9x | -0.5x |
| blind_2025_2026 | total_cost | 3,495 | 2,965 | -529 |
| blind_2025_2026 | avg_gross_exposure | 93.0% | 85.0% | -8.0% |
| blind_2025_2026 | daily_win_rate | 52.8% | 51.4% | -1.3% |

## Pre-registered rejection criteria (config/risk_overlays/vls_drawdown_guard_v1.yaml)

- pre_history_2020_2021: PASS — annual_degradation<=40%=PASS(23%); overlay_mdd_better_than_-30%=PASS(-25.6%); turnover_increase<=50%=PASS(-1%)
- validation_2022: REJECT — annual_degradation<=40%=PASS(-25%); overlay_mdd_better_than_-30%=FAIL(-38.3%); turnover_increase<=50%=PASS(-19%)
- oos1_2023: PASS — annual_degradation<=40%=PASS(20%); overlay_mdd_better_than_-30%=PASS(-27.3%); turnover_increase<=50%=PASS(-7%)
- crisis_2024: REJECT — annual_degradation<=40%=FAIL(45%); overlay_mdd_better_than_-30%=PASS(-29.2%); turnover_increase<=50%=PASS(-14%)
- blind_2025_2026: REJECT — annual_degradation<=40%=PASS(18%); overlay_mdd_better_than_-30%=FAIL(-31.7%); turnover_increase<=50%=PASS(-9%)

**Overall: REJECTED in validation_2022, crisis_2024, blind_2025_2026**

## Honest caveats

- The overlay scales T+1 gross exposure at signal day; existing positions
  exit only via the strategy's forced-exit rules (no overlay-forced trims).
- Drawdown rules use the running (in-loop) portfolio equity path — this is
  feedback-aware, unlike a static baseline-trigger design.
- Market-state inputs are REAL (benchmark_index ret_20d, universe breadth);
  the run blocks if they are absent or constant.
- Data tier is DIAGNOSTIC (E0): directional evidence only; formal E3
  requires a binlog-enabled server.
