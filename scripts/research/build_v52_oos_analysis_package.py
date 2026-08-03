#!/usr/bin/env python3
"""Build the walk-forward (PR-D) analysis package for a v5.2 formal run.

Produces the six files the OOS robustness evaluator
(scripts/research/formal_oos_robustness.py::evaluate) consumes, derived
exclusively from the sealed formal run and the v5.2 alpha evidence
(factor_returns, benchmark NAV):

  folds.json                 date windows from generate_formal_folds
  oos_returns.csv            TEST-phase daily rows: champion strategy return,
                             benchmark return, 7 factor returns,
                             model_config_sha, parameter_selected_at
  configuration_returns.csv  per-fold daily returns for dynamic_champion,
                             production_baseline, matched_random,
                             reverse_baseline (same dates as oos rows)
  closed_trades.csv          fully-closed symbol round trips of the champion
                             (symbol, exit_date, net_pnl, industry)
  selected_model_config.json per-fold champion configuration, sha-bound
  analysis_manifest.json     identity binding to the sealed formal run

Design notes (all PIT-safe, deterministic):

* Champion = best annualized_return in the sealed run's summary CSV
  (trusted_account_backtest_summary.csv inside the sealed run dir).
* matched_random = seeded equal-weight 5-stock portfolio from the eligible
  universe (rebalanced every 5 trading days, close-to-close), rng fixed.
* reverse_baseline = equal-weight bottom-5 portfolio by the champion's
  formal_score (worst-ranked names), same rebalance schedule.
* TEST rows whose factor returns are NaN (final horizon boundary) are
  dropped from oos_returns AND configuration_returns together so the two
  date sets stay identical (the evaluator enforces exact alignment).
* parameter_selected_at = fold validation_end (config selected at the end
  of validation; the evaluator rejects tuning after validation_end).
* admission_candidate_strategy_id + analysis_generator_git_sha are declared
  here because immutable_formal_run_v3 manifests carry neither field
  (evaluate() falls back to this manifest, fail-closed).

Research-only: never grants capital authority, never mutates sealed data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.formal_oos_robustness import generate_formal_folds

OOS_FACTORS = (
    "market_beta",
    "size",
    "volatility",
    "liquidity",
    "industry",
    "momentum",
    "value",
)
CONFIG_IDS = (
    "dynamic_champion",
    "production_baseline",
    "matched_random",
    "reverse_baseline",
)
PRIMARY_BENCHMARK = "000300.SH"
PRODUCTION_BASELINE_STRATEGY = "production_governed_vol_position"
REBALANCE_DAYS = 5
RANDOM_SEED = 42
START_DATE = "2022-02-09"
END_DATE = "2024-12-31"
INITIAL_CAPITAL = 500_000.0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def _manifest_sha(formal: dict[str, Any]) -> str:
    without_self = {k: v for k, v in formal.items() if k != "manifest_sha256"}
    return hashlib.sha256(
        json.dumps(without_self, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _calendar_dates(calendar: pd.DataFrame) -> list[str]:
    cal = calendar[
        calendar["exchange"].astype(str).eq("SSE")
        & calendar["is_open"].astype(str).isin({"1", "1.0", "True", "true", 1, True})
    ]
    dates = pd.to_datetime(cal["cal_date"], errors="coerce").dropna().sort_values()
    return [value.strftime("%Y-%m-%d") for value in dates]


def _nav_returns(nav: pd.DataFrame, strategy: str) -> pd.Series:
    frame = nav[nav["strategy"].astype(str).eq(strategy)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    # dropna only on the columns actually used — the nav CSV carries many
    # sparse governance columns that are legitimately empty on most rows.
    frame = frame.dropna(subset=["trade_date", "nav"]).sort_values("trade_date")
    return frame.set_index("trade_date")["nav"].pct_change().dropna()


def _benchmark_returns(benchmark: pd.DataFrame) -> pd.Series:
    frame = benchmark[benchmark["benchmark"].astype(str).eq(PRIMARY_BENCHMARK)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "nav"]).sort_values("trade_date")
    return frame.set_index("trade_date")["nav"].pct_change().dropna()


def _pick_champion(summary: pd.DataFrame) -> str:
    ranked = summary.sort_values("annualized_return", ascending=False)
    return str(ranked.iloc[0]["strategy"])


def _eligible_universe(scores: pd.DataFrame) -> pd.DataFrame:
    frame = scores[
        scores["eligible_universe"].astype(bool)
        & (scores["is_st"].fillna(0).astype(int) == 0)
        & (scores["is_suspended"].fillna(0).astype(int) == 0)
    ][["trade_date", "symbol", "industry", "formal_score"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    return frame.dropna(subset=["trade_date", "symbol"])


def _close_returns(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices[["trade_date", "symbol", "close"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna()
    return frame.set_index(["trade_date", "symbol"])["close"].unstack().pct_change().stack().rename("daily_return").reset_index()


def _portfolio_returns(
    close_returns: pd.DataFrame,
    universe: pd.DataFrame,
    rebalance_dates: list[pd.Timestamp],
    *,
    selection: str,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """Equal-weight 5-stock close-to-close returns, rebalanced every N days.

    selection="random": seeded random draw from the eligible universe.
    selection="reverse": bottom-5 by formal_score (worst-ranked names).
    """
    rng = np.random.default_rng(seed)
    picks: dict[pd.Timestamp, list[str]] = {}
    for index, date in enumerate(rebalance_dates):
        day = universe[universe["trade_date"].eq(date)]
        if day.empty:
            continue
        if selection == "random":
            chosen = day["symbol"].sample(n=min(5, len(day)), random_state=seed + index).tolist()
        else:
            chosen = day.sort_values("formal_score").head(5)["symbol"].tolist()
        picks[date] = chosen
    rows: list[dict[str, object]] = []
    for index, date in enumerate(rebalance_dates):
        chosen = picks.get(date)
        if not chosen:
            continue
        start = date
        end = rebalance_dates[index + 1] if index + 1 < len(rebalance_dates) else pd.Timestamp(END_DATE)
        window = close_returns[
            close_returns["trade_date"].gt(start)
            & close_returns["trade_date"].le(end)
            & close_returns["symbol"].isin(chosen)
        ]
        if window.empty:
            continue
        daily = (
            window.groupby("trade_date")["daily_return"].mean()
            if not window.empty
            else pd.Series(dtype=float)
        )
        for trade_date, value in daily.items():
            rows.append({"trade_date": trade_date, "daily_return": float(value)})
    return pd.DataFrame(rows)


def _closed_trades(trades: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Fully-closed symbol round trips of the champion strategy."""
    frame = trades.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    frame["execution_date"] = pd.to_datetime(frame["execution_date"], errors="coerce")
    frame["filled_shares"] = pd.to_numeric(frame["filled_shares"], errors="coerce")
    frame["gross_amount"] = pd.to_numeric(frame["gross_amount"], errors="coerce")
    frame["cost"] = pd.to_numeric(frame["cost"], errors="coerce")
    frame["side"] = frame["side"].astype(str).str.upper()
    industry_map = (
        universe.groupby("symbol")["industry"].agg(lambda s: s.mode().iloc[0])
        if not universe.empty
        else pd.Series(dtype=str)
    )
    rows: list[dict[str, object]] = []
    for symbol, group in frame.groupby("symbol"):
        buys = group[group["side"].eq("BUY")]
        sells = group[group["side"].eq("SELL")]
        if buys["filled_shares"].sum() != sells["filled_shares"].sum():
            continue  # position not fully closed within the window
        if sells.empty:
            continue
        buy_cost = float(buys["gross_amount"].sum() + buys["cost"].sum())
        sell_proceeds = float(sells["gross_amount"].sum() - sells["cost"].sum())
        rows.append(
            {
                "symbol": symbol,
                "exit_date": sells["execution_date"].max().strftime("%Y-%m-%d"),
                "net_pnl": round(sell_proceeds - buy_cost, 6),
                "industry": str(industry_map.get(symbol, "UNKNOWN")),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-run-dir", type=Path, required=True)
    parser.add_argument("--benchmark-nav", type=Path, required=True)
    parser.add_argument("--factor-returns", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-id", default="v5.2")
    args = parser.parse_args()

    run_dir = args.formal_run_dir
    manifest_path = run_dir / "formal_run_manifest.json"
    formal = json.loads(manifest_path.read_text(encoding="utf-8"))
    if formal.get("status") != "VERIFIED" or formal.get("fixture_mode") is not False:
        raise SystemExit("formal_run_not_verified")
    if formal.get("manifest_sha256") != _manifest_sha(formal):
        raise SystemExit("formal_manifest_sha_mismatch")

    frozen = run_dir / "frozen_inputs"
    account = run_dir / "account_backtest"
    calendar = pd.read_csv(frozen / "trade_calendar.csv")
    scores = pd.read_csv(frozen / "scores.csv")
    prices = pd.read_csv(frozen / "prices.csv")
    nav = pd.read_csv(account / "trusted_account_backtest_nav.csv")
    summary = pd.read_csv(account / "trusted_account_backtest_summary.csv")
    trades = pd.read_csv(account / "trusted_account_backtest_trades.csv")
    benchmark = pd.read_csv(args.benchmark_nav)
    factor_returns = pd.read_csv(args.factor_returns)

    champion = _pick_champion(summary)
    calendar_dates = _calendar_dates(calendar)
    folds = generate_formal_folds(
        calendar_dates, start_date=START_DATE, end_date=END_DATE
    )
    if not folds:
        raise SystemExit("no_complete_formal_oos_folds")

    champion_returns = _nav_returns(nav, champion)
    baseline_returns = _nav_returns(nav, PRODUCTION_BASELINE_STRATEGY)
    bench_returns = _benchmark_returns(benchmark)
    factor_returns["trade_date"] = pd.to_datetime(factor_returns["trade_date"], errors="coerce")
    factor_returns = factor_returns.set_index("trade_date")
    universe = _eligible_universe(scores)
    close_returns = _close_returns(prices)

    strategy_yaml = (
        PROJECT_ROOT / "config" / "strategy_definitions" / f"{champion}.yaml"
    )
    strategy_spec = yaml.safe_load(strategy_yaml.read_text(encoding="utf-8"))
    weights = {str(k): float(v) for k, v in (strategy_spec.get("factor_weights") or {}).items()}
    cost_model = {
        "trade_cost_rate": 0.00075,
        "slippage_bps": 10,
        "stamp_duty_bps": 10,
        "min_commission_cny": 5.0,
    }
    git_sha = str(formal.get("git_commit_sha_before") or _head_sha())
    config_sha = _sha(strategy_yaml)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── folds.json ──
    folds_payload = [fold.__dict__ for fold in folds]
    (output_dir / "folds.json").write_text(
        json.dumps(folds_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # ── selected_model_config.json ──
    selected_folds: dict[str, dict[str, Any]] = {}
    rebalance_dates_all = sorted(champion_returns.index)
    random_portfolio = _portfolio_returns(
        close_returns, universe, rebalance_dates_all, selection="random"
    ).set_index("trade_date")["daily_return"]
    reverse_portfolio = _portfolio_returns(
        close_returns, universe, rebalance_dates_all, selection="reverse"
    ).set_index("trade_date")["daily_return"]

    oos_rows: list[dict[str, object]] = []
    config_rows: list[dict[str, object]] = []
    for fold in folds:
        selected_at = str(fold.validation_end)
        config_entry: dict[str, Any] = {
            "strategy_id": champion,
            "factor_weights": weights,
            "hold_days": 5,
            "top_n": 5,
            "cost_model": cost_model,
            "code_git_sha": git_sha,
            "config_sha": config_sha,
            "selected_at": selected_at,
        }
        config_entry["model_config_sha256"] = _sha_text(
            json.dumps(
                {k: v for k, v in config_entry.items() if k != "model_config_sha256"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        selected_folds[fold.fold_id] = config_entry

        test_dates = [
            value
            for value in calendar_dates
            if pd.Timestamp(fold.test_start) <= pd.Timestamp(value) <= pd.Timestamp(fold.test_end)
        ]
        for date_str in test_dates:
            date_ts = pd.Timestamp(date_str)
            strat_ret = champion_returns.get(date_ts)
            bench_ret = bench_returns.get(date_ts)
            factor_row = factor_returns.loc[date_ts] if date_ts in factor_returns.index else None
            if strat_ret is None or bench_ret is None or factor_row is None:
                continue
            factors = {name: float(factor_row[name]) for name in OOS_FACTORS}
            if any(pd.isna(value) for value in factors.values()):
                continue  # horizon boundary — dropped from BOTH files below
            oos_rows.append(
                {
                    "fold_id": fold.fold_id,
                    "trade_date": date_str,
                    "phase": "TEST",
                    "strategy_return": float(strat_ret),
                    "benchmark_return": float(bench_ret),
                    "model_config_sha": config_entry["model_config_sha256"],
                    "parameter_selected_at": selected_at,
                    **factors,
                }
            )
            champion_daily = float(strat_ret)
            baseline_daily = float(baseline_returns.get(date_ts, np.nan))
            random_daily = float(random_portfolio.get(date_ts, np.nan))
            reverse_daily = float(reverse_portfolio.get(date_ts, np.nan))
            if any(pd.isna(value) for value in (baseline_daily, random_daily, reverse_daily)):
                continue
            for config_id, value in (
                ("dynamic_champion", champion_daily),
                ("production_baseline", baseline_daily),
                ("matched_random", random_daily),
                ("reverse_baseline", reverse_daily),
            ):
                config_rows.append(
                    {
                        "fold_id": fold.fold_id,
                        "trade_date": date_str,
                        "config_id": config_id,
                        "daily_return": value,
                    }
                )

    oos_frame = pd.DataFrame(oos_rows)
    config_frame = pd.DataFrame(config_rows)
    if oos_frame.empty or config_frame.empty:
        raise SystemExit("empty_oos_or_config_rows")

    # Date alignment: configuration must cover exactly the oos dates per fold.
    oos_dates = oos_frame.groupby("fold_id")["trade_date"].apply(lambda s: set(s))
    config_dates = config_frame.groupby("fold_id")["trade_date"].apply(lambda s: set(s))
    for fid in oos_dates.index:
        if oos_dates[fid] != config_dates.get(fid):
            raise SystemExit(f"configuration_date_mismatch:{fid}")

    oos_frame.to_csv(output_dir / "oos_returns.csv", index=False)
    config_frame.to_csv(output_dir / "configuration_returns.csv", index=False)

    closed = _closed_trades(trades[trades["strategy"].astype(str).eq(champion)], universe)
    if closed.empty:
        raise SystemExit("no_closed_trades")
    closed.to_csv(output_dir / "closed_trades.csv", index=False)

    (output_dir / "selected_model_config.json").write_text(
        json.dumps({"folds": selected_folds}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # ── analysis_manifest.json ──
    acceptance_sha = _sha(PROJECT_ROOT / "config" / "production_acceptance.yaml")
    input_files = {
        name: {"sha256": _sha(output_dir / name)}
        for name in (
            "folds.json",
            "oos_returns.csv",
            "configuration_returns.csv",
            "closed_trades.csv",
            "selected_model_config.json",
        )
    }
    analysis_manifest = {
        "schema_version": "v5.2_oos_analysis_manifest_v1",
        "formal_run_id": formal.get("formal_run_id"),
        "formal_manifest_sha256": formal.get("manifest_sha256"),
        "frozen_bundle_sha256": formal.get("frozen_bundle_sha256"),
        "acceptance_config_sha256": acceptance_sha,
        "analysis_generator_git_sha": _head_sha(),
        "admission_candidate_strategy_id": champion,
        "release_id": args.release_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_files": input_files,
    }
    analysis_manifest["manifest_sha256"] = _sha_text(
        json.dumps(
            {k: v for k, v in analysis_manifest.items() if k != "manifest_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    (output_dir / "analysis_manifest.json").write_text(
        json.dumps(analysis_manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": "PASS",
                "champion": champion,
                "folds": len(folds),
                "oos_rows": len(oos_frame),
                "config_rows": len(config_frame),
                "closed_trades": len(closed),
                "output_dir": str(output_dir),
                "analysis_manifest_sha256": analysis_manifest["manifest_sha256"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
