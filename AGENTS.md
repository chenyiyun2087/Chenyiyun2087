# Chenyiyun2087 Project Management Rules

This file defines the default project organization rules for future agents and maintainers.

## Default Directory Standard

Use `docs/00_project_overview/PROJECT_DIRECTORY.md` as the source of truth for directory placement.

Default placement:

- Production and daily operation scripts: `scripts/ops/`
- Research-only scripts: `scripts/research/`
- Maintenance, repair, migration, and backfill scripts: `scripts/maintenance/`
- Standalone export scripts: `scripts/export/`
- Strategy research notes: `docs/01_strategy_research/`
- Stock research notes: `docs/02_stock_research/`
- Backtest summaries and report indexes: `docs/03_backtest_reports/`
- Live trading, paper trading, and shadow trading records: `docs/04_live_trading/`
- External report reviews: `docs/05_external_reports/`
- Prompts and reusable analysis workflows: `docs/06_prompt_library/`
- Deprecated, temporary, or historical materials: `docs/99_archive/`
- Raw data: `data/raw/`
- Processed data: `data/processed/`
- External data packages: `data/external/`
- Small test samples: `data/samples/`
- Generated research outputs stay under `exports/`; human-readable indexes belong under `docs/`.

## Do Not Move Without a Specific Migration Task

Do not move existing production code directories because many scripts rely on current paths:

- `sina/`
- `scoreRank/`
- `chenyiyunSelected/`
- `backtest/`
- `web/`
- `eastmoney/`
- existing `scripts/ops/`
- existing `test/`

If a move is necessary, create a dedicated migration plan, update imports and runtime paths, and run relevant tests.

## Documentation Rules

- Every important strategy or backtest result should be summarized in `docs/03_backtest_reports/BACKTEST_INDEX.md`.
- Every durable stock research note should be discoverable from `docs/02_stock_research/STOCK_RESEARCH_INDEX.md`.
- Every durable strategy/methodology note should be discoverable from `docs/01_strategy_research/STRATEGY_RESEARCH_INDEX.md`.
- Development task status belongs in `docs/tasks/`; research conclusions belong in the topic-specific docs folders.
- Keep raw auto-generated outputs in their original `exports/` folders. Do not duplicate large CSV files into `docs/`; link and summarize them instead.

## Naming Rules

- Stock research: `YYYY-MM-DD_股票代码_主题.md`
- Strategy backtest summary: `YYYY-MM-DD_策略名_回测摘要.md`
- Industry research: `YYYY-MM-DD_行业_产业链梳理.md`
- Fund research: `YYYY-MM-DD_基金代码_分析.md`

## Strategy Research Safety

- Trusted backtests must use T-day signals and T+1 execution.
- Do not use `bs_model_*` historical backfilled fields in trusted backtests unless a walk-forward model process proves point-in-time availability.
- Dynamic factor weights and adaptive strategy selection must only use samples whose `exit_date < signal_date`.
- When adding a new strategy result, record the period, initial capital, transaction cost, slippage, max holding count, return, max drawdown, and future-function control.

