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
- Event window pass: at least 5 recovery event days and positive recovery-event theory gap.
- Event accumulator pass: `reports/production_monitor/research_shadow_event_log.csv` has durable recovery-event samples, deduplicated by `trade_date + shadow_strategy + production_strategy`.
- Execution proxy pass: execution feasibility is not `unknown_missing_execution_proxy`, and there are no degraded execution days.
- No large slippage proxy days, limit-up buy risk days, unfilled proxy days, or limit-down sell risk days.
- Pattern is not required for enabled shadow, but current pattern quality status must be disclosed.
- Production default unchanged and manual approval recorded.

## Required Read-Only Reports

- `reports/production_monitor/research_shadow_candidate_daily.json/md`: latest calendar-window shadow comparison.
- `reports/production_monitor/research_shadow_candidate_recovery_events.json/md`: recovery-event window sample.
- `reports/production_monitor/research_shadow_event_log.csv`: cumulative recovery-event ledger.
- `reports/production_monitor/research_shadow_event_summary.json`: cumulative event metrics.
- `reports/production_monitor/research_shadow_promotion_status.json/md`: promotion readiness dashboard.

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
