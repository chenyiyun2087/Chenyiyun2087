#!/usr/bin/env python3
"""Build dual-ledger packages from a completed formal backtest run.

Reads the backtest CSVs and frozen inputs from a formal run directory,
then writes per-strategy dual_ledger_packages/<strategy>/ directories
that run_dual_ledger_acceptance.py can consume.

v5.2: DATA_E0 — the strict ledger packages are built from the same
frozen inputs that the backtest consumed, plus the order-plan events
recorded by the ExecutionLedger during the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    CANONICAL_SCHEMA_VERSION,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DUAL_LEDGER_PACKAGES_DIRNAME = "dual_ledger_packages"
CANONICAL_KERNEL_MANIFEST_NAME = "canonical_kernel.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_orders(ledger_events: pd.DataFrame, strategy: str,
                  release_id: str, run_id: str) -> pd.DataFrame:
    """Extract PLANNED orders from ledger events for one strategy."""
    df = ledger_events[
        (ledger_events["strategy"] == strategy)
        & (ledger_events["event_type"] == "order")
        & (ledger_events["order_status"] == "PLANNED")
    ].copy()
    if df.empty:
        return pd.DataFrame()
    orders = pd.DataFrame()
    orders["order_id"] = df["order_id"].astype(str)
    orders["symbol"] = df["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
    orders["side"] = df["side"].astype(str).str.upper()
    orders["shares"] = pd.to_numeric(df["planned_shares"], errors="coerce").fillna(0).astype(int)
    orders["planned_price"] = pd.to_numeric(df["planned_price"], errors="coerce").fillna(0.0)
    orders["planned_notional"] = pd.to_numeric(df["planned_notional"], errors="coerce").fillna(0.0)
    orders["signal_date"] = df.get("signal_date", df.get("trade_date", ""))
    orders["execution_date"] = df.get("execution_date", df.get("trade_date", ""))
    orders["cost_rate"] = pd.to_numeric(df.get("cost_rate", 0.00075), errors="coerce").fillna(0.00075)
    orders["lot_size"] = pd.to_numeric(df.get("lot_size", 100), errors="coerce").fillna(100).astype(int)
    orders["release_id"] = release_id
    orders["run_id"] = run_id
    orders["canonical_kernel_id"] = CANONICAL_KERNEL_ID
    orders["canonical_kernel_version"] = CANONICAL_KERNEL_VERSION
    orders["canonical_schema_version"] = CANONICAL_SCHEMA_VERSION
    return orders


def _build_market_snapshot(prices: pd.DataFrame, tradable_universe: pd.DataFrame,
                           lifecycle: pd.DataFrame) -> pd.DataFrame:
    """Build market snapshot with tradability flags."""
    snap = prices.copy()
    snap["trade_date"] = pd.to_datetime(snap["trade_date"], errors="coerce")
    snap["symbol"] = snap["symbol"].astype(str).str.split(".").str[0].str.zfill(6)

    # Default tradability
    snap["is_tradable"] = True
    snap["is_listed"] = True
    snap["is_suspended"] = False
    snap["is_st"] = False
    snap["price_tick"] = "0.01"

    # Merge tradable universe
    if not tradable_universe.empty:
        tu = tradable_universe.copy()
        tu["trade_date"] = pd.to_datetime(tu["trade_date"], errors="coerce")
        tu["symbol"] = tu["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
        tu_cols = ["trade_date", "symbol"]
        for col in ["is_listed", "is_suspended", "is_st"]:
            if col in tu.columns:
                tu_cols.append(col)
        snap = snap.merge(tu[tu_cols], on=["trade_date", "symbol"], how="left", suffixes=("", "_tu"))
        for col in ["is_listed", "is_suspended", "is_st"]:
            if f"{col}_tu" in snap.columns:
                snap[col] = snap[f"{col}_tu"].fillna(snap[col])
                snap.drop(columns=[f"{col}_tu"], inplace=True)

    # Merge lifecycle for ST status
    if not lifecycle.empty:
        lc = lifecycle.copy()
        lc["trade_date"] = pd.to_datetime(lc["trade_date"], errors="coerce")
        lc["symbol"] = lc["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
        if "is_st" in lc.columns:
            snap = snap.merge(lc[["trade_date", "symbol", "is_st"]],
                             on=["trade_date", "symbol"], how="left", suffixes=("", "_lc"))
            snap["is_st"] = snap["is_st_lc"].fillna(snap["is_st"]).infer_objects(copy=False)
            snap.drop(columns=["is_st_lc"], inplace=True, errors="ignore")

    # Booleans → 0/1
    for col in ["is_tradable", "is_listed", "is_suspended", "is_st"]:
        if col in snap.columns:
            snap[col] = snap[col].astype(int)

    # Open / close price columns for dual-ledger replay
    # The dual ledger expects: raw_open, raw_close, prev_raw_close
    for price_col in ("open", "close", "pre_close"):
        if price_col in snap.columns:
            raw_name = "raw_open" if price_col == "open" else ("raw_close" if price_col == "close" else "raw_pre_close")
            snap[raw_name] = snap[price_col]
        elif price_col == "pre_close":
            snap["raw_pre_close"] = snap.get("close", 0)
        elif price_col == "close":
            snap["raw_close"] = snap.get("close", 0)
        elif price_col == "open":
            snap["raw_open"] = snap.get("close", 0)
    # prev_raw_close is a separate alias needed by the dual ledger
    if "raw_pre_close" in snap.columns and "prev_raw_close" not in snap.columns:
        snap["prev_raw_close"] = snap["raw_pre_close"]
    elif "pre_close" in snap.columns and "prev_raw_close" not in snap.columns:
        snap["prev_raw_close"] = snap["pre_close"]
    elif "prev_raw_close" not in snap.columns:
        snap["prev_raw_close"] = snap.get("raw_close", snap.get("close", 0))

    # Filter to needed columns
    wanted = ["trade_date", "symbol", "is_tradable", "is_listed", "is_suspended",
              "is_st", "price_tick"]
    for c in ["raw_open", "prev_raw_close", "raw_close"]:
        if c in snap.columns:
            wanted.append(c)
    return snap[[c for c in wanted if c in snap.columns]].copy()


def _safe_serialize(obj: Any) -> Any:
    """Convert numpy types to Python native types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_safe_serialize(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_serialize(x) for x in obj]
    return obj


def build_dual_ledger_packages(
    formal_run_dir: Path,
    release_id: str = "v5.2",
) -> dict[str, Any]:
    """Build dual-ledger packages for all strategies in a formal run."""
    account_dir = formal_run_dir / "account_backtest"
    frozen_dir = formal_run_dir / "frozen_inputs"

    if not account_dir.is_dir():
        return {"status": "BLOCKED", "reason": "account_backtest_dir_missing",
                "path": str(account_dir)}
    if not frozen_dir.is_dir():
        return {"status": "BLOCKED", "reason": "frozen_inputs_dir_missing",
                "path": str(frozen_dir)}

    # Read backtest outputs
    events_path = account_dir / "trusted_account_backtest_ledger_events.csv"
    if not events_path.is_file():
        return {"status": "BLOCKED", "reason": "ledger_events_csv_missing"}

    ledger_events = pd.read_csv(events_path)
    if ledger_events.empty:
        # No strict-ledger events — the strategy may have taken no positions
        # or strict mode wasn't active.  Produce empty packages so the dual
        # ledger verification can report zero-difference.
        ledger_events = pd.DataFrame(columns=["strategy", "event_type", "order_status"])

    # Read frozen inputs
    prices = pd.read_csv(frozen_dir / "prices.csv")
    tradable = pd.read_csv(frozen_dir / "tradable_universe.csv")
    lifecycle = pd.read_csv(frozen_dir / "strict_security_lifecycle.csv")
    corp_actions = pd.read_csv(frozen_dir / "strict_corporate_actions.csv")
    calendar = pd.read_csv(frozen_dir / "trade_calendar.csv")

    # Map PIT column names to dual-ledger expected column names
    PIT_ACTION_MAP = {
        "DIVIDEND": "dividend_cash",
        "BONUS": "stock_bonus",
        "SPLIT": "split_merge",
        "RIGHTS": "rights_subscription",
        "CONVERSION": "share_conversion",
        "DELIST": "delist_writeoff",
        "DELIST_CASH": "delist_cash_settlement",
    }
    if "action_type" not in corp_actions.columns and "corporate_action_type" in corp_actions.columns:
        corp_actions["action_type"] = corp_actions["corporate_action_type"].map(
            PIT_ACTION_MAP
        ).fillna(corp_actions["corporate_action_type"].str.lower())
    elif "action_type" not in corp_actions.columns:
        corp_actions["action_type"] = "dividend_cash"
    # Map PIT field names → dual-ledger expected field names
    for pit_col, ledger_col in [
        ("cash_dividend", "cash_per_share"),
        ("bonus_ratio", "stock_ratio"),
        ("rights_issue_ratio", "rights_ratio"),
        ("rights_issue_price", "rights_price"),
    ]:
        if pit_col in corp_actions.columns and ledger_col not in corp_actions.columns:
            corp_actions[ledger_col] = corp_actions[pit_col]

    # Read backtest report for run metadata
    report_path = account_dir / "trusted_account_backtest_report.json"
    run_id = formal_run_dir.name
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        run_id = report.get("provenance", {}).get("run_id", run_id)

    # Build market snapshot once (same for all strategies)
    market_snapshot = _build_market_snapshot(prices, tradable, lifecycle)

    # Parse strategies from ledger events or summary CSV
    strategies: list[str] = []
    if "strategy" in ledger_events.columns:
        strategies = sorted(ledger_events["strategy"].dropna().unique().tolist())
    if not strategies:
        summary_path = account_dir / "trusted_account_backtest_summary.csv"
        if summary_path.is_file():
            summary = pd.read_csv(summary_path)
            if "strategy" in summary.columns:
                strategies = sorted(summary["strategy"].dropna().unique().tolist())
    if not strategies:
        return {"status": "BLOCKED", "reason": "no_strategies_found"}

    dual_root = account_dir / DUAL_LEDGER_PACKAGES_DIRNAME
    dual_root.mkdir(parents=True, exist_ok=True)
    kernel_manifest = {
        "canonical_kernel_id": CANONICAL_KERNEL_ID,
        "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "builder": "scripts.research.build_dual_ledger_packages",
        "builder_version": "1.0.0",
        "trusted": True,
    }
    (dual_root / CANONICAL_KERNEL_MANIFEST_NAME).write_text(
        json.dumps(kernel_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    results: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        pkg_dir = dual_root / strategy
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / CANONICAL_KERNEL_MANIFEST_NAME).write_text(
            json.dumps(kernel_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        # orders.parquet
        orders = _build_orders(ledger_events, strategy, release_id, run_id)
        orders_path = pkg_dir / "orders.parquet"
        orders.to_parquet(orders_path, index=False)

        # market_snapshot.parquet
        mkt_path = pkg_dir / "market_snapshot.parquet"
        market_snapshot.to_parquet(mkt_path, index=False)

        # corporate_actions.parquet
        ca_path = pkg_dir / "corporate_actions.parquet"
        corp_actions.to_parquet(ca_path, index=False)

        # security_lifecycle.parquet
        sl_path = pkg_dir / "security_lifecycle.parquet"
        lifecycle.to_parquet(sl_path, index=False)

        # calendar.parquet
        cal_path = pkg_dir / "calendar.parquet"
        calendar.to_parquet(cal_path, index=False)

        # cost_model.json (must be before release_identity for cost_model_id)
        cost_model = {
            "commission_rate": 0.00075,
            "min_commission_cny": 5.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
            "open_auction_slippage_bps": 10,
            "gap_bps": 0,
            "spread_bps": 0,
            "adv_impact_bps": 0,
            "missed_fill_bps": 0,
            "stamp_duty_bps": 10,
            "cost_contract": "canonical_componentized_v1",
        }
        (pkg_dir / "cost_model.json").write_text(
            json.dumps(cost_model, ensure_ascii=False, indent=2))

        # execution_model.json (must be before release_identity for execution_model_id)
        exec_model = {
            "execution_model": "strict_t1_open_precommit",
            "signal_time": "T15:30:00+08:00",
            "execution_time": "T+1 09:30:00+08:00",
            "price_basis": "open",
            "price_tick_table": {"default": "0.01", "4": "0.001", "8": "0.001",
                                "92": "0.001", "300": "0.001", "301": "0.001",
                                "688": "0.001", "689": "0.001"},
        }
        (pkg_dir / "execution_model.json").write_text(
            json.dumps(exec_model, ensure_ascii=False, indent=2))

        # release_identity.json — must match runtime.contracts.ReleaseIdentity
        # Read backtest report for provenance metadata
        backtest_report: dict[str, Any] = {}
        if report_path.is_file():
            backtest_report = json.loads(report_path.read_text(encoding="utf-8"))
        provenance = backtest_report.get("provenance", {})
        params = backtest_report.get("params", {})
        git_sha = provenance.get("report_git_sha", "")
        config_fp = provenance.get("config_fingerprint", "")
        strategy_version = provenance.get("strategy_version", "v5.2")
        signal_dates_identity = sorted(orders["signal_date"].dropna().unique().tolist()) if not orders.empty else []
        exec_dates_identity = sorted(orders["execution_date"].dropna().unique().tolist()) if not orders.empty else []
        identity = {
            "release_id": release_id,
            "run_id": run_id,
            "strategy_id": strategy,
            "strategy_version": str(strategy_version),
            "git_commit_sha": str(git_sha) if len(str(git_sha)) >= 40 else "0" * 40,
            "config_sha": str(config_fp) if len(str(config_fp)) == 64 else "0" * 64,
            "data_snapshot_sha": _sha(mkt_path),
            "calendar_snapshot_sha": _sha(cal_path),
            "corporate_action_snapshot_sha": _sha(ca_path),
            "lifecycle_snapshot_sha": _sha(sl_path),
            "cost_model_id": hashlib.sha256(json.dumps(cost_model, sort_keys=True).encode()).hexdigest(),
            "execution_model_id": hashlib.sha256(json.dumps(exec_model, sort_keys=True).encode()).hexdigest(),
            "initial_capital": 500000.0,
            "signal_date": signal_dates_identity[0] if signal_dates_identity else "2022-01-01",
            "execution_date": exec_dates_identity[-1] if exec_dates_identity else "2024-12-31",
        }
        identity_path = pkg_dir / "release_identity.json"
        identity_path.write_text(json.dumps(identity, ensure_ascii=False, indent=2))

        results[strategy] = {
            "orders": len(orders),
            "market_rows": len(market_snapshot),
            "package_dir": str(pkg_dir),
            "canonical_kernel_id": CANONICAL_KERNEL_ID,
            "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
        }

    return {
        "status": "PASS",
        "strategies": strategies,
        "packages": results,
        "dual_root": str(dual_root),
        "canonical_kernel_id": CANONICAL_KERNEL_ID,
        "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-run-dir", type=Path, required=True,
                        help="Path to formal run directory (contains account_backtest/ and frozen_inputs/)")
    parser.add_argument("--release-id", default="v5.2")
    args = parser.parse_args()
    result = build_dual_ledger_packages(args.formal_run_dir, args.release_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
