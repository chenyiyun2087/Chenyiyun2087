# Research Shadow Promotion Checklist

This checklist gates any move from manual research shadow to enabled shadow or canary.

## Current Boundary

- Production default remains `production_governed_vol_position`.
- `research_shadow_candidate.enabled` remains `false`.
- `production_governed_vol_position_v1_2b_gate_tuned` is observation-only.
- False-positive classification is explanation-only.
- Pattern features remain monitor-only until data quality passes.

## Enabled Shadow Requirements

- Calendar window pass: latest 20 common trading days pass shadow monitor thresholds.
- Calendar gate pass: latest 20 common trading days have no hard execution block; large-slippage-only days are `EXECUTION_SLIPPAGE_WARNING`.
- Event window gate pass: latest 120-day recovery window has at least 5 recovery event days, positive recovery-event theory gap, and no event-level hard execution block.
- Cumulative event gate pass: `total_recovery_events >= 30`, cumulative recovery theory gap is positive, positive event rate is at least 55%, and event degraded ratio is at most 5%.
- Execution-safe event gate pass: execution-safe recovery events alone have at least 30 samples, positive cumulative theory gap, and positive event rate of at least 55%.
- Promotion-valid event gate pass: recovery events that exclude hard execution blocks have positive theory gap and enough samples for manual review.
- Incremental execution gate pass: hard-block execution events attributable to shadow incremental exposure are 0.
- Event accumulator pass: `reports/production_monitor/research_shadow_event_log.csv` has durable recovery-event samples, deduplicated by `trade_date + shadow_strategy + production_strategy`.
- Execution proxy pass: execution feasibility is not `unknown_missing_execution_proxy`; hard blocks must be absent from promotion candidates, while large-slippage-only days remain warning-only.
- Hard block thresholds: `abs(open_gap_proxy) > 5%`, `limit_up_buy_ratio > 20%`, `limit_down_sell_ratio > 20%`, or `estimated_turnover_impact > 3%`.
- Pattern is not required for enabled shadow; pattern lineage and quality must be disclosed as warning-only fields.
- Production default unchanged and manual approval recorded.

## Required Read-Only Reports

- `reports/production_monitor/research_shadow_candidate_daily.json/md`: latest calendar-window shadow comparison.
- `reports/production_monitor/research_shadow_candidate_recovery_events.json/md`: recovery-event window sample.
- `reports/production_monitor/research_shadow_event_log.csv`: cumulative recovery-event ledger.
- `reports/production_monitor/research_shadow_event_summary.json`: cumulative event metrics.
- `reports/production_monitor/research_shadow_promotion_status.json/md`: promotion readiness dashboard.
- `exports/signal_research/execution_proxy_quality/*/summary.json`: execution proxy coverage and degraded-ratio audit.
- `exports/signal_research/shadow_execution_degradation/*/summary.json`: shared versus incremental execution degradation attribution.
- `reports/production_monitor/shadow_execution_degradation_report.md`: daily and symbol-level degraded execution explanation.
- `exports/signal_research/research_shadow_event_quality/*/summary.json`: cumulative recovery-event quality and low-risk positive-contribution subset analysis.

## Blocking vs Warning Status

- Enabled-shadow blockers: `NOT_READY_CALENDAR_WINDOW`, `NOT_READY_EVENT_WINDOW`, `NOT_READY_EXECUTION_PROXY_MISSING`, `NOT_READY_EXECUTION_HARD_BLOCK`, `NOT_READY_CUMULATIVE_EVENT_QUALITY`, `NOT_READY_EXECUTION_SAFE_EVENT_GATE`, `NOT_READY_PROMOTION_VALID_EVENT_GATE`, `NOT_READY_INCREMENTAL_EXECUTION`.
- Warning-only disclosures: `EXECUTION_SLIPPAGE_WARNING`, `PATTERN_LINEAGE_WARNING`, `FP_SEPARABILITY_EXPLANATION_ONLY`.
- Pattern warnings block only pattern-based veto, risk guard, rerank, or pattern-based canary review; they do not block enabled shadow by themselves.

## Shared vs Incremental Execution Risk

- Shared execution risk: production and shadow hold the same Top5 path, have no meaningful position/risk-decision difference, and both would face the same execution proxy degradation.
- Shadow incremental execution risk: the degraded day overlaps a recovery event, risk-decision difference, position difference, or shadow-only symbol exposure.
- Large-slippage risk is reported as common, shadow incremental, event, and non-event; `large_slippage_proxy > 3%` alone is warning-only.
- Promotion requires incremental hard-block days to be 0, even when cumulative event return is positive.
- Execution-safe uplift simulation compares original v1.2b, hard-block fallback, open-gap downweight, and large-slippage downweight without changing strategy routing.
- `hard_block_fallback_event_gate` is a research-only counterfactual: hard-block recovery events use the production path, while execution-safe recovery events retain the v1.2b uplift. It may produce `READY_FOR_EXECUTION_SAFE_UPLIFT_RESEARCH`, but cannot clear the raw shadow event-window or incremental-execution blockers.
- Fallback research requires at least 5 original recovery events, zero remaining incremental hard-block days, positive fallback gap, at least 55% positive retained events, drawdown no worse than original shadow, and total return above production.

## Canary Requirements

- Enabled shadow has passed for 20 real trading days.
- Recovery event count is at least 5 during enabled shadow.
- No degraded execution quality.
- No 3 consecutive days of negative shadow theory gap.
- No abnormal position jumps.
- Manual approval recorded before any capital allocation.

## Explicit Non-Goals

- Do not switch production default from this checklist alone.
- Do not enable broker API execution.
- Do not promote `v1_2b_fp_classified`.
- Do not use pattern veto until Top5 coverage >= 90%, Top30 coverage >= 80%, and core missing < 20%.
- Do not let false-positive explanation labels, pattern coverage, or event ledgers alter sorting, sizing, buying, selling, scheduler, or production candidate export.
- Do not treat the fallback counterfactual as an enabled-shadow strategy until it has a separate account-level T+1 validation.
- The account-level research candidate is `production_governed_vol_position_v1_2b_execution_safe_uplift`. It may route to the v1 target at T+1 open when an incremental recovery buy hits a hard execution block; it remains research-only until its own 20/60/120/full-history gates pass.
