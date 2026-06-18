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

# Run tests
pytest test/
```

## Architecture: Two Independent Strategy Systems

This project contains **two separate strategy systems** that share infrastructure (MySQL, trade calendar, web console) but have independent signal generation, rebalancing rules, and evaluation:

1. **`sina` strategy system** — directories: `sina/`, `scoreRank/`, `web/strategy_playbook.py`. Core capabilities: B/S detection from Sina Finance screenshots, M2–M8 evaluation chain, M7 rebalancing, live tracking.

2. **`chenyiyun` strategy system** — directories: `chenyiyunSelected/`, `scripts/ops/run_chenyiyun_*.py`. Core capabilities: localized stock selection, daily/weekly rebalance signals, limit-up monitoring, position updates.

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

## Scheduling

The **production scheduler** is `web/app.py`'s built-in task system (not `scheduler.py`, which is historical). Tasks are defined in the `TASKS` dict inside `web/app.py`. The daily schedule follows a three-phase intraday pipeline:

- **Morning** (08:00–09:30): Trade calendar sync, signal strength check, weekly rebalance (Mondays)
- **Afternoon** (14:00–16:30): Limit-up check, Sina B/S capture + analysis, Eastmoney sentiment scan
- **Night** (21:00+): Full A-share scoring pipeline → industry backfill → B-signal consensus → trusted strategy candidates → shadow monitor → performance review → candle diag scan → M1 event/KPI build → M8 cycle → live tracker sync

All scheduled tasks check `dim_trade_cal` (exchange='SSE') before executing; non-trading days are skipped with a success record.

## Database

- MySQL via SQLAlchemy + PyMySQL: `mysql+pymysql://root:<password>@localhost:3306/chenyiyun`
- Password from env var `CHENYIYUN_DB_PASSWORD` (default: `19871019`)
- Key tables: `score_rank_daily`, `bs_detection_results`, `b_event_fact`, `b_event_kpi`, `strategy_m8_runs`, `strategy_m8_items`, `m7_sell_signals`, `live_positions`, `live_trades`, `live_daily_snapshots`, `dim_trade_cal`
- Tushare data in separate schema `tushare_stock` (primarily `dwd_stock_daily_standard`, `dwd_daily`, `dim_stock`)

## Networking

All scripts must call `enforce_direct_network()` (from `project_network.py`) before making network requests. This strips proxy env vars and configures urllib for direct connections. Subprocess scripts should use `build_direct_network_env()`.

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
