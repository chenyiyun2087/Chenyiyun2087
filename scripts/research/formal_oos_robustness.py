"""Formal OOS, statistical-robustness and concentration evaluation.

The evaluator consumes only a VERIFIED immutable formal run and an analysis
package bound to that run.  Missing folds, baselines, factors or trades block
the analysis instead of producing partial green gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import yaml

from scripts.research.statistical_robustness import compute_cpcv_pbo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = PROJECT_ROOT / "config" / "production_acceptance.yaml"
FACTORS = (
    "market_beta",
    "size",
    "volatility",
    "liquidity",
    "industry",
    "momentum",
    "value",
)
REQUIRED_PACKAGE_FILES = (
    "folds.json",
    "oos_returns.csv",
    "configuration_returns.csv",
    "closed_trades.csv",
)


@dataclass(frozen=True)
class Fold:
    fold_id: str
    train_start: str
    train_end: str
    purge_start: str
    purge_end: str
    validation_start: str
    validation_end: str
    embargo_start: str
    embargo_end: str
    test_start: str
    test_end: str
    model_config_sha: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _acceptance() -> dict[str, Any]:
    return (
        yaml.safe_load(ACCEPTANCE_PATH.read_text(encoding="utf-8")) or {}
    )["acceptance"]


def _next(open_dates: list[pd.Timestamp], after: pd.Timestamp, count: int) -> pd.Timestamp:
    eligible = [value for value in open_dates if value > after]
    if len(eligible) < count:
        raise ValueError("calendar_exhausted")
    return eligible[count - 1]


def _previous(
    open_dates: list[pd.Timestamp], at_or_before: pd.Timestamp, count: int
) -> pd.Timestamp:
    eligible = [value for value in open_dates if value <= at_or_before]
    if len(eligible) < count:
        raise ValueError("calendar_history_insufficient")
    return eligible[-count]


def generate_formal_folds(
    calendar_dates: list[str],
    *,
    start_date: str,
    end_date: str,
) -> list[Fold]:
    """Generate full-history 12/3/3 folds stepped every three months."""
    config = _acceptance()["rolling_oos"]
    open_dates = sorted(pd.Timestamp(value).normalize() for value in calendar_dates)
    cursor = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    folds: list[Fold] = []
    index = 0
    while True:
        nominal_train_end = cursor + pd.DateOffset(months=int(config["train_months"]))
        try:
            purge_end = _previous(open_dates, nominal_train_end, 1)
            train_end = _previous(
                open_dates,
                purge_end,
                int(config["purge_trading_days"]) + 1,
            )
            validation_start = _next(
                open_dates,
                purge_end,
                int(config["embargo_trading_days"]) + 1,
            )
            embargo_start = _next(open_dates, purge_end, 1)
            embargo_end = _next(
                open_dates, purge_end, int(config["embargo_trading_days"])
            )
        except ValueError:
            break
        validation_nominal_end = validation_start + pd.DateOffset(
            months=int(config["validation_months"])
        )
        try:
            validation_end = _previous(open_dates, validation_nominal_end, 1)
            test_start = _next(open_dates, validation_end, 1)
            test_nominal_end = test_start + pd.DateOffset(
                months=int(config["test_months"])
            )
            test_end = _previous(open_dates, test_nominal_end, 1)
        except ValueError:
            break
        if test_end > end:
            break
        identity = {
            "fold_id": f"WF{index:03d}",
            "train_start": cursor.date().isoformat(),
            "train_end": train_end.date().isoformat(),
            "purge_start": _next(open_dates, train_end, 1).date().isoformat(),
            "purge_end": purge_end.date().isoformat(),
            "validation_start": validation_start.date().isoformat(),
            "validation_end": validation_end.date().isoformat(),
            "embargo_start": embargo_start.date().isoformat(),
            "embargo_end": embargo_end.date().isoformat(),
            "test_start": test_start.date().isoformat(),
            "test_end": test_end.date().isoformat(),
        }
        identity["model_config_sha"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        folds.append(Fold(**identity))
        index += 1
        cursor += pd.DateOffset(months=int(config["step_months"]))
    if not folds:
        raise ValueError("no_complete_formal_oos_folds")
    return folds


def _annualized(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    compounded = float((1.0 + values).prod())
    return compounded ** (252.0 / len(values)) - 1.0 if compounded > 0 else -1.0


def _max_drawdown(values: pd.Series) -> float:
    nav = (1.0 + values).cumprod()
    return float((nav / nav.cummax() - 1.0).min()) if len(nav) else 0.0


def _deflated_sharpe_confidence(values: pd.Series, trials: int) -> float:
    """Bailey-style finite-sample DSR confidence approximation."""
    sample = values.astype(float).to_numpy()
    if len(sample) < 30 or np.std(sample, ddof=1) == 0:
        return 0.0
    sr = float(np.mean(sample) / np.std(sample, ddof=1) * math.sqrt(252))
    skew = float(pd.Series(sample).skew())
    kurtosis = float(pd.Series(sample).kurt() + 3.0)
    expected_max = NormalDist().inv_cdf(1.0 - 1.0 / max(trials, 2))
    denominator = math.sqrt(
        max(1e-12, 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr)
    )
    statistic = (sr - expected_max) * math.sqrt(len(sample) - 1) / denominator
    return float(NormalDist().cdf(statistic))


def _block_bootstrap_annualized(
    values: pd.Series,
    *,
    simulations: int = 500,
    block_size: int = 20,
) -> list[float]:
    data = values.astype(float).to_numpy()
    if len(data) < block_size:
        raise ValueError("bootstrap_sample_too_short")
    rng = np.random.default_rng(42)
    outputs: list[float] = []
    for _ in range(simulations):
        sampled: list[float] = []
        while len(sampled) < len(data):
            start = int(rng.integers(0, len(data) - block_size + 1))
            sampled.extend(data[start : start + block_size].tolist())
        outputs.append(_annualized(pd.Series(sampled[: len(data)])))
    return sorted(outputs)


def _multi_factor_attribution(
    returns: pd.DataFrame,
) -> dict[str, Any]:
    y = returns["strategy_return"].astype(float).to_numpy()
    x = returns[list(FACTORS)].astype(float).to_numpy()
    if len(y) <= len(FACTORS) + 2 or not np.isfinite(x).all():
        raise ValueError("factor_attribution_input_invalid")
    means = x.mean(axis=0)
    scales = x.std(axis=0, ddof=1)
    if np.any(scales <= 0):
        raise ValueError("factor_attribution_zero_variance")
    standardized = (x - means) / scales
    standardized_coefficients, _, _, _ = np.linalg.lstsq(
        standardized, y - y.mean(), rcond=None
    )
    exposures = standardized_coefficients / scales
    intercept = float(y.mean() - means @ exposures)
    # Elementwise form avoids a macOS Accelerate false-positive floating-point
    # warning observed for a narrow 7-column matrix multiplication.
    fitted = intercept + (x * exposures).sum(axis=1)
    residual = y - fitted
    total_ss = float(((y - y.mean()) ** 2).sum())
    residual_ss = float((residual**2).sum())
    r_squared = 1.0 - residual_ss / total_ss if total_ss > 0 else 0.0
    return {
        "alpha_daily": intercept,
        "alpha_annualized": float((1 + intercept) ** 252 - 1),
        "r_squared": r_squared,
        "factor_exposures": {
            factor: float(exposures[index])
            for index, factor in enumerate(FACTORS)
        },
    }


def _profit_share(grouped: pd.Series) -> float:
    positive = grouped[grouped.gt(0)]
    total = float(positive.sum())
    return float(positive.max() / total) if total > 0 and len(positive) else 1.0


def _concentration(trades: pd.DataFrame, oos: pd.DataFrame) -> dict[str, Any]:
    frame = trades.copy()
    frame["exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce")
    frame["net_pnl"] = pd.to_numeric(frame["net_pnl"], errors="coerce")
    if frame[["exit_date", "net_pnl", "symbol", "industry"]].isna().any().any():
        raise ValueError("closed_trade_attribution_input_invalid")
    stock = frame.groupby("symbol")["net_pnl"].sum()
    industry = frame.groupby("industry")["net_pnl"].sum()
    month = frame.groupby(frame["exit_date"].dt.to_period("M"))["net_pnl"].sum()
    year = frame.groupby(frame["exit_date"].dt.year)["net_pnl"].sum()
    window_returns = oos.groupby("fold_id")["strategy_return"].apply(
        lambda values: float((1.0 + values).prod() - 1.0)
    )
    top_five = float(stock[stock.gt(0)].nlargest(5).sum())
    return {
        "max_single_stock_profit_share": _profit_share(stock),
        "max_single_industry_profit_share": _profit_share(industry),
        "max_single_month_profit_share": _profit_share(month),
        "max_single_year_profit_share": _profit_share(year),
        "max_single_oos_window_profit_share": _profit_share(window_returns),
        "top_five_winner_profit_cny": top_five,
        "profit_after_removing_top_five_cny": float(frame["net_pnl"].sum() - top_five),
    }


def _blocked(
    formal_manifest: Path, analysis_package: Path, blockers: list[str]
) -> dict[str, Any]:
    payload = {
        "schema_version": "formal_oos_robustness_v1",
        "status": "BLOCKED",
        "technical_evidence_complete": False,
        "economic_gates_passed": False,
        "formal_manifest": str(formal_manifest),
        "analysis_package": str(analysis_package),
        "blockers": sorted(set(blockers)),
    }
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload


def evaluate(formal_manifest: Path, analysis_package: Path) -> dict[str, Any]:
    if not formal_manifest.is_file():
        return _blocked(formal_manifest, analysis_package, ["formal_manifest_missing"])
    formal = json.loads(formal_manifest.read_text(encoding="utf-8"))
    if formal.get("status") != "VERIFIED":
        return _blocked(formal_manifest, analysis_package, ["formal_run_not_verified"])
    missing = [
        name for name in REQUIRED_PACKAGE_FILES if not (analysis_package / name).is_file()
    ]
    if missing:
        return _blocked(
            formal_manifest,
            analysis_package,
            [f"analysis_object_missing:{name}" for name in missing],
        )
    folds = [Fold(**item) for item in json.loads(
        (analysis_package / "folds.json").read_text(encoding="utf-8")
    )]
    if not folds:
        return _blocked(formal_manifest, analysis_package, ["oos_folds_missing"])
    returns = pd.read_csv(analysis_package / "oos_returns.csv")
    required_return_columns = {
        "fold_id",
        "trade_date",
        "phase",
        "strategy_return",
        "benchmark_return",
        "model_config_sha",
        "parameter_selected_at",
        *FACTORS,
    }
    absent = sorted(required_return_columns - set(returns.columns))
    if absent:
        return _blocked(
            formal_manifest,
            analysis_package,
            [f"oos_return_column_missing:{name}" for name in absent],
        )
    returns["trade_date"] = pd.to_datetime(returns["trade_date"], errors="coerce")
    returns["parameter_selected_at"] = pd.to_datetime(
        returns["parameter_selected_at"], errors="coerce"
    )
    fold_map = {fold.fold_id: fold for fold in folds}
    contamination: list[str] = []
    for fold_id, frame in returns.groupby("fold_id"):
        fold = fold_map.get(str(fold_id))
        if not fold:
            contamination.append(f"unknown_fold:{fold_id}")
            continue
        if not frame["phase"].astype(str).eq("TEST").all():
            contamination.append(f"non_test_rows:{fold_id}")
        if set(frame["model_config_sha"].astype(str)) != {fold.model_config_sha}:
            contamination.append(f"model_identity_mismatch:{fold_id}")
        if frame["parameter_selected_at"].max() > pd.Timestamp(fold.validation_end):
            contamination.append(f"test_window_tuning:{fold_id}")
        if frame["trade_date"].min() < pd.Timestamp(
            fold.test_start
        ) or frame["trade_date"].max() > pd.Timestamp(fold.test_end):
            contamination.append(f"test_window_date_mismatch:{fold_id}")
    if contamination or set(fold_map) != set(returns["fold_id"].astype(str)):
        if set(fold_map) != set(returns["fold_id"].astype(str)):
            contamination.append("fold_result_coverage_incomplete")
        return _blocked(formal_manifest, analysis_package, contamination)

    configuration = pd.read_csv(analysis_package / "configuration_returns.csv")
    required_config = {"trade_date", "config_id", "daily_return"}
    if required_config - set(configuration.columns):
        return _blocked(formal_manifest, analysis_package, ["comparison_baseline_missing"])
    pivot = configuration.pivot(
        index="trade_date", columns="config_id", values="daily_return"
    )
    if pivot.isna().any().any() or pivot.shape[1] < 2:
        return _blocked(
            formal_manifest, analysis_package, ["comparison_baseline_incomplete"]
        )
    oos = returns.sort_values("trade_date").copy()
    strategy_returns = pd.to_numeric(oos["strategy_return"], errors="coerce")
    if strategy_returns.isna().any():
        return _blocked(formal_manifest, analysis_package, ["oos_returns_invalid"])
    window_metrics: list[dict[str, Any]] = []
    for fold_id, frame in oos.groupby("fold_id", sort=True):
        values = frame["strategy_return"].astype(float)
        annualized = _annualized(values)
        drawdown = _max_drawdown(values)
        window_metrics.append(
            {
                "fold_id": fold_id,
                "total_return": float((1.0 + values).prod() - 1.0),
                "annualized_return": annualized,
                "max_drawdown": drawdown,
                "calmar": annualized / abs(drawdown) if drawdown < 0 else 0.0,
                "positive": float((1.0 + values).prod() - 1.0) > 0,
            }
        )
    positive_ratio = sum(item["positive"] for item in window_metrics) / len(
        window_metrics
    )
    oos_annualized = _annualized(strategy_returns)
    oos_drawdown = _max_drawdown(strategy_returns)
    oos_calmar = oos_annualized / abs(oos_drawdown) if oos_drawdown < 0 else 0.0
    dsr_confidence = _deflated_sharpe_confidence(
        strategy_returns, trials=pivot.shape[1]
    )
    pbo = compute_cpcv_pbo(
        [pivot[column].astype(float).tolist() for column in pivot.columns],
        purge=10,
        embargo=5,
    )
    bootstrap = _block_bootstrap_annualized(strategy_returns)
    bootstrap_fifth = float(np.quantile(bootstrap, 0.05))
    attribution = _multi_factor_attribution(oos)
    trades = pd.read_csv(
        analysis_package / "closed_trades.csv", dtype={"symbol": str}
    )
    concentration = _concentration(trades, oos)
    acceptance = _acceptance()
    thresholds = {
        **acceptance["rolling_oos"],
        **acceptance["statistical_robustness"],
    }
    gates = {
        "positive_oos_window_ratio": positive_ratio
        >= float(thresholds["min_positive_window_ratio"]),
        "oos_calmar": oos_calmar >= float(thresholds["min_oos_calmar"]),
        "max_single_window_profit_contribution": concentration[
            "max_single_oos_window_profit_share"
        ]
        <= float(thresholds["max_single_window_profit_contribution"]),
        "deflated_sharpe": dsr_confidence
        >= float(thresholds["min_deflated_sharpe_confidence"]),
        "cpcv_pbo": pbo
        <= float(thresholds["max_probability_of_backtest_overfitting"]),
        "block_bootstrap": bootstrap_fifth
        >= float(thresholds["bootstrap_annual_return_5th_percentile_min"]),
        "single_stock_concentration": concentration[
            "max_single_stock_profit_share"
        ]
        <= float(thresholds["max_single_stock_profit_contribution"]),
        "single_industry_concentration": concentration[
            "max_single_industry_profit_share"
        ]
        <= float(thresholds["max_single_industry_profit_contribution"]),
        "single_month_concentration": concentration[
            "max_single_month_profit_share"
        ]
        <= float(thresholds["max_single_month_profit_contribution"]),
        "single_year_concentration": concentration[
            "max_single_year_profit_share"
        ]
        <= float(acceptance["full_history"]["max_single_year_profit_contribution"]),
    }
    economic_passed = all(gates.values())
    payload: dict[str, Any] = {
        "schema_version": "formal_oos_robustness_v1",
        "status": "PASS" if economic_passed else "ECONOMIC_FAILED",
        "technical_evidence_complete": True,
        "economic_gates_passed": economic_passed,
        "formal_run_id": formal.get("formal_run_id"),
        "window_metrics": window_metrics,
        "positive_oos_window_ratio": positive_ratio,
        "oos_annualized_return": oos_annualized,
        "oos_max_drawdown": oos_drawdown,
        "oos_calmar": oos_calmar,
        "deflated_sharpe_confidence": dsr_confidence,
        "cpcv_pbo": pbo,
        "bootstrap_annual_return_5th": bootstrap_fifth,
        "factor_attribution": attribution,
        "concentration": concentration,
        "gates": gates,
        "blockers": [name for name, passed in gates.items() if not passed],
        "input_sha256": {
            name: _sha(analysis_package / name) for name in REQUIRED_PACKAGE_FILES
        },
    }
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-manifest", type=Path, required=True)
    parser.add_argument("--analysis-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.formal_manifest, args.analysis_package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
