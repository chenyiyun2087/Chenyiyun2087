#!/usr/bin/env python3
"""Evaluate factor challengers without granting production or capital authority.

The lab deliberately separates a short-sample research signal from qualified
economic Alpha.  It uses only dated factor values and subsequently observed
returns, and fails closed when PIT, industry, history, or source-key evidence
is incomplete.
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

from runtime.acceptance_config import canonical_sha, load_validation_profile


DEFAULT_FACTOR_DIR = (
    PROJECT_ROOT / "exports/evidence_production/20260731_alpha_v4_3/factors"
)
FACTOR_NAMES = ("size", "volatility", "liquidity", "momentum", "value")


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_float(value: Any) -> float | None:
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _max_drawdown(returns: pd.Series) -> float | None:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    if values.empty:
        return None
    nav = (1.0 + values).cumprod()
    return float((nav / nav.cummax() - 1.0).min())


def _annualized_metrics(returns: pd.Series) -> dict[str, float | int | None]:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    if values.empty:
        return {
            "observations": 0,
            "total_return": None,
            "annualized_return": None,
            "annualized_volatility": None,
            "sharpe_zero_rf": None,
            "max_drawdown": None,
            "positive_day_ratio": None,
        }
    total = float((1.0 + values).prod() - 1.0)
    annualized = (
        float((1.0 + total) ** (252.0 / len(values)) - 1.0)
        if total > -1.0
        else -1.0
    )
    volatility = float(values.std(ddof=1) * np.sqrt(252.0))
    sharpe = (
        float(values.mean() / values.std(ddof=1) * np.sqrt(252.0))
        if len(values) > 1 and values.std(ddof=1) > 0
        else None
    )
    return {
        "observations": int(len(values)),
        "total_return": total,
        "annualized_return": annualized,
        "annualized_volatility": volatility,
        "sharpe_zero_rf": sharpe,
        "max_drawdown": _max_drawdown(values),
        "positive_day_ratio": float((values > 0).mean()),
    }


def _rank_ic(group: pd.DataFrame, factor: str, return_column: str) -> float:
    sample = group[[factor, return_column]].dropna()
    if len(sample) < 20 or sample[factor].nunique() < 5:
        return np.nan
    return float(
        sample[factor].rank(method="average").corr(
            sample[return_column].rank(method="average")
        )
    )


def _membership_turnover(panel: pd.DataFrame, score_column: str) -> float | None:
    previous: set[str] | None = None
    observations: list[float] = []
    for _, group in panel.groupby("trade_date", sort=True):
        sample = group[["symbol", score_column]].dropna()
        if len(sample) < 20:
            continue
        threshold = sample[score_column].quantile(0.8)
        current = set(
            sample.loc[sample[score_column] >= threshold, "symbol"].astype(str)
        )
        if previous and current:
            # One-way constituent replacement ratio for an equal-weight basket.
            observations.append(1.0 - len(previous & current) / len(previous))
        previous = current
    return float(np.mean(observations)) if observations else None


def _daily_spread_series(
    panel: pd.DataFrame, score_column: str, return_column: str
) -> pd.Series:
    rows: dict[pd.Timestamp, float] = {}
    for date, group in panel.groupby("trade_date", sort=True):
        sample = group[[score_column, return_column]].dropna()
        if len(sample) < 20 or sample[score_column].nunique() < 5:
            continue
        low = sample[score_column].quantile(0.2)
        high = sample[score_column].quantile(0.8)
        rows[pd.Timestamp(date)] = float(
            sample.loc[sample[score_column] >= high, return_column].mean()
            - sample.loc[sample[score_column] <= low, return_column].mean()
        )
    return pd.Series(rows, dtype=float).sort_index()


def _regime_spreads(panel: pd.DataFrame, score_column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for regime, group in panel.groupby("market_regime", dropna=True, sort=True):
        spread = _daily_spread_series(group, score_column, "fwd_1d_return")
        rows.append(
            {
                "market_regime": float(regime),
                "dates": int(len(spread)),
                "mean_one_day_spread": _safe_float(spread.mean()),
                "positive_day_ratio": _safe_float((spread > 0).mean()),
            }
        )
    return rows


def _quantile_spreads(
    panel: pd.DataFrame,
    score_column: str,
    horizons: list[int],
) -> dict[str, Any]:
    decay: list[dict[str, Any]] = []
    for horizon in horizons:
        return_column = f"fwd_{horizon}d_return"
        daily: list[dict[str, float]] = []
        ic_values: list[float] = []
        for _, group in panel.groupby("trade_date", sort=True):
            sample = group[[score_column, return_column]].dropna()
            if len(sample) < 20 or sample[score_column].nunique() < 5:
                continue
            low = sample[score_column].quantile(0.2)
            high = sample[score_column].quantile(0.8)
            top = float(
                sample.loc[sample[score_column] >= high, return_column].mean()
            )
            bottom = float(
                sample.loc[sample[score_column] <= low, return_column].mean()
            )
            daily.append({"top": top, "bottom": bottom, "spread": top - bottom})
            ic_values.append(_rank_ic(group, score_column, return_column))
        spread = pd.Series([row["spread"] for row in daily], dtype=float)
        ic = pd.Series(ic_values, dtype=float).dropna()
        ic_std = float(ic.std(ddof=1)) if len(ic) > 1 else 0.0
        decay.append(
            {
                "horizon_days": horizon,
                "dates": int(len(daily)),
                "mean_top_return": _safe_float(
                    pd.Series([row["top"] for row in daily]).mean()
                ),
                "mean_bottom_return": _safe_float(
                    pd.Series([row["bottom"] for row in daily]).mean()
                ),
                "mean_spread_return": _safe_float(spread.mean()),
                "median_rank_ic": _safe_float(ic.median()),
                "mean_rank_ic": _safe_float(ic.mean()),
                "information_ratio": _safe_float(
                    ic.mean() / ic_std if ic_std > 0 else np.nan
                ),
                "positive_ic_ratio": _safe_float((ic > 0).mean()),
            }
        )
    return {
        "decay_curve": decay,
        "average_top_quantile_turnover": _membership_turnover(
            panel, score_column
        ),
    }


def build_factor_challenger_lab(
    factor_dir: Path,
    output_dir: Path,
    *,
    profile_name: str = "formal_v5_0",
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    panel_path = factor_dir / "factor_panel.csv"
    returns_path = factor_dir / "factor_returns.csv"
    manifest_path = factor_dir / "factor_evidence_manifest.json"
    panel = pd.read_csv(panel_path)
    factor_returns = pd.read_csv(returns_path)
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    panel["trade_date"] = pd.to_datetime(panel["trade_date"], errors="coerce")
    factor_returns["trade_date"] = pd.to_datetime(
        factor_returns["trade_date"], errors="coerce"
    )
    panel["low_volatility_value"] = (
        pd.to_numeric(panel["volatility"], errors="coerce")
        + pd.to_numeric(panel["value"], errors="coerce")
    ) / 2.0
    factor_returns["low_volatility_value"] = (
        pd.to_numeric(factor_returns["volatility"], errors="coerce")
        + pd.to_numeric(factor_returns["value"], errors="coerce")
    ) / 2.0
    horizons = [int(value) for value in profile["factor_ic"]["horizons"]]
    min_days = int(profile["economic_alpha_qualification"]["min_trading_days"])
    required_regimes = int(
        profile["economic_alpha_qualification"]["min_market_regimes"]
    )
    min_ir = float(
        profile["economic_alpha_qualification"]["min_information_ratio"]
    )
    candidates: dict[str, Any] = {}
    observed_regimes = int(
        panel.groupby("trade_date")["market_regime"].first().nunique(dropna=True)
    )
    for factor in (*FACTOR_NAMES, "low_volatility_value"):
        diagnostics = _quantile_spreads(panel, factor, horizons)
        decay = diagnostics["decay_curve"]
        positive_median = [
            row
            for row in decay
            if row["median_rank_ic"] is not None
            and row["median_rank_ic"] > 0
            and row["information_ratio"] is not None
            and row["information_ratio"] > min_ir
        ]
        short_horizon_support = [
            row for row in positive_median if row["horizon_days"] <= 20
        ]
        aligned_daily_returns = _daily_spread_series(
            panel, factor, "fwd_1d_return"
        )
        extended_daily_returns = factor_returns[factor]
        sample_days = int(panel.loc[panel[factor].notna(), "trade_date"].nunique())
        aligned_metrics = _annualized_metrics(aligned_daily_returns)
        if len(short_horizon_support) >= 2 and (
            aligned_metrics["total_return"] is not None
            and aligned_metrics["total_return"] > 0
        ):
            diagnostic_signal = "PROMISING_SHORT_SAMPLE"
        elif positive_median:
            diagnostic_signal = "HORIZON_ONLY_OR_CONFLICTING"
        else:
            diagnostic_signal = "NOT_SUPPORTED"
        candidates[factor] = {
            "sample_days": sample_days,
            "coverage": float(panel[factor].notna().mean()),
            **diagnostics,
            "aligned_one_day_long_short": aligned_metrics,
            "extended_one_day_long_short": _annualized_metrics(
                extended_daily_returns
            ),
            "extended_sample_warning": (
                "extended one-day metrics use a longer date window and are "
                "not directly comparable with the complete-case decay panel"
            ),
            "regime_spreads": _regime_spreads(panel, factor),
            "diagnostic_signal": diagnostic_signal,
            "economic_alpha_status": "BLOCKED",
            "qualification_checks": {
                "history_gte_minimum": sample_days >= min_days,
                "short_horizon_rank_ic_and_ir_support": (
                    len(short_horizon_support) >= 2
                ),
                "aligned_long_short_total_return_positive": bool(
                    aligned_metrics["total_return"] is not None
                    and aligned_metrics["total_return"] > 0
                ),
                "three_market_regimes": observed_regimes >= required_regimes,
                "formal_pit": False,
                "industry_neutralized": False,
            },
        }
    blockers = list(source_manifest.get("blockers") or [])
    blockers.extend(
        [
            f"challenger_history_below_{min_days}_days",
            f"market_regime_coverage_below_{required_regimes}",
            "industry_neutral_challenger_missing",
            "formal_pit_challenger_manifest_missing",
        ]
    )
    blockers = sorted(set(blockers))
    report: dict[str, Any] = {
        "schema_version": "alpha_v4_4_factor_challenger_lab_v1",
        "profile": profile_name,
        "status": "BLOCKED",
        "formal_evidence": False,
        "evidence_level": "E2",
        "claim": "research_factor_diagnostics_only",
        "source": {
            "factor_panel_path": str(panel_path),
            "factor_panel_sha256": _file_sha(panel_path),
            "factor_returns_path": str(returns_path),
            "factor_returns_sha256": _file_sha(returns_path),
            "factor_manifest_path": str(manifest_path),
            "factor_manifest_sha256": _file_sha(manifest_path),
        },
        "economic_alpha_qualification": {
            "min_trading_days": min_days,
            "target_trading_days": int(
                profile["economic_alpha_qualification"]["target_trading_days"]
            ),
            "min_market_regimes": required_regimes,
            "observed_market_regimes": observed_regimes,
            "min_information_ratio": min_ir,
            "all_required_checks_pass": False,
        },
        "candidates": candidates,
        "blockers": blockers,
        "research_status": "PROMISING_DIAGNOSTICS",
        "trading_status": "BLOCKED",
        "capital_status": "NO_SCALE",
        "capital_authority": False,
        "broker_permission": False,
        "allowed_incremental_capital_cny": 0,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    for factor, payload in candidates.items():
        metrics = payload["aligned_one_day_long_short"]
        summary_rows.append(
            {
                "factor": factor,
                "sample_days": payload["sample_days"],
                "coverage": payload["coverage"],
                "diagnostic_signal": payload["diagnostic_signal"],
                "economic_alpha_status": payload["economic_alpha_status"],
                "aligned_one_day_total_return": metrics["total_return"],
                "aligned_one_day_annualized_return": metrics["annualized_return"],
                "aligned_one_day_sharpe_zero_rf": metrics["sharpe_zero_rf"],
                "aligned_one_day_max_drawdown": metrics["max_drawdown"],
                "average_top_quantile_turnover": payload[
                    "average_top_quantile_turnover"
                ],
            }
        )
    summary_path = output_dir / "factor_challenger_summary.csv"
    pd.DataFrame(summary_rows).to_csv(summary_path, index=False)
    report["summary_path"] = str(summary_path)
    report["summary_sha256"] = _file_sha(summary_path)
    report["content_sha256"] = canonical_sha(
        {
            key: value
            for key, value in report.items()
            if key not in {"content_sha256", "summary_path"}
        }
    )
    report_path = output_dir / "factor_challenger_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factor-dir", type=Path, default=DEFAULT_FACTOR_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", default="formal_v5_0")
    args = parser.parse_args()
    report = build_factor_challenger_lab(
        args.factor_dir, args.output_dir, profile_name=args.profile
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
