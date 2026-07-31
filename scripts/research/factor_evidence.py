#!/usr/bin/env python3
"""Build honest partial factor/IC evidence from the local dated stock panel.

This builder produces diagnostics from observed prices only.  It does not
promote the result to E3 when history, industry classification, or formal PIT
coverage is insufficient.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha, load_validation_profile


DEFAULT_SOURCE = PROJECT_ROOT / "data/processed/bs_training_dataset_v5.parquet"
DEFAULT_POSITIONS = (
    PROJECT_ROOT
    / "exports/signal_research/20260618_213002_059138_trusted_account_backtest"
    / "trusted_account_backtest_positions.csv"
)
DEFAULT_NAV = (
    PROJECT_ROOT
    / "exports/signal_research/20260618_213002_059138_trusted_account_backtest"
    / "trusted_account_backtest_nav.csv"
)
DEFAULT_BENCHMARK = (
    PROJECT_ROOT
    / "exports/evidence_production/20260730_alpha_v4_2/benchmark"
    / "benchmark_nav_daily.csv"
)


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rank_score(series: pd.Series, *, reverse: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if reverse:
        numeric = -numeric
    return numeric.rank(method="average", pct=True) - 0.5


def _add_forward_returns(frame: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    dates = sorted(frame["trade_date"].dropna().unique())
    prices = frame[["symbol", "trade_date", "close_qfq"]].copy()
    for horizon in sorted(set([1, *horizons])):
        mapping = {
            dates[index]: dates[index + horizon]
            for index in range(len(dates) - horizon)
        }
        lookup = prices.rename(
            columns={
                "trade_date": "_target_date",
                "close_qfq": "_future_close",
            }
        )
        frame["_target_date"] = frame["trade_date"].map(mapping)
        frame = frame.merge(
            lookup,
            on=["symbol", "_target_date"],
            how="left",
        )
        frame[f"fwd_{horizon}d_return"] = (
            frame["_future_close"] / frame["close_qfq"] - 1.0
        )
        frame = frame.drop(columns=["_target_date", "_future_close"])
    return frame


def build_partial_factor_evidence(
    source_path: Path,
    positions_path: Path,
    nav_path: Path,
    benchmark_path: Path,
    output_dir: Path,
    *,
    profile_name: str = "formal_v5_0",
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    factors = [str(value) for value in profile["attribution"]["required_factors"]]
    horizons = [int(value) for value in profile["factor_ic"]["horizons"]]
    source_sha = _file_sha(source_path)
    version = f"bs_training_v5_partial_{source_sha[:12]}"
    columns = [
        "ts_code",
        "trade_date",
        "batch_date",
        "close_qfq",
        "pre_close_qfq",
        "ret_1d",
        "ret_20d",
        "amount",
        "circ_mv",
        "pb",
        "turnover_vol_20",
        "amihud_20",
    ]
    frame = pd.read_parquet(source_path, columns=columns)
    frame["symbol"] = (
        frame["ts_code"].astype(str).str.extract(r"(\d{6})", expand=False)
    )
    frame["trade_date"] = pd.to_datetime(
        frame["trade_date"].astype(str), errors="coerce"
    )
    frame["batch_date"] = pd.to_datetime(
        frame["batch_date"].astype(str), errors="coerce"
    )
    duplicate_count = int(frame.duplicated(["symbol", "trade_date"]).sum())
    frame = frame.drop_duplicates(["symbol", "trade_date"], keep="last")
    frame["ret_1d"] = (
        pd.to_numeric(frame["close_qfq"], errors="coerce")
        / pd.to_numeric(frame["pre_close_qfq"], errors="coerce")
        - 1.0
    )
    benchmark = pd.read_csv(benchmark_path)
    benchmark = benchmark[benchmark["benchmark"].astype(str) == "000300.SH"].copy()
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"], errors="coerce")
    benchmark["market_return"] = pd.to_numeric(
        benchmark["nav"], errors="coerce"
    ).pct_change()
    frame = frame.merge(
        benchmark[["trade_date", "market_return"]],
        on="trade_date",
        how="left",
    )
    beta_parts = []
    for _, group in frame.sort_values(["symbol", "trade_date"]).groupby(
        "symbol", sort=False
    ):
        group = group.copy()
        stock_return = pd.to_numeric(group["ret_1d"], errors="coerce")
        market_return = pd.to_numeric(group["market_return"], errors="coerce")
        covariance = stock_return.rolling(20, min_periods=10).cov(market_return)
        variance = market_return.rolling(20, min_periods=10).var()
        group["_beta_raw"] = covariance / variance.replace(0.0, np.nan)
        beta_parts.append(group)
    frame = pd.concat(beta_parts, ignore_index=True)
    regime_map = {
        "FREEZE": -2.0,
        "RISK_OFF": -1.0,
        "NEUTRAL": 0.0,
        "RISK_ON": 1.0,
        "BROAD_RISK_ON": 2.0,
        "index_weak": -1.0,
        "index_neutral": 0.0,
        "index_strong": 1.0,
    }
    nav = pd.read_csv(nav_path, usecols=["trade_date", "market_state"])
    nav["trade_date"] = pd.to_datetime(nav["trade_date"], errors="coerce")
    nav = nav.dropna(subset=["market_state"]).drop_duplicates(
        "trade_date", keep="last"
    )
    nav["market_regime"] = nav["market_state"].map(regime_map)
    frame = frame.merge(
        nav[["trade_date", "market_regime"]], on="trade_date", how="left"
    )
    positions = pd.read_csv(
        positions_path, usecols=["trade_date", "symbol", "weight"]
    )
    positions["trade_date"] = pd.to_datetime(
        positions["trade_date"], errors="coerce"
    )
    positions["symbol"] = (
        positions["symbol"].astype(str).str.extract(r"(\d{6})", expand=False)
    )
    positions = (
        positions.groupby(["trade_date", "symbol"], as_index=False)["weight"].sum()
    )
    frame = frame.merge(
        positions.rename(columns={"weight": "portfolio_weight"}),
        on=["trade_date", "symbol"],
        how="left",
    )
    frame["portfolio_weight"] = frame["portfolio_weight"].fillna(0.0)
    raw = {
        "market_beta": "_beta_raw",
        "size": "circ_mv",
        "volatility": "turnover_vol_20",
        "liquidity": "amihud_20",
        "momentum": "ret_20d",
        "value": "pb",
    }
    for factor, column in raw.items():
        reverse = factor in {"size", "volatility", "liquidity", "value"}
        frame[factor] = frame.groupby("trade_date")[column].transform(
            lambda series, reverse=reverse: _rank_score(
                series, reverse=reverse
            )
        )
    frame["industry"] = np.nan
    frame = _add_forward_returns(frame, horizons)
    frame["signal_time"] = (
        frame["trade_date"].dt.strftime("%Y-%m-%d") + "T15:30:00+08:00"
    )
    available = (
        frame["batch_date"].dt.strftime("%Y-%m-%d") + "T15:10:00+08:00"
    )
    for factor in factors:
        frame[f"{factor}_available_at"] = available
        frame[f"{factor}_data_version"] = version
        frame[f"{factor}_source_snapshot_sha256"] = source_sha
    panel_columns = [
        "trade_date",
        "symbol",
        "portfolio_weight",
        "signal_time",
        *factors,
        "fwd_1d_return",
        *(f"fwd_{horizon}d_return" for horizon in horizons),
        *(f"{factor}_available_at" for factor in factors),
        *(f"{factor}_data_version" for factor in factors),
        *(f"{factor}_source_snapshot_sha256" for factor in factors),
    ]
    panel = frame[panel_columns].copy()
    forward_columns = [f"fwd_{horizon}d_return" for horizon in horizons]
    panel = panel.dropna(subset=forward_columns).copy()
    panel["trade_date"] = panel["trade_date"].dt.date.astype(str)
    daily_rows: list[dict[str, Any]] = []
    all_dates = sorted(frame["trade_date"].dropna().unique())
    next_date = {
        all_dates[index]: all_dates[index + 1]
        for index in range(len(all_dates) - 1)
    }
    for date, group in frame.groupby("trade_date", sort=True):
        realized_date = next_date.get(date)
        if realized_date is None:
            continue
        realized_date = pd.Timestamp(realized_date)
        row: dict[str, Any] = {
            "trade_date": realized_date.date().isoformat(),
            "signal_time": (
                f"{realized_date.date().isoformat()}T16:00:00+08:00"
            ),
        }
        for factor in factors:
            sample = group[[factor, "fwd_1d_return"]].dropna()
            if len(sample) >= 20 and sample[factor].nunique() >= 5:
                low = sample[factor].quantile(0.2)
                high = sample[factor].quantile(0.8)
                row[factor] = float(
                    sample.loc[sample[factor] >= high, "fwd_1d_return"].mean()
                    - sample.loc[sample[factor] <= low, "fwd_1d_return"].mean()
                )
            else:
                row[factor] = None
            row[f"{factor}_available_at"] = (
                f"{realized_date.date().isoformat()}T16:00:00+08:00"
            )
            row[f"{factor}_data_version"] = version
            row[f"{factor}_source_snapshot_sha256"] = source_sha
        daily_rows.append(row)
    factor_returns = pd.DataFrame(daily_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / "factor_panel.csv"
    returns_path = output_dir / "factor_returns.csv"
    panel.to_csv(panel_path, index=False)
    factor_returns.to_csv(returns_path, index=False)
    regression_factors = [
        factor
        for factor in factors
        if float(factor_returns[factor].notna().mean()) >= 0.50
        and factor_returns[factor].nunique(dropna=True) >= 2
    ]
    strategy_nav = pd.read_csv(nav_path)
    strategy_name = "production_governed_vol_position_v1_2b_dynamic_score"
    strategy_nav = strategy_nav[
        strategy_nav["strategy"].astype(str) == strategy_name
    ].copy()
    strategy_nav["trade_date"] = pd.to_datetime(
        strategy_nav["trade_date"], errors="coerce"
    )
    strategy_nav["strategy_return"] = pd.to_numeric(
        strategy_nav["nav"], errors="coerce"
    ).pct_change()
    regression = factor_returns[["trade_date", *regression_factors]].copy()
    regression["trade_date"] = pd.to_datetime(
        regression["trade_date"], errors="coerce"
    )
    regression = regression.merge(
        strategy_nav[["trade_date", "strategy_return"]],
        on="trade_date",
        how="inner",
    ).dropna()
    diagnostic: dict[str, Any] = {
        "schema_version": "alpha_v4_7_partial_attribution_v1",
        "status": "BLOCKED",
        "formal_evidence": False,
        "factors": regression_factors,
        "aligned_trading_days": int(len(regression)),
        "blockers": [
            "partial_factor_model",
            "history_below_252_days",
            "industry_and_regime_factors_missing",
            "formal_pit_missing",
        ],
    }
    if len(regression) > len(regression_factors) + 2:
        x = regression[regression_factors].to_numpy(dtype=float)
        y = regression["strategy_return"].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(x)), x])
        coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ coefficients
        residual_ss = float((residual**2).sum())
        total_ss = float(((y - y.mean()) ** 2).sum())
        dof = len(y) - len(regression_factors) - 1
        alpha_se = None
        alpha_tstat = None
        if dof > 0:
            covariance = np.linalg.pinv(design.T @ design)
            alpha_se = float(
                np.sqrt(max(residual_ss / dof * covariance[0, 0], 0.0))
            )
            if alpha_se > 0:
                alpha_tstat = float(coefficients[0] / alpha_se)
        diagnostic.update(
            {
                "regression_rank": int(rank),
                "alpha_daily": float(coefficients[0]),
                "alpha_annualized": float(
                    (1.0 + float(coefficients[0])) ** 252 - 1.0
                ),
                "alpha_tstat": alpha_tstat,
                "r_squared": (
                    float(1.0 - residual_ss / total_ss)
                    if total_ss > 0
                    else 0.0
                ),
                "unexplained_variance_ratio": (
                    float(residual_ss / total_ss) if total_ss > 0 else 1.0
                ),
                "factor_exposures": {
                    factor: float(coefficients[index + 1])
                    for index, factor in enumerate(regression_factors)
                },
            }
        )
    attribution_path = output_dir / "partial_attribution_report.json"
    attribution_path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    coverage = {
        factor: float(panel[factor].notna().mean()) for factor in factors
    }
    forward_coverage = {
        str(horizon): float(panel[f"fwd_{horizon}d_return"].notna().mean())
        for horizon in horizons
    }
    blockers = [
        "formal_history_below_252_dates",
        "industry_classification_missing",
        "market_regime_not_cross_sectional_for_ic",
        "formal_pit_manifest_missing",
    ]
    if duplicate_count:
        blockers.append(
            f"source_duplicate_symbol_dates_deduplicated:{duplicate_count}"
        )
    manifest = {
        "schema_version": "alpha_v4_7_factor_evidence_builder_v1",
        "status": "PARTIAL",
        "evidence_level": "E2",
        "source_path": str(source_path),
        "source_sha256": source_sha,
        "data_version": version,
        "sample_start": panel["trade_date"].min(),
        "sample_end": panel["trade_date"].max(),
        "unique_dates": int(panel["trade_date"].nunique()),
        "source_unique_dates": int(frame["trade_date"].nunique()),
        "symbols": int(panel["symbol"].nunique()),
        "rows": int(len(panel)),
        "factor_coverage": coverage,
        "forward_return_coverage": forward_coverage,
        "panel_path": str(panel_path),
        "panel_sha256": _file_sha(panel_path),
        "factor_returns_path": str(returns_path),
        "factor_returns_sha256": _file_sha(returns_path),
        "partial_attribution_path": str(attribution_path),
        "partial_attribution_sha256": _file_sha(attribution_path),
        "partial_attribution": diagnostic,
        "blockers": blockers,
        "capital_authority": False,
        "automatic_promotion_allowed": False,
    }
    manifest["content_sha256"] = canonical_sha(
        {key: value for key, value in manifest.items() if key != "content_sha256"}
    )
    manifest_path = output_dir / "factor_evidence_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    source_manifest = {
        "schema_version": "alpha_v3_5_factor_source_manifest_v1",
        "status": "BLOCKED",
        "factors": {
            factor: {
                "data_version": version,
                "source_snapshot_path": str(source_path),
                "source_snapshot_sha256": source_sha,
            }
            for factor in factors
        },
        "blockers": blockers,
    }
    (output_dir / "factor_source_manifest.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS)
    parser.add_argument("--nav", type=Path, default=DEFAULT_NAV)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="formal_v5_0")
    args = parser.parse_args()
    result = build_partial_factor_evidence(
        args.source,
        args.positions,
        args.nav,
        args.benchmark,
        args.output_dir,
        profile_name=args.profile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
