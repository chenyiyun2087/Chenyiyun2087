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
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.statistical_robustness import compute_cpcv_pbo


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
    "analysis_manifest.json",
    "selected_model_config.json",
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


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            # P0-9 fix: embargo between Validation and Test (at least max_hold_days trading days)
            max_hold = int(config.get("max_hold_days", 10))
            test_embargo = max(int(config.get("embargo_trading_days", 5)), max_hold)
            test_start = _next(open_dates, validation_end, test_embargo + 1)
            test_nominal_end = test_start + pd.DateOffset(
                months=int(config["test_months"])
            )
            test_end = _previous(open_dates, test_nominal_end, 1)
        except ValueError:
            break
        if test_end > end:
            break
        # P0-3: Fold is purely a date window. model_config_sha lives in selected_model_config.json
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
            "model_config_sha": "",  # populated from selected_model_config.json per fold
        }
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


def _block_bootstrap_max_drawdown(
    values: pd.Series,
    *,
    simulations: int = 500,
    block_size: int = 20,
) -> list[float]:
    """P0-11: Block-bootstrap the max-drawdown distribution."""
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
        outputs.append(_max_drawdown(pd.Series(sampled[: len(data)])))
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
    # PR-H1: Reject fake manifests — must have full SHA chain, not just status=VERIFIED
    if formal.get("status") != "VERIFIED":
        return _blocked(formal_manifest, analysis_package, ["formal_run_not_verified"])
    if not formal.get("manifest_sha256"):
        return _blocked(formal_manifest, analysis_package, ["formal_manifest_incomplete_no_self_sha"])
    if not formal.get("frozen_bundle_sha256"):
        return _blocked(formal_manifest, analysis_package, ["formal_manifest_incomplete_no_frozen_bundle"])
    # Verify manifest integrity: re-compute canonical SHA (excluding self-sha field)
    manifest_without_self = {k: v for k, v in formal.items() if k != "manifest_sha256"}
    computed_manifest_sha = hashlib.sha256(
        json.dumps(manifest_without_self, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if formal.get("manifest_sha256") != computed_manifest_sha:
        return _blocked(formal_manifest, analysis_package, ["formal_manifest_sha_mismatch"])
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

    # P0-1 fix: initialize contamination BEFORE selected_model_config validation
    contamination: list[str] = []

    # P0-8: Validate selected_model_config.json if present — binds actual model params per fold
    selected_config_path = analysis_package / "selected_model_config.json"
    selected_configs: dict[str, dict[str, Any]] = {}
    if selected_config_path.is_file():
        selected_raw = json.loads(selected_config_path.read_text(encoding="utf-8"))
        selected_folds = selected_raw.get("folds") or {}
        fold_map_temp = {f.fold_id: f for f in folds}
        for fid, fconfig in selected_folds.items():
            if fid not in fold_map_temp:
                return _blocked(
                    formal_manifest, analysis_package,
                    [f"selected_model_config_unknown_fold:{fid}"],
                )
            declared_sha = str(fconfig.get("model_config_sha256") or "")
            if not declared_sha or len(declared_sha) != 64:
                return _blocked(
                    formal_manifest, analysis_package,
                    [f"selected_model_config_invalid_sha:{fid}"],
                )
            # Recompute SHA from the config (excluding self-sha)
            config_without_sha = {k: v for k, v in fconfig.items() if k != "model_config_sha256"}
            computed = hashlib.sha256(
                json.dumps(config_without_sha, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if computed != declared_sha:
                return _blocked(
                    formal_manifest, analysis_package,
                    [f"selected_model_config_sha_mismatch:{fid}"],
                )
            selected_configs[fid] = fconfig

    # P0-A: selected_model_config must cover ALL folds — no missing, no extra
    fold_id_set = {f.fold_id for f in folds}
    selected_id_set = set(selected_configs.keys())
    if selected_id_set != fold_id_set:
        missing = sorted(fold_id_set - selected_id_set)
        extra = sorted(selected_id_set - fold_id_set)
        reasons = []
        if missing:
            reasons.append(f"selected_config_missing_folds:{','.join(missing)}")
        if extra:
            reasons.append(f"selected_config_extra_folds:{','.join(extra)}")
        return _blocked(formal_manifest, analysis_package, reasons)

    # P0-B: Model config schema — every fold must have required fields
    REQUIRED_MODEL_FIELDS = (
        "strategy_id", "factor_weights", "hold_days", "top_n",
        "cost_model", "code_git_sha", "config_sha", "selected_at",
    )
    for fid, fconfig in selected_configs.items():
        missing_fields = [f for f in REQUIRED_MODEL_FIELDS if f not in fconfig or not fconfig[f]]
        if missing_fields:
            return _blocked(
                formal_manifest, analysis_package,
                [f"selected_model_config_missing_fields:{fid}:{','.join(missing_fields)}"],
            )

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
    # P0-8: If selected_model_config present, validate per-row model_config_sha matches
    if selected_configs and "model_config_sha" in returns.columns:
        for fid, fconfig in selected_configs.items():
            expected_sha = str(fconfig.get("model_config_sha256") or "")
            row_shas = set(returns.loc[returns["fold_id"] == fid, "model_config_sha"].astype(str))
            if row_shas and row_shas != {expected_sha}:
                contamination.append(f"model_config_sha_row_mismatch:{fid}")

    returns["trade_date"] = pd.to_datetime(returns["trade_date"], errors="coerce")
    returns["parameter_selected_at"] = pd.to_datetime(
        returns["parameter_selected_at"], errors="coerce"
    )
    # P0-3: Populate fold model_config_sha from selected_model_config
    fold_map: dict[str, Fold] = {}
    for fold in folds:
        if fold.fold_id in selected_configs:
            fold = Fold(
                fold_id=fold.fold_id,
                train_start=fold.train_start, train_end=fold.train_end,
                purge_start=fold.purge_start, purge_end=fold.purge_end,
                validation_start=fold.validation_start, validation_end=fold.validation_end,
                embargo_start=fold.embargo_start, embargo_end=fold.embargo_end,
                test_start=fold.test_start, test_end=fold.test_end,
                model_config_sha=selected_configs[fold.fold_id].get("model_config_sha256", ""),
            )
        fold_map[fold.fold_id] = fold
    folds = list(fold_map.values())
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
    # P0-7: Validate analysis_manifest.json binds to formal run
    analysis_manifest_path = analysis_package / "analysis_manifest.json"
    analysis_manifest = json.loads(analysis_manifest_path.read_text(encoding="utf-8"))

    # P0-C: ALL key fields mandatory — any missing → BLOCKED
    manifest_blockers: list[str] = []
    if analysis_manifest.get("formal_run_id") != formal.get("formal_run_id"):
        manifest_blockers.append("analysis_manifest_formal_run_id_mismatch")
    if not analysis_manifest.get("formal_manifest_sha256"):
        manifest_blockers.append("analysis_manifest_missing_formal_manifest_sha256")
    else:
        manifest_without_self_c = {k: v for k, v in formal.items() if k != "manifest_sha256"}
        expected_fm_sha = hashlib.sha256(
            json.dumps(manifest_without_self_c, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if analysis_manifest["formal_manifest_sha256"] != expected_fm_sha:
            manifest_blockers.append("analysis_manifest_formal_sha_mismatch")
    if not analysis_manifest.get("frozen_bundle_sha256"):
        manifest_blockers.append("analysis_manifest_missing_frozen_bundle_sha256")
    if not analysis_manifest.get("acceptance_config_sha256"):
        manifest_blockers.append("analysis_manifest_missing_acceptance_config_sha256")
    # Self-hash: analysis_manifest must have manifest_sha256 that validates
    am_self_sha = str(analysis_manifest.get("manifest_sha256") or "")
    if not am_self_sha:
        manifest_blockers.append("analysis_manifest_missing_self_sha")
    else:
        am_without = {k: v for k, v in analysis_manifest.items() if k != "manifest_sha256"}
        if _sha_text(json.dumps(am_without, sort_keys=True, separators=(",", ":"))) != am_self_sha:
            manifest_blockers.append("analysis_manifest_self_sha_mismatch")
    # Verify ALL required input files declared with SHA
    input_files = analysis_manifest.get("input_files") or {}
    for fname in REQUIRED_PACKAGE_FILES:
        if fname == "analysis_manifest.json":
            continue
        declared = (input_files.get(fname) or {}).get("sha256", "")
        if not declared:
            manifest_blockers.append(f"analysis_manifest_missing_file_sha:{fname}")
        else:
            actual = _sha(analysis_package / fname)
            if actual != declared:
                manifest_blockers.append(f"analysis_manifest_sha_mismatch:{fname}")
    if manifest_blockers:
        return _blocked(formal_manifest, analysis_package, manifest_blockers)

    if contamination or set(fold_map) != set(returns["fold_id"].astype(str)):
        if set(fold_map) != set(returns["fold_id"].astype(str)):
            contamination.append("fold_result_coverage_incomplete")
        return _blocked(formal_manifest, analysis_package, contamination)

    configuration = pd.read_csv(analysis_package / "configuration_returns.csv")
    configuration["trade_date"] = pd.to_datetime(configuration["trade_date"], errors="coerce")
    # P0-9 fix: require fold_id for correct per-fold alignment
    required_config = {"trade_date", "config_id", "daily_return", "fold_id"}
    if required_config - set(configuration.columns):
        return _blocked(formal_manifest, analysis_package, ["comparison_baseline_missing"])
    pivot = configuration.pivot_table(
        index=["fold_id", "trade_date"], columns="config_id", values="daily_return",
        aggfunc="first",
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
    # P0-11: Bootstrap max drawdown distribution and gate
    # P0-10 fix: 5th percentile = worst (most negative) drawdowns = tail risk
    bootstrap_dds = _block_bootstrap_max_drawdown(strategy_returns)
    bootstrap_dd_5th = float(np.quantile(bootstrap_dds, 0.05)) if bootstrap_dds else -1.0
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
        "oos_max_drawdown": oos_drawdown >= float(thresholds.get("max_oos_drawdown", -0.35)),
        "oos_window_coverage": len(folds) > 0 and set(fold_map) == set(returns["fold_id"].astype(str)),
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
        "bootstrap_max_dd": bootstrap_dd_5th
        >= float(thresholds.get("bootstrap_max_dd_95th_percentile_max", -0.40)),
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
    # P0-10 fix: Mandatory baseline config set — require exact named columns
    REQUIRED_BASELINE_CONFIGS = (
        "dynamic_champion",
        "production_baseline",
        "matched_random",
        "reverse_baseline",
    )
    missing_baselines = [c for c in REQUIRED_BASELINE_CONFIGS if c not in pivot.columns]
    if missing_baselines:
        return _blocked(formal_manifest, analysis_package,
                        [f"baseline_config_missing:{c}" for c in missing_baselines])

    champ_returns = pivot["dynamic_champion"].astype(float)
    oos_fold_returns = oos.groupby("fold_id")["strategy_return"].apply(
        lambda v: float((1.0 + v).prod() - 1.0)
    )

    # Champion vs production baseline calmar improvement
    base_returns = pivot["production_baseline"].astype(float)
    base_annual = _annualized(base_returns)
    base_dd = _max_drawdown(base_returns)
    base_calmar = base_annual / abs(base_dd) if base_dd < 0 else 0.0
    calmar_improvement = (oos_calmar - base_calmar) / abs(base_calmar) if base_calmar != 0 else 0.0
    gates["challenger_calmar_improvement"] = (
        calmar_improvement >= float(thresholds.get("challenger_min_calmar_improvement_pct", 10)) / 100.0
    )

    # P0-D: Verify no duplicate (fold_id, trade_date) in pivot
    if pivot.index.duplicated().any():
        dup_count = int(pivot.index.duplicated().sum())
        return _blocked(formal_manifest, analysis_package,
                        [f"baseline_duplicate_fold_date_rows:{dup_count}"])

    # P0-D: Per-fold date alignment — OOS dates must exactly equal baseline dates
    oos_indexed = oos.set_index(["fold_id", "trade_date"])
    for fold_id in oos_fold_returns.index:
        try:
            oos_fold_dates = set(
                pd.to_datetime(oos_indexed.xs(fold_id, level="fold_id").index).strftime("%Y-%m-%d")
            )
            base_fold_dates = set(
                pd.to_datetime(pivot.xs(fold_id, level="fold_id").index).strftime("%Y-%m-%d")
            )
            if oos_fold_dates != base_fold_dates:
                missing_o = sorted(oos_fold_dates - base_fold_dates)[:5]
                missing_b = sorted(base_fold_dates - oos_fold_dates)[:5]
                return _blocked(formal_manifest, analysis_package,
                                [f"baseline_date_mismatch:{fold_id}:oos_only={len(missing_o)}:base_only={len(missing_b)}"])
        except KeyError:
            return _blocked(formal_manifest, analysis_package,
                            [f"baseline_fold_missing:{fold_id}"])

    # P0-D: Champion consistency — dynamic_champion must equal oos strategy_return
    for fold_id in oos_fold_returns.index:
        try:
            oos_fold = oos_indexed.xs(fold_id, level="fold_id")["strategy_return"].astype(float)
            champ_fold = pivot.xs(fold_id, level="fold_id")["dynamic_champion"].astype(float)
            if not np.allclose(oos_fold.values, champ_fold.values, rtol=1e-10, atol=1e-10):
                max_diff = float((oos_fold - champ_fold).abs().max())
                return _blocked(formal_manifest, analysis_package,
                                [f"champion_return_divergence:{fold_id}:max_diff={max_diff:.2e}"])
        except KeyError:
            return _blocked(formal_manifest, analysis_package,
                            [f"champion_consistency_fold_missing:{fold_id}"])

    # Random baseline win ratio (per OOS fold)
    random_fold_wins = 0
    for fold_id in oos_fold_returns.index:
        try:
            fold_pivot = pivot.xs(fold_id, level="fold_id")
            fold_oos = oos_indexed.xs(fold_id, level="fold_id")
            champ_cum = float((1.0 + fold_oos["strategy_return"].astype(float)).prod() - 1.0)
            rand_cum = float((1.0 + fold_pivot["matched_random"].astype(float)).prod() - 1.0)
            if champ_cum > rand_cum:
                random_fold_wins += 1
        except KeyError:
            pass
    random_win_ratio = random_fold_wins / len(oos_fold_returns) if len(oos_fold_returns) > 0 else 0.0
    gates["random_baseline_win_ratio"] = (
        random_win_ratio >= float(thresholds.get("min_random_baseline_win_ratio", 0.70))
    )

    # Reverse baseline win ratio (per OOS fold)
    reverse_fold_wins = 0
    for fold_id in oos_fold_returns.index:
        try:
            fold_pivot = pivot.xs(fold_id, level="fold_id")
            fold_oos = oos_indexed.xs(fold_id, level="fold_id")
            champ_cum = float((1.0 + fold_oos["strategy_return"].astype(float)).prod() - 1.0)
            rev_cum = float((1.0 + fold_pivot["reverse_baseline"].astype(float)).prod() - 1.0)
            if champ_cum > rev_cum:
                reverse_fold_wins += 1
        except KeyError:
            pass
    reverse_win_ratio = reverse_fold_wins / len(oos_fold_returns) if len(oos_fold_returns) > 0 else 0.0
    gates["reverse_baseline_win_ratio"] = (
        reverse_win_ratio >= float(thresholds.get("min_reverse_baseline_win_ratio", 0.70))
    )
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
