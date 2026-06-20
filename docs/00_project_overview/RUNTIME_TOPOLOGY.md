# Runtime Topology

## Single production scheduler

**Primary entry point:** `web/app.py` (Flask built-in task system)

The web console scheduler is the ONLY production scheduler. The legacy
`scheduler.py` is **disabled for production** — it remains available for
historical reference and development convenience but MUST NOT be used as
the production task runner.

## Task identity and idempotency

Every task execution is recorded with:

- task_name
- trigger_type (scheduled / manual)
- target_date
- idempotency_key = (task_name, target_date, trigger_type)

Re-running a task for the same (name, date, trigger) within the same day
MUST be a no-op — the task lock table prevents concurrent execution and
the history table records prior completions.

## Cross-process lock

Task execution uses MySQL row-level locks (`SELECT ... FOR UPDATE` on
`app_task_lock`). This prevents duplicate execution across:

- Multiple web server workers
- Manual admin triggers racing with scheduled triggers
- Stale heartbeats (3-hour timeout with heartbeat reset)

## Pipeline order (current production)

```
Step 0:  Order schema validation + backfill
Step 1:  PreScoreGate (market data readiness)
Step 2:  eastmoney strategy scan
Step 3:  scoreRank daily scoring
Step 4:  Industry backfill
Step 5:  B-signal consensus
Step 5b: PostScoreGate (score data validation)
Step 5c: Health gate (previous-day health → order permissions)
Step 6:  Candidate export (gated by PostScoreGate + health)
Step 7:  Shadow monitor (T+1 execution quality)
Step 7b: Health monitor update (for next trading day)
Step 8:  Performance review
Step 9:  M1 event/KPI build
Step 10: M8 strategy cycle
Step 11: Live tracker sync
```

## Data state grades

| State | Meaning | Permitted actions |
|-------|---------|-------------------|
| GREEN | All checks normal | Full candidate export + order drafts |
| YELLOW | Warnings present | Orders require manual confirmation |
| RED | Critical failure | No new BUY orders; sell + maintain only |
| BLOCKED | Data gate failed | Pipeline aborted before candidate export |
