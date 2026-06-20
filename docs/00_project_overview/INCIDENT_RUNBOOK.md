# Incident Runbook

## Incident classification

| Code | Name | Trigger | Freeze action | Recovery |
|------|------|---------|---------------|----------|
| INC-001 | ORDER_SCHEMA_FAILURE | v2 migration validation fails | Pipeline abort | Fix schema, re-run migration, validate |
| INC-002 | ORDER_BACKFILL_INCOMPLETE | NULL identity fields after backfill | Pipeline abort | Investigate, backfill, verify zero NULL |
| INC-003 | T1_CALENDAR_FAILURE | No next trading day in dim_trade_cal | Block order writing | Populate trade calendar, verify |
| INC-004 | FINAL_CANDIDATE_UNTRADABLE | Top5 contains ST/suspended/no-close | Block order generation | Review candidates, check data freshness |
| INC-005 | HEALTH_STALE_FREEZE | Health record >1 trading day old | Freeze new buys | Run health monitor, verify data pipeline |
| INC-006 | EXECUTION_DEGRADATION | 3+ consecutive RED execution days | Freeze new buys | Investigate slippage, blocked orders |
| INC-007 | DATA_READINESS_BLOCKED | PreScoreGate or PostScoreGate critical fail | Pipeline abort | Check data sources, re-run after data available |
| INC-008 | FEISHU_DELIVERY_FAILURE | TLS error or HTTP failure on webhook | Log + retry with backoff | Verify webhook URL, check certificate |
| INC-009 | ORDER_CLEANUP_FAILURE | RED cleanup of stale BUY drafts failed | Pipeline abort (fail-closed) | Manual review of stale orders, re-run |
| INC-010 | CANARY_DRAWDOWN_BREACH | Canary drawdown exceeds OOS band | Freeze canary | Post-mortem, manual approval to resume |

## Response procedure

1. **Identify** — which incident code, which release, which date
2. **Contain** — freeze affected release/lane immediately
3. **Diagnose** — check data freshness, execution quality, schema state
4. **Fix** — address root cause (data, code, config)
5. **Verify** — re-run with validation, confirm zero residual errors
6. **Document** — record in strategy_incidents table with timeline
7. **Resume** — manual approval required before unfreezing

## Escalation

| Severity | Response time | Escalation |
|----------|--------------|------------|
| INC-001,002,003,004,005 | Immediate (pipeline abort) | Operator review same day |
| INC-006,007 | Same trading day | Strategy owner review |
| INC-008 | Next trading day | Infrastructure review |
| INC-009,010 | Immediate freeze | Strategy owner + operator |
