# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Chenyiyun2087 is a Chinese A-share quantitative research and execution system covering data collection, scoring/ranking, strategy backtesting, live trading tracking, and a Flask web dashboard. The project runs on Python 3 with a MySQL database (`chenyiyun`).

## Common Commands

```bash
# Web console (primary production scheduler + dashboard)
bash start_web_console.sh           # starts on port 5001 by default
# Or directly:
python -m flask --app web.app run --host 0.0.0.0 --port 5001 --no-debugger --no-reload

# Standalone scheduler (historical, not the primary scheduler)
bash start_scheduler.sh

# Daily full-market scoring (M1)
python -m scoreRank.cli.run_daily

# M8 strategy regression cycle
python -m scoreRank.cli.run_m8_cycle --lookback-dates 60

# Build B-event KPI tables (full rebuild)
python -m scoreRank.cli.build_b_event_kpi --all

# Live tracker sync
python sina/live_tracker/run_live_tracker.py sync

# Sina B/S detection (capture screenshots + analyze buy/sell signals)
python sina/bs_detection/main.py config_1

# Eastmoney sentiment scan
python eastmoney/main.py config_1

# B-signal enhancement cycle (export → research → train → import)
python scripts/run_bs_signal_enhancement_cycle.py --target hit_20_10pct --model-kind all

# Monthly auto-cycle for B-signal model
python scripts/ops/run_monthly_bs_signal_enhancement_cycle.py

# Run all tests
pytest test/

# Run specific test directories
pytest test/ScoreRank/                        # unittest-style (M1-M8 chain tests)
pytest test/Sina/                              # unittest-style (B/S logic, live tracker)
pytest test/chenyiyunSelected/                 # pytest-style (daily runner, local adapter)
pytest backtest/tests/                         # backtest engine tests
pytest test/test_strict_execution_ledger.py    # specific pytest-style test

# Install dependencies (use the lock file for reproducible environments)
pip install -r requirements.lock.txt
```

## Architecture: Two Independent Strategy Systems

This project contains **two separate strategy systems** that share infrastructure (MySQL, trade calendar, web console) but have independent signal generation, rebalancing rules, and evaluation:

1. **`sina` strategy system** — directories: `sina/`, `scoreRank/`, `web/strategy_playbook.py`. Core capabilities: B/S detection from Sina Finance screenshots, M2–M8 evaluation chain, M7 rebalancing, live tracking. This is the primary, sophisticated system with multi-score evaluation, Claude AI integration, and ML model training.

2. **`chenyiyun` strategy system** — directories: `chenyiyunSelected/`, `scripts/ops/run_chenyiyun_*.py`. Core capabilities: migrated JoinQuant strategy, a simpler factor-based pipeline (dividend yield → turnover volatility → leverage → small market cap), equal-weight weekly rebalancing, limit-up monitoring, position updates. No AI or ML component.

## Core Data Pipeline (sina strategy)

```
sina_picture (screenshots) → sina_analyse (OCR B/S detection) → bs_detection_results
    → scoreRank.run_daily (Technical + Claude + opt_score) → score_rank_daily
    → build_b_event_kpi (event facts + future returns) → b_event_fact / b_event_kpi
    → run_m8_cycle (M2/M3 regression + parameter search) → strategy_m8_runs/items
    → M4 allocation → M7 rebalance orders → live_tracker sync
```

## Scoring System (`score_rank_daily`)

Three parallel scores, not a single metric:
- **`score`** (0–100): Technical total from 10 weighted sub-scores (trend, breakout, volume, RS, contraction, bias, chip, liquidity, bull align, vol mild) minus risk penalties
- **`opt_score`** (0–10): Factor optimization score from 7 category factors (momentum, value, quality, technical, capital, chip, size)
- **`claude_score`** (0–100): AI six-dimension score (momentum, value, quality, technical, capital, chip)

Pool types: `TRADE` (is_bs_candidate=1 AND score≥75), `WATCH` (is_bs_candidate=1 AND 60≤score<75).

Scoring thresholds, factor weights, and risk penalties are configured in `scoreRank/core/config.py`.

## M2–M8 Strategy Chain

| Stage | Purpose | Entry Point |
|-------|---------|-------------|
| M2 | Fixed strategy preset regression | `evaluate_m2_presets` |
| M3 | Parameter grid search per strategy family | `evaluate_m3_optimizer` |
| M4 | Multi-strategy voting fusion → portfolio allocation | `evaluate_m4_allocation` |
| M5 | Rolling window validation | `evaluate_m5_rolling` |
| M6 | NAV backtest with costs/slippage | `evaluate_m6_nav` |
| M7 | Rebalance order generation (forced exits + rebalancing) | `evaluate_m7_rebalance` |
| M8 | Scheduled cycle: runs M2+M3, persists results | `run_m8_cycle.py` |

The computational core of M2–M8 lives in `web/strategy_playbook.py` (~73k lines). It contains all `evaluate_m*` functions, the M7 rule engine (forced exit reasons, trailing stops, time stops, score exits), and helper functions for pyramid/weighted/quadrant filtering.

**Important**: The M6 NAV backtest in `strategy_playbook.py` is independent of the `backtest/` engine. M6 evaluates against `b_event_fact`/`b_event_kpi` tables (event-level), while the `backtest/` engine is bar-data-driven and used by the chenyiyun strategy system.

## chenyiyun Strategy: Stock Selection Pipeline

The local strategy (`chenyiyunSelected/strategy/local_strategy_adapter.py`) implements a multi-filter pipeline:

1. Universe: `dwd_stock_label_daily` joined with `dim_stock` (exclude ST, STAR Market 科创板, BSE 北交所)
2. Exclude stocks listed < 375 days
3. Filter top 50% by dividend yield (`dividend_ratio`)
4. Keep top 80% by turnover volatility (`turnover_vol_20`)
5. Keep top 50% by lowest leverage (`mlev = (total_mv + total_liabilities) / total_mv`)
6. Sort by smallest circulating market cap, take top 15
7. Equal-weight the top 10 as BUY signals

Two strategy versions exist: `chenyiyun1.py` (original) and `chenyiyun1_v2.py` (adds drawdown-based position sizing at 12%/18%/22% thresholds, style exposure filtering via CSI 1000/300 ratio, cooldown periods, liquidity filter). The local adapter implements the v1 pipeline.

## Backtest Engine (`backtest/`)

A lightweight, event-driven backtesting framework (separate package, `backtest/pyproject.toml`, Python ≥3.10):

```
backtest/src/backtest_engine/
  core/       engine.py, broker.py, portfolio.py, strategy.py, clock.py, types.py
  datafeed/   tushare_feed.py (MySQL-backed), mock_feed.py (synthetic data)
  metrics/    performance.py (returns, Sharpe, max drawdown, turnover)
  strategies/ high_dividend_local.py (production consumer)
  reporting/  JSON export
```

The production entry point is `chenyiyunSelected/strategy/run_local_backtest.py`, which feeds the local strategy's rebalance plan into `HighDividendLocalStrategy` with `TushareDailyFeed` data.

Single-frequency (daily bars only). Configurable costs via `BacktestConfig` (commission rate, slippage in bps).

Tests live under `backtest/tests/` and use pytest with `pythonpath = ["src"]` (configured in `pyproject.toml`).

## Scheduling

The **production scheduler** is `web/app.py`'s built-in task system (not `scheduler.py`, which is historical). Tasks are defined in the `TASKS` dict inside `web/app.py`.

The scheduler is a **thread-based polling loop** (not apscheduler/clock-driven): every 20 seconds it queues whitelisted due tasks in MySQL. A durable worker claims queued jobs, launches their subprocesses, and maintains task-lock heartbeats. Jobs use a `task_name + business_date` active-key for cross-process de-duplication, retry once after failure, and wait for configured upstream jobs before execution.

Key task management tables: `app_task_queue`, `app_task_lock`, `app_task_history`, `app_task_status`, `app_notification_channel`.

Only tasks in `SCHEDULED_TASK_WHITELIST` are eligible for auto-scheduling; others must be triggered manually. Notification channels support 飞书, 企业微信, 钉钉, and custom webhook.

The admin panel at `/admin` provides task status, scheduling controls, persistent queue inspection/retry/cancel, lock inspection, and execution history. Environment variable `DISABLE_APP_SCHEDULER_LOOP=1` disables both scheduler and queue worker (for development).

### Intraday Pipeline

- **Morning** (08:00–09:30): Trade calendar sync, signal strength check, weekly rebalance (Mondays)
- **Afternoon** (14:00–16:30): Limit-up check, Sina B/S capture + analysis, Eastmoney sentiment scan
- **Night** (21:00+): Full A-share scoring pipeline → industry backfill → B-signal consensus → trusted strategy candidates → shadow monitor → performance review → candle diag scan → M1 event/KPI build → M8 cycle → live tracker sync

All scheduled tasks check `dim_trade_cal` (exchange='SSE') before executing; non-trading days are skipped with a success record.

## Database

- MySQL via SQLAlchemy + PyMySQL: `mysql+pymysql://root:<password>@localhost:3306/chenyiyun`
- Configured via environment variables (see `scoreRank/core/db_config.py`):

| Variable | Default |
|----------|---------|
| `CHENYIYUN_DB_URL` | (explicit URL, overrides others) |
| `CHENYIYUN_DB_USER` | `root` |
| `CHENYIYUN_DB_PASSWORD` | `""` |
| `CHENYIYUN_DB_HOST` | `localhost` |
| `CHENYIYUN_DB_PORT` | `3306` |
| `CHENYIYUN_DB_NAME` | `chenyiyun` |
| `CHENYIYUN_DB_CHARSET` | `utf8mb4` |
| `CHENYIYUN_DB_UNIX_SOCKET` | `""` (optional) |

- Key tables: `score_rank_daily`, `bs_detection_results`, `b_event_fact`, `b_event_kpi`, `strategy_m8_runs`, `strategy_m8_items`, `m7_sell_signals`, `live_positions`, `live_trades`, `live_daily_snapshots`, `dim_trade_cal`
- Tushare data in separate schema `tushare_stock` (primarily `dwd_stock_daily_standard`, `dwd_daily`, `dim_stock`)
- chenyiyun-specific tables: `ads_local_strategy_orders`, `ads_local_strategy_signals`, `ads_chenyiyun_selected_signals`, `ads_chenyiyun_limitup_checks`

## Networking

All scripts must call `enforce_direct_network()` (from `project_network.py`) before making network requests. This strips proxy env vars and configures urllib for direct connections. Subprocess scripts should use `build_direct_network_env()` to construct a clean environment dict. For Selenium/Chrome, use `configure_chrome_direct_options()`.

## Tests

Tests use a **mix of pytest and unittest**:

- **Root-level tests** (`test/test_*.py`): pytest-style, plain functions, direct imports from `scripts.research.*` or `scripts.ops.*`
- **`test/ScoreRank/`**: unittest-style (`unittest.TestCase`), tests for M1–M8 chain, scorer, B/S pipeline, consensus builder. Named `test_m*_functional_no_db.py` — designed to run without a live database
- **`test/Sina/`**: unittest-style, B/S logic and live tracker tests
- **`test/chenyiyunSelected/`**: pytest-style, daily signal runner and local strategy adapter tests
- **`test/Eastmoney/`**: debug/verification scripts, not formal tests
- **`backtest/tests/`**: pytest-style, engine smoke test and metrics unit test

There is **no project-level pytest configuration** (no `conftest.py`, `pytest.ini`, or `setup.cfg` at the root). Some subdirectory tests use `sys.path.append()` to resolve imports; the `backtest/` package uses its own `pyproject.toml` with `pythonpath = ["src"]`.

## CI (GitHub Actions)

Workflow `.github/workflows/strict-ledger-audit.yml` runs on push/PR:
- **fixtures**: Runs 6 strict-execution/snapshot/reliability tests with `pytest -q`, plus a forbidden-import check on `replay_strict_execution_ledger_v2.py`
- **secret-scan**: gitleaks scan of commit history

## Project Organization (from AGENTS.md)

- Production/ops scripts: `scripts/ops/`
- Research scripts: `scripts/research/`
- Maintenance/migration scripts: `scripts/maintenance/`
- Export scripts: `scripts/export/`
- Documentation: `docs/` (by topic category)
- Raw data: `data/raw/`, processed: `data/processed/`
- Generated outputs: `exports/`
- **Do NOT move** existing production directories (`sina/`, `scoreRank/`, `chenyiyunSelected/`, `backtest/`, `web/`, `eastmoney/`) without a dedicated migration plan
- Strategy backtests must use T-day signals with T+1 execution; never leak future data through backfilled fields
