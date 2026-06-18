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
- Calendar gate pass: latest 20 common trading days have no severe unexplained degraded execution proxy.
- Event window gate pass: latest 120-day recovery window has at least 5 recovery event days, positive recovery-event theory gap, and no event-level degraded execution that would block promotion.
- Cumulative event gate pass: `total_recovery_events >= 30`, cumulative recovery theory gap is positive, positive event rate is at least 55%, and event degraded ratio is at most 5%.
- Incremental execution gate pass: degraded execution events attributable to the shadow incremental exposure are 0.
- Event accumulator pass: `reports/production_monitor/research_shadow_event_log.csv` has durable recovery-event samples, deduplicated by `trade_date + shadow_strategy + production_strategy`.
- Execution proxy pass: execution feasibility is not `unknown_missing_execution_proxy`, and degraded execution days are explained as either shared production/shadow risk or explicitly blocked as shadow incremental risk.
- No large slippage proxy days, limit-up buy risk days, unfilled proxy days, or limit-down sell risk days.
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
- `exports/signal_research/research_shadow_event_quality/*/summary.json`: cumulative recovery-event quality and low-risk positive-contribution subset analysis.

## Blocking vs Warning Status

- Enabled-shadow blockers: `NOT_READY_CALENDAR_WINDOW`, `NOT_READY_NO_EVENTS`, `NOT_READY_EXECUTION_PROXY`, `NOT_READY_CUMULATIVE_EVENT_QUALITY`, `NOT_READY_INCREMENTAL_EXECUTION`.
- Warning-only disclosures: `PATTERN_LINEAGE_WARNING`, `FP_SEPARABILITY_EXPLANATION_ONLY`.
- Pattern warnings block only pattern-based veto, risk guard, rerank, or pattern-based canary review; they do not block enabled shadow by themselves.

## Shared vs Incremental Execution Risk

- Shared execution risk: production and shadow hold the same Top5 path, have no meaningful position/risk-decision difference, and both would face the same execution proxy degradation.
- Shadow incremental execution risk: the degraded day overlaps a recovery event, risk-decision difference, position difference, or shadow-only symbol exposure.
- Promotion requires the incremental execution risk count to be 0, even when cumulative event return is positive.

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
