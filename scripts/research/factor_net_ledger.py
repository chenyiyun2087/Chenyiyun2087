#!/usr/bin/env python3
"""Build an explicit T+1 open-to-open factor portfolio ledger.

This is research evidence only.  The long-short leg is synthetic because the
repository contains no stock-borrow or short-sale availability evidence.
QFQ execution prices are also blocked until their adjustment anchors are
proved point-in-time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import (
    canonical_sha,
    load_acceptance_config,
    load_validation_profile,
)
from scripts.research.factor_challenger_lab import (
    DEFAULT_FACTOR_DIR,
    _annualized_metrics,
    _file_sha,
)
from scripts.research.factor_evidence import DEFAULT_SOURCE


def _weights(symbols: set[str]) -> dict[str, float]:
    if not symbols:
        return {}
    weight = 1.0 / len(symbols)
    return {symbol: weight for symbol in symbols}


def _one_way_turnover(
    previous: set[str], current: set[str], *, liquidate: bool = False
) -> float:
    if liquidate:
        return 1.0 if previous else 0.0
    if not previous:
        return 1.0 if current else 0.0
    old = _weights(previous)
    new = _weights(current)
    return float(
        sum(abs(new.get(symbol, 0.0) - old.get(symbol, 0.0)) for symbol in old | new)
        / 2.0
    )


def _portfolio_return(
    prices: pd.DataFrame,
    symbols: set[str],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> tuple[float | None, int]:
    if not symbols:
        return None, 0
    entry = prices.loc[
        (prices["trade_date"] == entry_date) & prices["symbol"].isin(symbols),
        ["symbol", "open_qfq"],
    ].rename(columns={"open_qfq": "entry_open"})
    exit_ = prices.loc[
        (prices["trade_date"] == exit_date) & prices["symbol"].isin(symbols),
        ["symbol", "open_qfq"],
    ].rename(columns={"open_qfq": "exit_open"})
    aligned = entry.merge(exit_, on="symbol", how="inner")
    aligned = aligned[
        (aligned["entry_open"] > 0) & (aligned["exit_open"] > 0)
    ].copy()
    missing = len(symbols) - int(len(aligned))
    if aligned.empty:
        return None, missing
    return float((aligned["exit_open"] / aligned["entry_open"] - 1.0).mean()), missing


def _membership(panel: pd.DataFrame, factor: str, date: pd.Timestamp) -> tuple[set[str], set[str]]:
    sample = panel.loc[panel["trade_date"] == date, ["symbol", factor]].dropna()
    if len(sample) < 20 or sample[factor].nunique() < 5:
        return set(), set()
    low = sample[factor].quantile(0.2)
    high = sample[factor].quantile(0.8)
    return (
        set(sample.loc[sample[factor] >= high, "symbol"].astype(str)),
        set(sample.loc[sample[factor] <= low, "symbol"].astype(str)),
    )


def _split_metrics(returns: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(returns, errors="coerce").dropna().reset_index(drop=True)
    split = max(1, int(len(values) * 0.70))
    return {
        "first_70pct": _annualized_metrics(values.iloc[:split]),
        "last_30pct_holdout": _annualized_metrics(values.iloc[split:]),
    }


def _overlap_matrix(panel: pd.DataFrame, factors: list[str]) -> dict[str, dict[str, float | None]]:
    dates = sorted(panel["trade_date"].dropna().unique())
    memberships = {
        factor: {date: _membership(panel, factor, date)[0] for date in dates}
        for factor in factors
    }
    result: dict[str, dict[str, float | None]] = {}
    for left in factors:
        result[left] = {}
        for right in factors:
            ratios: list[float] = []
            for date in dates:
                a, b = memberships[left][date], memberships[right][date]
                if a and b:
                    ratios.append(len(a & b) / len(a | b))
            result[left][right] = float(np.mean(ratios)) if ratios else None
    return result


def build_factor_net_ledger(
    factor_dir: Path,
    source_path: Path,
    output_dir: Path,
    *,
    profile_name: str = "alpha_v4_7",
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    acceptance = load_acceptance_config()
    panel_path = factor_dir / "factor_panel.csv"
    panel = pd.read_csv(panel_path)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"], errors="coerce")
    panel["symbol"] = panel["symbol"].astype(str).str.zfill(6)
    panel["low_volatility_value"] = (
        pd.to_numeric(panel["volatility"], errors="coerce")
        + pd.to_numeric(panel["value"], errors="coerce")
    ) / 2.0
    source = pd.read_parquet(
        source_path, columns=["ts_code", "trade_date", "open_qfq", "amount"]
    )
    source["symbol"] = source["ts_code"].astype(str).str.extract(r"(\d{6})", expand=False)
    source["trade_date"] = pd.to_datetime(
        source["trade_date"].astype(str), errors="coerce"
    )
    source = source.drop_duplicates(["symbol", "trade_date"], keep="last")
    source["open_qfq"] = pd.to_numeric(source["open_qfq"], errors="coerce")
    trade_dates = sorted(source["trade_date"].dropna().unique())
    next_trade_date = {
        trade_dates[index]: trade_dates[index + 1]
        for index in range(len(trade_dates) - 1)
    }
    factors = ["volatility", "value", "low_volatility_value"]
    frequencies = {"DAILY": 1, "WEEKLY_5D": 5, "MONTHLY_20D": 20}
    scenarios = list(acceptance["execution"]["scenarios"])
    ledger_rows: list[dict[str, Any]] = []
    scorecard: dict[str, Any] = {}
    for factor in factors:
        scorecard[factor] = {}
        signal_dates = sorted(panel.loc[panel[factor].notna(), "trade_date"].unique())
        for frequency, step in frequencies.items():
            selected_dates = signal_dates[::step]
            experiment_rows: list[dict[str, Any]] = []
            previous_long: set[str] = set()
            previous_short: set[str] = set()
            for index in range(len(selected_dates) - 1):
                signal_date = pd.Timestamp(selected_dates[index])
                next_signal = pd.Timestamp(selected_dates[index + 1])
                execution_date = next_trade_date.get(signal_date)
                exit_execution_date = next_trade_date.get(next_signal)
                if execution_date is None or exit_execution_date is None:
                    continue
                execution_date = pd.Timestamp(execution_date)
                exit_execution_date = pd.Timestamp(exit_execution_date)
                long_symbols, short_symbols = _membership(panel, factor, signal_date)
                long_return, long_missing = _portfolio_return(
                    source, long_symbols, execution_date, exit_execution_date
                )
                short_underlying_return, short_missing = _portfolio_return(
                    source, short_symbols, execution_date, exit_execution_date
                )
                if long_return is None or short_underlying_return is None:
                    continue
                long_turnover = _one_way_turnover(previous_long, long_symbols)
                short_turnover = _one_way_turnover(previous_short, short_symbols)
                if index == len(selected_dates) - 2:
                    long_turnover += _one_way_turnover(
                        long_symbols, set(), liquidate=True
                    )
                    short_turnover += _one_way_turnover(
                        short_symbols, set(), liquidate=True
                    )
                row: dict[str, Any] = {
                    "factor": factor,
                    "rebalance_frequency": frequency,
                    "signal_time": f"{signal_date.date().isoformat()}T15:30:00+08:00",
                    "execution_time": f"{execution_date.date().isoformat()}T09:30:00+08:00",
                    "exit_execution_time": (
                        f"{exit_execution_date.date().isoformat()}T09:30:00+08:00"
                    ),
                    "t_plus_1_execution": execution_date > signal_date,
                    "long_names": len(long_symbols),
                    "synthetic_short_names": len(short_symbols),
                    "missing_long_open_prices": long_missing,
                    "missing_short_open_prices": short_missing,
                    "long_turnover": long_turnover,
                    "synthetic_short_turnover": short_turnover,
                    "gross_long_return": long_return,
                    "gross_short_underlying_return": short_underlying_return,
                    "gross_synthetic_spread_return": (
                        long_return - short_underlying_return
                    ),
                }
                for scenario in scenarios:
                    scenario_id = str(scenario["id"])
                    one_way_cost = float(scenario["cost_rate"]) + float(
                        scenario["slippage_bps"]
                    ) / 10_000.0
                    row[f"{scenario_id}_net_long_return"] = (
                        long_return - long_turnover * one_way_cost
                    )
                    row[f"{scenario_id}_net_synthetic_spread_return"] = (
                        long_return
                        - short_underlying_return
                        - (long_turnover + short_turnover) * one_way_cost
                    )
                experiment_rows.append(row)
                previous_long, previous_short = long_symbols, short_symbols
            ledger_rows.extend(experiment_rows)
            experiment = pd.DataFrame(experiment_rows)
            scenario_metrics: dict[str, Any] = {}
            for scenario in scenarios:
                scenario_id = str(scenario["id"])
                net_long_column = f"{scenario_id}_net_long_return"
                net_spread_column = f"{scenario_id}_net_synthetic_spread_return"
                scenario_metrics[scenario_id] = {
                    "net_long": _annualized_metrics(
                        experiment.get(net_long_column, pd.Series(dtype=float))
                    ),
                    "net_synthetic_spread": _annualized_metrics(
                        experiment.get(net_spread_column, pd.Series(dtype=float))
                    ),
                    "net_long_time_split": _split_metrics(
                        experiment.get(net_long_column, pd.Series(dtype=float))
                    ),
                    "net_synthetic_spread_time_split": _split_metrics(
                        experiment.get(net_spread_column, pd.Series(dtype=float))
                    ),
                }
            scorecard[factor][frequency] = {
                "holding_periods": int(len(experiment)),
                "all_t_plus_1": bool(
                    not experiment.empty and experiment["t_plus_1_execution"].all()
                ),
                "missing_open_prices": int(
                    experiment.get(
                        "missing_long_open_prices", pd.Series(dtype=float)
                    ).sum()
                    + experiment.get(
                        "missing_short_open_prices", pd.Series(dtype=float)
                    ).sum()
                ),
                "data_completeness_status": (
                    "PASS"
                    if not experiment.empty
                    and int(
                        experiment.get(
                            "missing_long_open_prices", pd.Series(dtype=float)
                        ).sum()
                        + experiment.get(
                            "missing_short_open_prices", pd.Series(dtype=float)
                        ).sum()
                    )
                    == 0
                    else "BLOCKED"
                ),
                "average_long_turnover": (
                    float(experiment["long_turnover"].mean())
                    if not experiment.empty
                    else None
                ),
                "average_synthetic_short_turnover": (
                    float(experiment["synthetic_short_turnover"].mean())
                    if not experiment.empty
                    else None
                ),
                "gross_long": _annualized_metrics(
                    experiment.get("gross_long_return", pd.Series(dtype=float))
                ),
                "gross_synthetic_spread": _annualized_metrics(
                    experiment.get(
                        "gross_synthetic_spread_return", pd.Series(dtype=float)
                    )
                ),
                "cost_scenarios": scenario_metrics,
            }
    ledger = pd.DataFrame(ledger_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "factor_t1_ledger.csv"
    ledger.to_csv(ledger_path, index=False)
    blockers = [
        "history_below_252_days",
        "formal_pit_manifest_missing",
        "industry_neutralization_missing",
        "qfq_adjustment_anchor_not_pit_verified",
        "synthetic_short_leg_has_no_borrow_availability_evidence",
        "limit_and_suspension_fill_evidence_missing",
        "only_two_market_regimes_observed",
    ]
    total_missing_open_prices = int(
        sum(
            int(payload["missing_open_prices"])
            for factor_payload in scorecard.values()
            for payload in factor_payload.values()
        )
    )
    if total_missing_open_prices:
        blockers.append(
            f"selected_constituent_open_price_missing:{total_missing_open_prices}"
        )
        blockers.append(
            "source_panel_not_verified_as_complete_daily_investable_universe"
        )
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_5_factor_net_ledger_v1",
        "profile": profile_name,
        "status": "BLOCKED",
        "formal_evidence": False,
        "evidence_level": "E2",
        "portfolio_formation_time": "T_CLOSE_15_30_ASIA_SHANGHAI",
        "execution_time": "T_PLUS_1_OPEN_09_30_ASIA_SHANGHAI",
        "short_leg_semantics": "SYNTHETIC_RESEARCH_ONLY_NOT_A_SHARE_TRADABLE",
        "metric_interpretation": (
            "INCOMPLETE_UNIVERSE_DIAGNOSTIC_ONLY; missing selected open prices "
            "can bias every reported return and Sharpe"
        ),
        "account_capital_cny": float(profile["stress"]["initial_capital_cny"]),
        "source": {
            "factor_panel_path": str(panel_path),
            "factor_panel_sha256": _file_sha(panel_path),
            "price_source_path": str(source_path),
            "price_source_sha256": _file_sha(source_path),
            "ledger_path": str(ledger_path),
            "ledger_sha256": _file_sha(ledger_path),
        },
        "cost_scenarios": scenarios,
        "factor_overlap_top_quantile_jaccard": _overlap_matrix(panel, factors),
        "selected_constituent_missing_open_price_count": (
            total_missing_open_prices
        ),
        "scorecard": scorecard,
        "blockers": blockers,
        "economic_alpha_status": "BLOCKED",
        "capital_authority": False,
        "broker_permission": False,
        "allowed_incremental_capital_cny": 0,
    }
    report["content_sha256"] = canonical_sha(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    (output_dir / "factor_economic_scorecard.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-dir", type=Path, default=DEFAULT_FACTOR_DIR)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="alpha_v4_7")
    args = parser.parse_args()
    result = build_factor_net_ledger(
        args.factor_dir, args.source, args.output_dir, profile_name=args.profile
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
