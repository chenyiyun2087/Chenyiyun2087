# Tail Risk, Exposure and Capacity Audit

> All numerical observations below are historical saved-ledger diagnostics. Fresh strict reproducibility is NOT_VERIFIABLE, so they cannot support promotion.

## production_governed_vol_position

- No compatible saved account ledger: NOT_VERIFIABLE.
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## baseline_full_liquidity_detail_vol_position

- Saved range: 2023-01-05 to 2026-06-02; max drawdown -0.6641; drawdown duration 757 trading days.
- Maximum single-stock weight: 0.7453471989041306; maximum industry weight: 0.9031577344493339.
- Worst completed trades (up to 10):

|   symbol | entry_date   | exit_date   |   net_pnl |   return_on_entry_notional |
|---------:|:-------------|:------------|----------:|---------------------------:|
|   300533 | 2023-05-25   | 2023-06-08  |  -37603.8 |                  -0.64968  |
|   601012 | 2023-04-21   | 2023-05-10  |  -37232.7 |                  -0.125477 |
|     2654 | 2023-07-21   | 2023-08-04  |  -31449.9 |                  -0.146831 |
|      681 | 2023-06-21   | 2023-07-07  |  -29622.5 |                  -0.137783 |
|   600703 | 2023-04-07   | 2023-04-21  |  -23241.2 |                  -0.109585 |
|     2553 | 2025-03-26   | 2025-04-10  |  -22276.8 |                  -0.323684 |
|      960 | 2026-03-05   | 2026-03-19  |  -21834.5 |                  -0.219244 |
|     2222 | 2023-04-21   | 2023-05-10  |  -21738.6 |                  -0.26159  |
|   300762 | 2024-08-02   | 2024-08-16  |  -20629.2 |                  -0.157427 |
|     2352 | 2024-10-08   | 2024-10-22  |  -20377.1 |                  -0.145176 |

- Largest single completed-lot loss: -37603.82; maximum observed consecutive losing completed lots: NOT_VERIFIABLE (same-day lot ordering is not a broker execution sequence).
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## adaptive_market_style

- Saved range: 2023-01-05 to 2026-06-04; max drawdown -0.3733; drawdown duration 621 trading days.
- Maximum single-stock weight: 0.457158957203121; maximum industry weight: 0.6160734043668332.
- Worst completed trades (up to 10):

|   symbol | entry_date   | exit_date   |   net_pnl |   return_on_entry_notional |
|---------:|:-------------|:------------|----------:|---------------------------:|
|   300975 | 2024-03-29   | 2024-04-16  |  -36770.7 |                  -0.381112 |
|   300620 | 2025-09-23   | 2025-10-15  |  -30526.8 |                  -0.276511 |
|   300827 | 2025-11-17   | 2025-12-01  |  -26011.1 |                  -0.173845 |
|   603888 | 2023-04-14   | 2023-04-28  |  -24478.8 |                  -0.270798 |
|   300251 | 2025-02-14   | 2025-02-28  |  -22858.4 |                  -0.257539 |
|   601179 | 2026-03-12   | 2026-03-26  |  -21183   |                  -0.11737  |
|     2085 | 2024-08-05   | 2024-08-19  |  -19772.2 |                  -0.188814 |
|   300007 | 2025-02-28   | 2025-03-14  |  -19634.5 |                  -0.113361 |
|   600745 | 2023-04-14   | 2023-04-28  |  -18672.1 |                  -0.206953 |
|   688787 | 2023-03-30   | 2023-04-14  |  -18442.5 |                  -0.264865 |

- Largest single completed-lot loss: -36770.73; maximum observed consecutive losing completed lots: NOT_VERIFIABLE (same-day lot ordering is not a broker execution sequence).
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## dual_system_adaptive_route

- Saved range: 2026-03-04 to 2026-06-02; max drawdown -0.1206; drawdown duration 52 trading days.
- Maximum single-stock weight: 0.2988679131081612; maximum industry weight: 0.4860072172492303.
- Worst completed trades (up to 10):

|   symbol | entry_date   | exit_date   |   net_pnl |   return_on_entry_notional |
|---------:|:-------------|:------------|----------:|---------------------------:|
|   600549 | 2026-03-04   | 2026-03-18  | -11931.8  |                 -0.154737  |
|      960 | 2026-03-04   | 2026-03-18  | -11322    |                 -0.143077  |
|   601872 | 2026-03-04   | 2026-03-18  | -10274.8  |                 -0.130127  |
|     2463 | 2026-03-18   | 2026-04-01  |  -6744.42 |                 -0.109475  |
|     2709 | 2026-05-06   | 2026-05-20  |  -6365.25 |                 -0.0955199 |
|   300394 | 2026-04-17   | 2026-05-06  |  -4954.61 |                 -0.136909  |
|   601012 | 2026-03-18   | 2026-04-01  |  -3629.02 |                 -0.0568955 |
|   601669 | 2026-03-18   | 2026-04-01  |  -2556.74 |                 -0.0393006 |
|   300274 | 2026-04-01   | 2026-04-20  |  -2512.57 |                 -0.0594409 |
|     2384 | 2026-03-18   | 2026-04-01  |  -2297.29 |                 -0.0410486 |

- Largest single completed-lot loss: -11931.80; maximum observed consecutive losing completed lots: NOT_VERIFIABLE (same-day lot ordering is not a broker execution sequence).
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## ashare_auto_shadow

- Saved range: 2026-03-04 to 2026-06-02; max drawdown -0.1728; drawdown duration 22 trading days.
- Maximum single-stock weight: 0.9984537173772708; maximum industry weight: 0.9984537173772708.
- Worst completed trades (up to 10):

|   symbol | entry_date   | exit_date   |    net_pnl |   return_on_entry_notional |
|---------:|:-------------|:------------|-----------:|---------------------------:|
|     3018 | 2026-05-19   | 2026-06-02  | -68709.6   |                -0.114459   |
|   300594 | 2026-03-10   | 2026-03-30  | -15865.8   |                -0.0318526  |
|   603966 | 2026-04-28   | 2026-05-19  | -11185.5   |                -0.0575261  |
|   301081 | 2026-03-30   | 2026-04-14  |  -4585.6   |                -0.009494   |
|   300457 | 2026-04-28   | 2026-05-19  |  -1668.56  |                -0.0086426  |
|   603558 | 2026-04-17   | 2026-05-06  |   -214.308 |                -0.0757212  |
|   300205 | 2026-04-22   | 2026-05-11  |    -12.912 |                -0.0210293  |
|   603990 | 2026-05-06   | 2026-05-20  |     38.286 |                 0.00593397 |
|   301667 | 2026-04-14   | 2026-04-28  |  26992.5   |                 0.11407    |
|     1223 | 2026-04-28   | 2026-05-19  |  33990.1   |                 0.175696   |

- Largest single completed-lot loss: -68709.55; maximum observed consecutive losing completed lots: NOT_VERIFIABLE (same-day lot ordering is not a broker execution sequence).
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## tiered_liquidity_then_bs_v2

- Saved range: 2023-01-04 to 2026-06-02; max drawdown -0.9420; drawdown duration 762 trading days.
- Maximum single-stock weight: 0.6940637509991499; maximum industry weight: 0.8089992842542602.
- Worst completed trades (up to 10):

|   symbol | entry_date   | exit_date   |   net_pnl |   return_on_entry_notional |
|---------:|:-------------|:------------|----------:|---------------------------:|
|   300494 | 2023-06-20   | 2023-07-06  |  -59783.8 |                  -0.762436 |
|   301236 | 2023-06-06   | 2023-06-20  |  -45545.4 |                  -0.532868 |
|   600188 | 2023-07-06   | 2023-07-20  |  -42809.2 |                  -0.662942 |
|   603236 | 2023-06-06   | 2023-06-20  |  -41234.6 |                  -0.469028 |
|   600248 | 2023-05-09   | 2023-05-23  |  -30088.8 |                  -0.272152 |
|   603533 | 2023-05-09   | 2023-05-23  |  -25514.3 |                  -0.23164  |
|     2342 | 2026-02-09   | 2026-03-03  |  -21867.8 |                  -0.206222 |
|   600339 | 2023-05-09   | 2023-05-23  |  -20688.9 |                  -0.186323 |
|     2820 | 2023-01-04   | 2023-01-18  |  -17970.8 |                  -0.180418 |
|      560 | 2024-05-22   | 2024-06-05  |  -16647.3 |                  -0.255499 |

- Largest single completed-lot loss: -59783.83; maximum observed consecutive losing completed lots: NOT_VERIFIABLE (same-day lot ordering is not a broker execution sequence).
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## ashare_hybrid_conservative_shadow

- Saved range: 2026-03-04 to 2026-06-02; max drawdown -0.1556; drawdown duration 6 trading days.
- Maximum single-stock weight: 0.9873612095136156; maximum industry weight: 0.9958453697840255.
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## ashare_trend_breakout_shadow

- Saved range: 2026-03-04 to 2026-06-02; max drawdown 0.0000; drawdown duration 0 trading days.
- Maximum single-stock weight: nan; maximum industry weight: nan.
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## chenyiyun_selected

- No compatible saved account ledger: NOT_VERIFIABLE.
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

## repair_reversal_shadow

- No compatible saved account ledger: NOT_VERIFIABLE.
- Limit-down exit delay, extreme-regime attribution, ADV participation, impact cost and executable days: NOT_VERIFIABLE from saved files.
- Capacity conclusion: NOT_VERIFIABLE; no authoritative ADV/impact snapshot was part of the frozen evidence.

