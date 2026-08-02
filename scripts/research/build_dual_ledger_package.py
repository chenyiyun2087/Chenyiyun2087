#!/usr/bin/env python3
"""Build dual-ledger package from formal run backtest outputs.

The immutable runner produces account_backtest CSV outputs; the dual-ledger
acceptance engine requires a parquet package (orders, market snapshot,
corporate actions, lifecycle, calendar, cost/execution models).

This converter materializes that package from the frozen inputs + engine
outputs so run_dual_ledger_acceptance can independently re-simulate.

Usage:
  python scripts/research/build_dual_ledger_package.py \
    --run-dir exports/formal_runs/<formal_run_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def build_package(run_dir: Path) -> dict[str, object]:
    """Create dual_ledger_packages/<strategy>/ under the run dir."""
    frozen = run_dir / "frozen_inputs"
    account = run_dir / "account_backtest"
    manifest = json.loads((run_dir / "formal_run_manifest.json").read_text(encoding="utf-8"))
    strategies = manifest.get("strategy_ids") or []
    if not strategies:
        strategies = ["production_governed_vol_position_v1_2b_dynamic_score"]

    reports = []
    for strategy in strategies:
        pkg_dir = run_dir / "dual_ledger_packages" / strategy
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # release identity
        (pkg_dir / "release_identity.json").write_text(json.dumps({
            "release_id": manifest.get("formal_pit_run_id", ""),
            "strategy": strategy,
            "formal_run_id": manifest.get("formal_run_id", ""),
        }, ensure_ascii=False, indent=2))

        # orders from ledger events
        events = pd.read_csv(account / "trusted_account_backtest_ledger_events.csv")
        orders = events.rename(columns={
            "order_id": "order_id", "trade_date": "trade_date", "symbol": "symbol",
            "side": "side", "shares": "shares", "limit_price": "limit_price",
            "tif": "tif",
        })
        orders.to_parquet(pkg_dir / "orders.parquet", index=False)

        # market snapshot from prices
        prices = pd.read_csv(frozen / "prices.csv")
        prices.to_parquet(pkg_dir / "market_snapshot.parquet", index=False)

        # corporate actions / lifecycle / calendar
        pd.read_csv(frozen / "strict_corporate_actions.csv").to_parquet(
            pkg_dir / "corporate_actions.parquet", index=False)
        pd.read_csv(frozen / "strict_security_lifecycle.csv").to_parquet(
            pkg_dir / "security_lifecycle.parquet", index=False)
        pd.read_csv(frozen / "trade_calendar.csv").to_parquet(
            pkg_dir / "calendar.parquet", index=False)

        # cost / execution models
        (pkg_dir / "cost_model.json").write_text(json.dumps({
            "cost_rate_one_way": 0.00075, "slippage_bps_one_way": 10,
        }))
        (pkg_dir / "execution_model.json").write_text(json.dumps({
            "signal_timing": "T_CLOSE", "fill_timing": "T_PLUS_1_OPEN",
        }))

        reports.append({"strategy": strategy, "package": str(pkg_dir)})

    return {"status": "PASS", "packages": reports}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_package(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
